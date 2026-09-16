"""
cv-service v4: webcam → YOLO → ByteTrack → ReID → Namer → BehaviorAnalyzer
   → Counter → SQLite → MJPEG anotado + WS eventos + Sensors simulado

v4 (a partir de v3-painel-controle):
  - behavior/analyzer.py classifica cada track em normal/ativa/repouso/anomala
    usando 5 regras: velocidade, static_seconds, aspect_ratio, isolamento, erratic
  - sensors/environment.py gera temp/umidade simulados (oscilação senoidal)
  - bbox desenhada agora colorida pelo estado (verde/laranja/cinza/vermelho)
  - trilha colorida pelo estado da galinha
  - novos endpoints:
      GET    /api/analytics          métricas (total/normal/ativa/repouso/anomala)
      GET    /api/sensors            temperatura + umidade (atual)
      GET    /api/timeline?hours=N   série temporal de contagens
      GET    /api/state              agora inclui sensor + analytics
  - SQLite: nova tabela timeline_snapshots (1 min, persiste 24h)
"""
from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import cv2
import numpy as np
import supervision as sv
from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from behavior.analyzer import BehaviorAnalyzer
from behavior.states import STATE_COLOR_BGR, State
from classifier import EmbeddingClassifier
from config import settings
from counter import Counter
from detector import Detector
from labels import LabelsStore
from namer import TrackNamer
from panel.db import Database
from panel.repo import PanelRepo
from reid import ReIDEncoder
from sensors.environment import SimulatedEnvironmentSensor
from storage import Storage
from tracker import Tracker
from tracking.heatmap import Heatmap
from tracking.palette_heatmap import PaletteHeatmap, _Roi as PaletteRoi
from tracking.path_tracker import PathTracker


class AppState:
    cap: cv2.VideoCapture | None = None
    detector: Detector | None = None
    tracker: Tracker | None = None
    reid: ReIDEncoder | None = None
    labels: LabelsStore | None = None
    classifier: EmbeddingClassifier | None = None
    namer: TrackNamer | None = None
    counter: Counter | None = None
    storage: Storage | None = None
    db: Database | None = None
    repo: PanelRepo | None = None
    heatmap: Heatmap | None = None
    paths: PathTracker | None = None
    palette_heatmap: PaletteHeatmap | None = None
    behavior: BehaviorAnalyzer | None = None
    sensor: SimulatedEnvironmentSensor | None = None
    running: bool = False
    # contagem acumulada entre loops do vídeo (persiste enquanto o serviço roda)
    lifetime_contagens: dict = {}
    loop_count: int = 0
    _prev_pos_frames: float = 0.0
    # path/identificador da fonte atualmente em uso (atualizado em _swap_source_to)
    current_source_path: str = ""
    latest_jpeg: bytes = b""
    latest_contagens: dict = {}
    latest_analytics: dict = {}
    latest_states: dict = {}  # track_id -> state
    frame_h: int = 720
    frame_w: int = 1280
    ws_clients: set[WebSocket] = set()
    pending_namer_loop: asyncio.AbstractEventLoop | None = None
    # timeline in-memory das últimas N leituras (1 por minuto)
    timeline_history: deque = deque(maxlen=24 * 60)  # 24h em minutos
    last_timeline_ts: float = 0.0


state = AppState()

# lock que serializa leituras do `state.cap` em relação a trocas de fonte
cap_lock = asyncio.Lock()


def _crop_to_b64(frame: np.ndarray, bbox: tuple, size: int = 160) -> str:
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1 = max(0, min(int(x1), w - 1))
    y1 = max(0, min(int(y1), h - 1))
    x2 = max(0, min(int(x2), w))
    y2 = max(0, min(int(y2), h))
    if x2 <= x1 or y2 <= y1:
        return ""
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return ""
    crop_resized = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop_resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _on_new_item(
    track_id: int, name: str, sim: float, frame: np.ndarray, bbox: tuple, embedding
) -> None:
    crop_b64 = _crop_to_b64(frame, bbox)
    payload: dict[str, Any] = {
        "type": "novo_item",
        "track_id": track_id,
        "name": name,
        "sim": round(sim, 3),
        "crop": crop_b64,
    }
    if state.repo is not None and embedding is not None:
        try:
            state.repo.upsert_label(name, [embedding], fixed=False)
        except Exception as e:
            print(f"[db] upsert_label falhou: {e}")

    async def _broadcast():
        for ws in list(state.ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                state.ws_clients.discard(ws)

    loop = state.pending_namer_loop
    if loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(), loop)
    except Exception:
        pass


async def _process_loop() -> None:
    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator()

    while state.running:
        if state.cap is None or not state.cap.isOpened():
            await asyncio.sleep(0.1)
            continue

        async with cap_lock:
            ok, frame = await asyncio.to_thread(state.cap.read)
            if not ok or frame is None:
                # vídeo chegou ao fim — se loop ligado, volta ao início
                if settings.video_path and settings.video_loop:
                    try:
                        # === detecta/soma reset de loop ===
                        # salva contagens do loop atual no acumulado
                        if state.counter is not None:
                            for cls, st in state.counter.estado().items():
                                acc = state.lifetime_contagens.setdefault(
                                    cls, {"in": 0, "out": 0, "current": 0}
                                )
                                acc["in"] += st["in"]
                                acc["out"] += st["out"]
                                acc["current"] += st["current"]
                            state.counter.reset()
                        if state.behavior is not None:
                            state.behavior = BehaviorAnalyzer()  # zera histórico de estados
                        if state.heatmap is not None:
                            state.heatmap.reset()  # zera heatmap acumulado
                        if state.palette_heatmap is not None:
                            state.palette_heatmap.reset()  # zera cold wave
                        if state.paths is not None:
                            state.paths = PathTracker(
                                max_points_per_track=settings.path_max_points,
                                max_age_seconds=settings.path_max_age,
                            )
                        state.loop_count += 1
                        state._frame_counter = 0
                        state._prev_pos_frames = 0.0
                        print(
                            f"[loop #{state.loop_count}] reset OK — "
                            f"lifetime_contagens={ {k: dict(v) for k, v in state.lifetime_contagens.items()} }"
                        )

                        state.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = await asyncio.to_thread(state.cap.read)
                    except Exception as e:
                        print(f"[loop] erro no reset: {e}")
                        ok = False
                if not ok or frame is None:
                    await asyncio.sleep(0.05)
                    continue

        h, w = frame.shape[:2]
        if (w, h) != (state.frame_w, state.frame_h):
            state.frame_w, state.frame_h = w, h
            state.counter.configure_frame(h, w)
            if state.heatmap is not None:
                state.heatmap = Heatmap(
                    (h, w),
                    kernel_radius=settings.heatmap_radius,
                    decay=settings.heatmap_decay,
                )

        detections = state.detector.detect(frame)
        tracks = state.tracker.update(detections)
        tracks = state.namer.resolve(tracks, frame)

        # ====== v4: Behavior analysis por track ======
        # Coleta centros pra calcular isolamento
        centers: list[tuple[int, float, float]] = []
        if tracks.tracker_id is not None:
            for i in range(len(tracks)):
                tid = int(tracks.tracker_id[i]) if tracks.tracker_id[i] is not None else None
                if tid is None:
                    continue
                x1, y1, x2, y2 = tracks.xyxy[i]
                centers.append((tid, (x1 + x2) / 2.0, (y1 + y2) / 2.0))

        # Classifica cada track
        behaviors_now: dict[int, dict] = {}
        other_centers_per_track: dict[int, list[tuple[float, float]]] = {}
        for tid, cx, cy in centers:
            others = [(ox, oy) for ot, ox, oy in centers if ot != tid]
            other_centers_per_track[tid] = others

        if tracks.tracker_id is not None:
            for i in range(len(tracks)):
                tid = int(tracks.tracker_id[i]) if tracks.tracker_id[i] is not None else None
                if tid is None:
                    continue
                bbox = tuple(tracks.xyxy[i].tolist())
                beh = state.behavior.update(tid, state.counter.cls_name(tracks, i), bbox)
                # reclassifica com centros
                beh = state.behavior.classify(
                    tid,
                    state.counter.cls_name(tracks, i),
                    other_centers=other_centers_per_track.get(tid, []),
                )
                behaviors_now[tid] = {
                    "state": beh.state,
                    "reason": beh.reason,
                    "speed_px_s": round(beh.speed_px_s, 1),
                    "static_seconds": round(beh.static_seconds, 1),
                    "isolation_px": round(beh.isolation_px, 1),
                    "is_lying": beh.is_lying,
                }
        state.latest_states = behaviors_now

        # ===== Métricas agregadas =====
        by_state = defaultdict(int)
        for d in behaviors_now.values():
            by_state[d["state"]] += 1
        # total = tracks ativas agora (incluindo as que existem)
        # mas precisamos também do "repouso" mesmo que track_id parou de update
        total_active = len(behaviors_now)
        # soma de aves únicas (cada track_id novo conta 1) em todas as classes
        unique_total = sum(st.get("unique", 0) for st in state.counter.estado().values()) if state.counter else 0
        analytics = {
            "total": total_active,
            "unique_total": unique_total,
            "normal": by_state.get(State.NORMAL, 0),
            "ativa": by_state.get(State.ATIVA, 0),
            "repouso": by_state.get(State.REPOUSO, 0),
            "anomalo": by_state.get(State.ANOMALA, 0),
            "pct_ativa": round(100 * by_state.get(State.ATIVA, 0) / max(1, total_active), 1),
            "pct_repouso": round(100 * by_state.get(State.REPOUSO, 0) / max(1, total_active), 1),
            "pct_anomalo": round(100 * by_state.get(State.ANOMALA, 0) / max(1, total_active), 1),
        }
        state.latest_analytics = analytics

        # ===== Heatmap + Paths (v3) =====
        if state.heatmap is not None and settings.heatmap_enabled:
            try:
                for i in range(len(tracks)):
                    if tracks.tracker_id is None or tracks.tracker_id[i] is None:
                        continue
                    cls_name = state.counter.cls_name(tracks, i)
                    x1, y1, x2, y2 = tracks.xyxy[i]
                    cx = int((x1 + x2) / 2)
                    cy = int((y1 + y2) / 2)
                    state.heatmap.add(cls_name, (cx, cy))
            except Exception as e:
                print(f"[heatmap] erro: {e}")

        if state.paths is not None and settings.paths_enabled:
            track_colors: dict[int, tuple] = {}
            for i in range(len(tracks)):
                if tracks.tracker_id is None or tracks.tracker_id[i] is None:
                    continue
                tid = int(tracks.tracker_id[i])
                cls_name = state.counter.cls_name(tracks, i)
                x1, y1, x2, y2 = tracks.xyxy[i]
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                state.paths.update(tid, cls_name, (cx, cy))
                track_colors[tid] = STATE_COLOR_BGR[behaviors_now.get(tid, {}).get("state", State.NORMAL)]
            state.paths.expire()
            # Atualiza cores das trilhas (override)
            state.paths_state_colors = track_colors

        eventos = state.counter.update(tracks)

        # ===== Palette heatmap DESATIVADO (alpha blending 2K causava travamento) =====
        palette_inside = 0
        # if state.palette_heatmap is not None:
        #     palette_inside = state.palette_heatmap.update(tracks)

        # ===== Contagem por track_id único: cada galinha nova no frame +1 =====
        novos_agora: list[tuple[int, str, float]] = []
        if tracks.tracker_id is not None and state.counter is not None:
            for i in range(len(tracks)):
                tid_v = tracks.tracker_id[i]
                if tid_v is None:
                    continue
                tid = int(tid_v)
                cls_name = state.counter.cls_name(tracks, i)
                if state.counter.register(tid, cls_name):
                    conf = float(tracks.confidence[i]) if tracks.confidence is not None else 0.0
                    novos_agora.append((tid, cls_name, conf))
        if novos_agora:
            for tid, cls_name, conf in novos_agora:
                payload = {
                    "type": "track_entered",
                    "track_id": tid,
                    "classe": cls_name,
                    "conf": round(conf, 3),
                }
                for ws in list(state.ws_clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        state.ws_clients.discard(ws)

        if eventos and state.storage:
            for ev in eventos:
                state.storage.registrar_evento(ev)
                payload = {
                    "type": "evento",
                    "ts": ev.ts,
                    "track_id": ev.track_id,
                    "classe": ev.cls_name,
                    "direcao": ev.direction,
                    "conf": round(ev.conf, 3),
                }
                for ws in list(state.ws_clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        state.ws_clients.discard(ws)

        # ===== Anota frame com bboxes coloridas por estado =====
        annotated = frame.copy()
        # Overlay azul (palette heatmap) — DESATIVADO pra evitar travamento
        # if state.palette_heatmap is not None:
        #     try:
        #         overlay = state.palette_heatmap.render_overlay()
        #         alpha = state.palette_heatmap.render_alpha()
        #         if overlay.shape == annotated.shape and alpha.shape == annotated.shape[:2]:
        #             a3 = np.stack([alpha, alpha, alpha], axis=-1)
        #             annotated = (annotated.astype(np.float32) * (1.0 - a3) + overlay.astype(np.float32) * a3).astype(np.uint8)
        #         state.palette_heatmap.draw_roi_box(annotated)
        #     except Exception as e:
        #         print(f"[palette] overlay erro: {e}")
        try:
            if len(tracks) > 0 and tracks.tracker_id is not None:
                for i in range(len(tracks)):
                    tid = int(tracks.tracker_id[i])
                    if tid is None:
                        continue
                    cls_name = state.counter.cls_name(tracks, i)
                    conf = float(tracks.confidence[i]) if tracks.confidence is not None else 0.0
                    x1, y1, x2, y2 = tracks.xyxy[i]
                    beh_data = behaviors_now.get(tid, {})
                    beh_state = beh_data.get("state", State.NORMAL)
                    color = STATE_COLOR_BGR[beh_state]
                    cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                    label = f"#{tid} {cls_name} {beh_state} {conf:.0%}"
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(annotated, (int(x1), int(y1) - th - 6), (int(x1) + tw, int(y1)), color, -1)
                    cv2.putText(
                        annotated, label,
                        (int(x1), int(y1) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1,
                    )
        except Exception as e:
            print(f"[warn] annotator error: {e}")

        # ===== Trilhas coloridas por estado =====
        if state.paths is not None and settings.paths_enabled:
            for d in state.paths.to_dict_list():
                pts = np.array([(p[0], p[1]) for p in d["points"]], dtype=np.int32)
                if len(pts) >= 2:
                    color = STATE_COLOR_BGR.get(
                        behaviors_now.get(d["track_id"], {}).get("state", State.NORMAL),
                        STATE_COLOR_BGR[State.NORMAL],
                    )
                    cv2.polylines(annotated, [pts], False, color, 2, cv2.LINE_AA)
                    cv2.circle(annotated, tuple(pts[-1]), 4, color, -1)

        # HUD (sem linha de contagem — contagem agora é por track único)
        y = 24
        if state.storage is not None:
            cv2.putText(
                annotated, f"camera: {state.storage.camera_id}  turno: {state.storage.turno_id}",
                (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
            )
            y += 20
        for cls, st in state.counter.estado().items():
            txt = f"{cls}: UNIQ={st.get('unique', 0)}  ATIVAS={st['current']}"
            cv2.putText(
                annotated, txt,
                (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (50, 255, 50), 2,
            )
            y += 22
        # linha do palete (cold wave) — DESATIVADA
        # if state.palette_heatmap is not None:
        #     stats = state.palette_heatmap.stats()
        #     cv2.putText(
        #         annotated,
        #         f"PALETE: {stats['inside_now']} galinhas agora | ocupacao {stats['occupancy_pct']:.0f}% | wave={stats['wave_intensity']:.2f}",
        #         (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 80), 2,
        #     )
        #     y += 20

        state.latest_jpeg = _to_jpeg(annotated)
        state.latest_contagens = state.counter.estado()

        # Snapshot de timeline a cada 60s
        import time as _time
        if _time.time() - state.last_timeline_ts >= 60.0:
            state.last_timeline_ts = _time.time()
            snap = {
                "ts": state.last_timeline_ts,
                "total": analytics["total"],
                "ativa": analytics["ativa"],
                "repouso": analytics["repouso"],
                "anomalo": analytics["anomalo"],
                "normal": analytics["normal"],
            }
            state.timeline_history.append(snap)
            if state.repo is not None:
                try:
                    state.repo.log_event("timeline_snapshot", snap)
                except Exception:
                    pass

        # Tracks snapshot via WS (a cada 5 frames)
        state._frame_counter = getattr(state, "_frame_counter", 0) + 1
        if state._frame_counter % 5 == 0 and len(tracks) > 0:
            try:
                tracks_payload = []
                for i in range(len(tracks)):
                    if tracks.tracker_id is None or tracks.tracker_id[i] is None:
                        continue
                    tid = int(tracks.tracker_id[i])
                    cls_name = state.counter.cls_name(tracks, i)
                    x1, y1, x2, y2 = tracks.xyxy[i]
                    conf = float(tracks.confidence[i]) if tracks.confidence is not None else 0.0
                    beh_data = behaviors_now.get(tid, {})
                    tracks_payload.append({
                        "track_id": tid,
                        "cls_name": cls_name,
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                        "conf": round(conf, 3),
                        "state": beh_data.get("state", State.NORMAL),
                        "speed_px_s": beh_data.get("speed_px_s", 0),
                    })
                snap_msg = {"type": "tracks", "tracks": tracks_payload}
                for ws in list(state.ws_clients):
                    try:
                        await ws.send_json(snap_msg)
                    except Exception:
                        state.ws_clients.discard(ws)
            except Exception:
                pass

        await asyncio.sleep(0)


def _to_jpeg(frame: np.ndarray, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    print("[startup] cv-service v4 inicializando...")
    print(
        f"  camera={settings.camera_index} {settings.width}x{settings.height}@{settings.fps}fps"
    )
    print(f"  model={settings.model} conf={settings.confidence}")

    state.db = Database(settings.db_path)
    state.db.connect()
    state.repo = PanelRepo(state.db)

    state.cap = cv2.VideoCapture(settings.video_path or settings.camera_index)
    if not state.cap.isOpened():
        src = settings.video_path or f"camera_index={settings.camera_index}"
        raise RuntimeError(f"Não consegui abrir source={src!r}")
    if settings.video_path:
        # usa o FPS nativo do arquivo se não foi forçado pelo usuário
        native_fps = float(state.cap.get(cv2.CAP_PROP_FPS) or 0)
        print(f"  video source: {settings.video_path}  fps_nativo={native_fps}  loop={settings.video_loop}")
    else:
        state.cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
        state.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
        state.cap.set(cv2.CAP_PROP_FPS, settings.fps)
    state.cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    state.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
    state.cap.set(cv2.CAP_PROP_FPS, settings.fps)

    state.detector = Detector(settings.model, settings.confidence)
    state.tracker = Tracker()
    state.labels = LabelsStore(settings.labels_path)
    state.reid = ReIDEncoder(
        device=None if settings.reid_device == "auto" else settings.reid_device
    )
    state.classifier = EmbeddingClassifier(
        state.labels, threshold=settings.reid_threshold
    )
    state.namer = TrackNamer(
        reid=state.reid,
        classifier=state.classifier,
        labels=state.labels,
        on_new_item=_on_new_item,
    )
    state.counter = Counter(
        line_orientation=settings.line_orientation,
        line_position=settings.line_position,
    )
    state.storage = Storage(turno_dir=settings.turno_dir, camera_id=settings.camera_id)
    state.storage.iniciar_turno()

    # v4
    state.behavior = BehaviorAnalyzer()
    state.sensor = SimulatedEnvironmentSensor()

    state.pending_namer_loop = asyncio.get_running_loop()

    ok, probe = await asyncio.to_thread(state.cap.read)
    if ok and probe is not None:
        state.frame_h, state.frame_w = probe.shape[:2]
        state.counter.configure_frame(state.frame_h, state.frame_w)
        state.heatmap = Heatmap(
            (state.frame_h, state.frame_w),
            kernel_radius=settings.heatmap_radius,
            decay=settings.heatmap_decay,
        )
        state.paths = PathTracker(
            max_points_per_track=settings.path_max_points,
            max_age_seconds=settings.path_max_age,
        )
        state.palette_heatmap = PaletteHeatmap(
            (state.frame_h, state.frame_w),
            decay=0.97,
            amp=0.4,
            gauss_radius_px=80,
        )
        print(f"  frame real: {state.frame_w}x{state.frame_h}")

    state.running = True
    state.current_source_path = settings.video_path or f"camera_index={settings.camera_index}"
    task = asyncio.create_task(_process_loop())

    try:
        yield
    finally:
        state.running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if state.storage:
            state.storage.finalizar_turno()
        if state.cap:
            state.cap.release()
        if state.db:
            state.db.close()
        print("[shutdown] OK")


app = FastAPI(
    title="controle-de-movimento cv-service",
    version="0.4.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "cv-service",
        "version": "0.4.0",
        "endpoints": [
            "/api/state", "/api/labels (GET/PATCH/DELETE)",
            "/api/panel (GET/PATCH)", "/api/heatmap.png", "/api/heatmap/info", "/api/paths",
            "/api/analytics", "/api/sensors", "/api/timeline",
            "/video_feed (MJPEG)", "/api/frame.jpg",
            "/ws/events (WebSocket)",
        ],
    }


@app.get("/api/state")
async def api_state():
    sensor = state.sensor.status() if state.sensor else None
    return JSONResponse({
        "camera_id": settings.camera_id,
        "camera_index": settings.camera_index,
        "video_path": state.current_source_path or None,
        "is_webcam": state.current_source_path.startswith("camera_index="),
        "video_loop": settings.video_loop,
        "loop_count": state.loop_count,
        "resolution": f"{state.frame_w}x{state.frame_h}",
        "model": settings.model,
        "line_orientation": settings.line_orientation,
        "line_position": settings.line_position,
        "contagens": state.latest_contagens,
        "lifetime_contagens": state.lifetime_contagens,
        "labels_count": len(state.labels.list_names()) if state.labels else 0,
        "heatmap_enabled": settings.heatmap_enabled,
        "paths_enabled": settings.paths_enabled,
        "analytics": state.latest_analytics,
        "sensor": sensor,
        "palette": state.palette_heatmap.stats() if state.palette_heatmap else None,
    })


@app.get("/api/labels")
async def api_labels_list():
    if not state.labels:
        return JSONResponse({"labels": []})
    return JSONResponse({"labels": state.labels.summary()})


@app.patch("/api/labels/{old_name}")
async def api_labels_rename(old_name: str, request: Request):
    if not state.labels:
        return JSONResponse({"error": "labels not ready"}, status_code=503)
    body = await request.json()
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        return JSONResponse({"error": "new_name obrigatório"}, status_code=400)
    if old_name == new_name:
        return JSONResponse({"ok": True, "renamed": False})
    ok = state.labels.rename(old_name, new_name)
    if not ok:
        return JSONResponse({"error": f"não consegui renomear {old_name!r}"}, status_code=400)
    if state.repo is not None:
        try:
            state.repo.rename_label(old_name, new_name)
            state.repo.set_fixed(new_name, True)
        except Exception:
            pass
    return JSONResponse({"ok": True, "renamed": True, "from": old_name, "to": new_name, "fixed": True})


@app.delete("/api/labels/{name}")
async def api_labels_delete(name: str):
    if not state.labels:
        return JSONResponse({"error": "labels not ready"}, status_code=503)
    ok = state.labels.remove(name)
    if state.repo is not None:
        try:
            state.repo.delete_label(name)
        except Exception:
            pass
    if not ok:
        return JSONResponse({"error": f"classe {name!r} não existe"}, status_code=404)
    return JSONResponse({"ok": True, "removed": name})


# ===== Painel v3 =====
@app.get("/api/panel")
async def api_panel_get():
    if not state.repo:
        return JSONResponse({})
    return JSONResponse(state.repo.get_settings())


@app.patch("/api/panel")
async def api_panel_patch(request: Request):
    if not state.repo:
        return JSONResponse({"error": "db not ready"}, status_code=503)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "body precisa ser objeto"}, status_code=400)
    state.repo.set_settings_bulk(body)
    if "line_position" in body and state.counter is not None:
        state.counter.line_position = float(body["line_position"])
    if "line_orientation" in body and state.counter is not None:
        state.counter.line_orientation = str(body["line_orientation"])
    return JSONResponse({"ok": True, "saved": list(body.keys())})


# ===== Heatmap v3 =====
@app.get("/api/heatmap/info")
async def api_heatmap_info():
    if state.heatmap is None:
        return JSONResponse({"enabled": False})
    return JSONResponse({
        "enabled": settings.heatmap_enabled,
        "decay": settings.heatmap_decay,
        "radius": settings.heatmap_radius,
        "classes": state.heatmap.classes(),
        "shape": [state.frame_h, state.frame_w],
    })


@app.get("/api/heatmap.png")
async def api_heatmap_png(cls: str | None = Query(default=None)):
    if state.heatmap is None or not settings.heatmap_enabled:
        return Response(status_code=204)
    img = state.heatmap.render(cls_name=cls)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return Response(status_code=500)
    return Response(content=buf.tobytes(), media_type="image/png")


@app.delete("/api/heatmap")
async def api_heatmap_clear(cls: str | None = Query(default=None)):
    if state.heatmap is None:
        return Response(status_code=204)
    state.heatmap.reset(cls_name=cls)
    return JSONResponse({"ok": True, "reset": cls or "all"})


# ===== Paths v3 =====
@app.get("/api/paths")
async def api_paths():
    if state.paths is None:
        return JSONResponse({"paths": []})
    return JSONResponse({"paths": state.paths.to_dict_list()})


# ===== v4 NOVOS =====
@app.get("/api/analytics")
async def api_analytics():
    return JSONResponse({
        "analytics": state.latest_analytics,
        "states": state.latest_states,
    })


@app.get("/api/sensors")
async def api_sensors():
    if state.sensor is None:
        return JSONResponse({"error": "sensor nao inicializado"}, status_code=503)
    return JSONResponse(state.sensor.status())


@app.get("/api/timeline")
async def api_timeline(hours: int = Query(default=24, ge=1, le=72)):
    """Retorna a timeline em memória. Cada entry = snapshot a cada 60s."""
    entries = list(state.timeline_history)
    cutoff_minutes = hours * 60
    if len(entries) > cutoff_minutes:
        entries = entries[-cutoff_minutes:]
    return JSONResponse({
        "entries": entries,
        "granularity_seconds": 60,
        "hours_window": hours,
    })


# ===== Palette ROI (regiao do cocho/palete) =====
@app.get("/api/palette")
async def api_palette_get():
    if state.palette_heatmap is None:
        return JSONResponse({"error": "palette nao inicializada"}, status_code=503)
    return JSONResponse(state.palette_heatmap.stats())


@app.patch("/api/palette/roi")
async def api_palette_roi_patch(request: Request):
    """Define a ROI do palete. Body: {x1, y1, x2, y2} em coords fracionarias [0..1]."""
    if state.palette_heatmap is None:
        return JSONResponse({"error": "palette nao inicializada"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body precisa ser JSON"}, status_code=400)
    try:
        roi = PaletteRoi(
            x1=float(body["x1"]),
            y1=float(body["y1"]),
            x2=float(body["x2"]),
            y2=float(body["y2"]),
        )
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse({"error": f"parametros invalidos: {e}"}, status_code=400)
    if not (0.0 <= roi.x1 < roi.x2 <= 1.0 and 0.0 <= roi.y1 < roi.y2 <= 1.0):
        return JSONResponse({"error": "ROI fora de [0,1] ou x1>=x2 ou y1>=y2"}, status_code=400)
    state.palette_heatmap.set_roi(roi)
    return JSONResponse({"ok": True, "roi": state.palette_heatmap.stats()["roi"]})


# ===== Fonte de vídeo (troca em runtime, sem reiniciar serviço) =====
import os as _os
import shutil as _shutil
import time as _time
import uuid as _uuid

UPLOAD_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "uploads")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
ALLOWED_EXT = {".mp4", ".webm", ".ogv", ".mkv", ".avi", ".mov", ".m4v"}


def _sanitize_ext(name: str) -> str:
    _, ext = _os.path.splitext(name.lower())
    return ext if ext in ALLOWED_EXT else ""


async def _swap_source_to(new_path: str) -> dict:
    """Troca o cv2.VideoCapture em runtime de forma segura (lock + release)."""
    _os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not _os.path.isfile(new_path):
        raise ValueError(f"arquivo não existe: {new_path!r}")
    test_cap = cv2.VideoCapture(new_path)
    if not test_cap.isOpened():
        test_cap.release()
        raise ValueError(f"OpenCV não conseguiu abrir {new_path!r}")
    fps_n = float(test_cap.get(cv2.CAP_PROP_FPS) or 0)
    w = int(test_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(test_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    test_cap.release()

    async with cap_lock:
        old_cap = state.cap
        new_cap = cv2.VideoCapture(new_path)
        state.cap = new_cap
        # reset de estatísticas do turno / loop
        if state.counter is not None:
            state.counter.reset()
        if state.behavior is not None:
            state.behavior = BehaviorAnalyzer()
        if state.heatmap is not None and (w, h) == (state.frame_w, state.frame_h):
            state.heatmap.reset()
        elif state.heatmap is not None:
            state.heatmap = Heatmap(
                (h, w), kernel_radius=settings.heatmap_radius, decay=settings.heatmap_decay
            )
        # recriar palette_heatmap com as novas dimensões
        if state.palette_heatmap is None or (w, h) != (state.frame_w, state.frame_h):
            state.palette_heatmap = PaletteHeatmap((h, w))
        if state.paths is not None:
            # respeita novas dimensões
            try:
                state.counter.configure_frame(h, w)
            except Exception:
                pass
            state.paths = PathTracker(
                max_points_per_track=settings.path_max_points,
                max_age_seconds=settings.path_max_age,
            )
        state.frame_w, state.frame_h = w, h
        if state.counter is not None:
            state.counter.configure_frame(h, w)
        state.lifetime_contagens = {}
        state.loop_count = 0
        state._frame_counter = 0
        state._prev_pos_frames = 0.0
        state.timeline_history.clear()
        state.current_source_path = new_path
        if old_cap is not None:
            try:
                old_cap.release()
            except Exception:
                pass
    return {
        "video_path": new_path,
        "fps": fps_n,
        "width": w,
        "height": h,
    }


@app.get("/api/source")
async def api_source_get():
    return JSONResponse({
        "video_path": state.current_source_path or None,
        "is_webcam": state.current_source_path.startswith("camera_index="),
        "video_loop": settings.video_loop,
        "camera_index": settings.camera_index,
        "loop_count": state.loop_count,
        "resolution": f"{state.frame_w}x{state.frame_h}",
    })


@app.post("/api/source/upload")
async def api_source_upload(request: Request):
    """Recebe multipart/form-data com campo 'file' e troca o vídeo atual."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        return JSONResponse({"error": "esperado multipart/form-data"}, status_code=400)

    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES + 4096:
        return JSONResponse(
            {"error": f"arquivo > {MAX_UPLOAD_BYTES // (1024*1024)} MB"},
            status_code=413,
        )

    # parse multipart simples via python-multipart se disponível; senão
    # fallback: usa Request.form() do Starlette
    try:
        form = await request.form()
        file = form.get("file")
        if file is None:
            return JSONResponse({"error": "campo 'file' ausente"}, status_code=400)
        filename = getattr(file, "filename", "upload.bin")
        ext = _sanitize_ext(filename)
        if not ext:
            return JSONResponse(
                {"error": f"extensão não suportada (use {sorted(ALLOWED_EXT)})"},
                status_code=400,
            )
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"error": f"arquivo > {MAX_UPLOAD_BYTES // (1024*1024)} MB"},
                status_code=413,
            )
    except Exception as e:
        return JSONResponse({"error": f"falha parsing multipart: {e}"}, status_code=400)

    _os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = f"{int(_time.time())}_{_uuid.uuid4().hex[:8]}{ext}"
    save_path = _os.path.join(UPLOAD_DIR, safe_name)
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        info = await _swap_source_to(save_path)
    except Exception as e:
        try:
            _os.remove(save_path)
        except Exception:
            pass
        return JSONResponse({"error": str(e)}, status_code=400)

    print(f"[source] upload trocado: {save_path}  fps={info['fps']}  {info['width']}x{info['height']}")
    return JSONResponse({"ok": True, "source": info})


@app.post("/api/source/url")
async def api_source_url(request: Request):
    """Baixa um vídeo de uma URL pública e troca a fonte."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body precisa ser JSON"}, status_code=400)
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "campo 'url' obrigatório"}, status_code=400)

    # extrai extensão da URL
    from urllib.parse import urlparse
    ext = _sanitize_ext(urlparse(url).path)
    if not ext:
        # tenta Content-Type
        try:
            import urllib.request as _ur
            head = _ur.urlopen(url, timeout=10)
            ct = head.headers.get("Content-Type", "")
            head.close()
            if "mp4" in ct: ext = ".mp4"
            elif "webm" in ct: ext = ".webm"
            elif "ogg" in ct: ext = ".ogv"
            else:
                return JSONResponse(
                    {"error": f"não consegui inferir extensão (Content-Type={ct!r})"},
                    status_code=400,
                )
        except Exception as e:
            return JSONResponse({"error": f"falha HEAD: {e}"}, status_code=400)

    _os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = f"{int(_time.time())}_{_uuid.uuid4().hex[:8]}{ext}"
    save_path = _os.path.join(UPLOAD_DIR, safe_name)

    try:
        with _ur.urlopen(url, timeout=120) as resp, open(save_path, "wb") as fh:
            total = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk: break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    fh.close(); _os.remove(save_path)
                    return JSONResponse(
                        {"error": f"download > {MAX_UPLOAD_BYTES // (1024*1024)} MB"},
                        status_code=413,
                    )
                fh.write(chunk)
    except Exception as e:
        try: _os.remove(save_path)
        except Exception: pass
        return JSONResponse({"error": f"download falhou: {e}"}, status_code=400)

    try:
        info = await _swap_source_to(save_path)
    except Exception as e:
        try: _os.remove(save_path)
        except Exception: pass
        return JSONResponse({"error": str(e)}, status_code=400)

    print(f"[source] url trocada: {url} -> {save_path}")
    return JSONResponse({"ok": True, "source": info})


@app.post("/api/source/webcam")
async def api_source_webcam():
    """Volta a usar a webcam (camera_index)."""
    idx = settings.camera_index
    test_cap = cv2.VideoCapture(idx)
    if not test_cap.isOpened():
        test_cap.release()
        return JSONResponse({"error": f"webcam index={idx} indisponível"}, status_code=400)
    test_cap.release()

    async with cap_lock:
        old_cap = state.cap
        new_cap = cv2.VideoCapture(idx)
        new_cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
        new_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
        new_cap.set(cv2.CAP_PROP_FPS, settings.fps)
        state.cap = new_cap
        state.lifetime_contagens = {}
        state.loop_count = 0
        state._frame_counter = 0
        state._prev_pos_frames = 0.0
        state.timeline_history.clear()
        state.current_source_path = f"camera_index={idx}"
        if state.counter is not None: state.counter.reset()
        if state.behavior is not None: state.behavior = BehaviorAnalyzer()
        if state.heatmap is not None: state.heatmap.reset()
        if state.palette_heatmap is not None: state.palette_heatmap.reset()
        if state.paths is not None: state.paths = PathTracker(
            max_points_per_track=settings.path_max_points,
            max_age_seconds=settings.path_max_age,
        )
        if old_cap is not None:
            try: old_cap.release()
            except Exception: pass

    return JSONResponse({
        "ok": True,
        "source": {"video_path": None, "camera_index": idx}
    })


# ===== Stream =====
@app.get("/video_feed")
async def video_feed():
    async def gen():
        boundary = b"--frame"
        while True:
            jpeg = state.latest_jpeg
            if jpeg:
                yield (
                    boundary + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
            await asyncio.sleep(1.0 / max(settings.fps, 1))
    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/frame.jpg")
async def api_frame_jpg():
    if state.latest_jpeg:
        return Response(content=state.latest_jpeg, media_type="image/jpeg")
    return Response(status_code=204)


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    state.ws_clients.add(ws)
    try:
        await ws.send_json({
            "type": "init",
            "contagens": state.latest_contagens,
            "resolution": [state.frame_w, state.frame_h],
            "line_orientation": settings.line_orientation,
            "line_position": settings.line_position,
            "labels": state.labels.summary() if state.labels else [],
            "panel": state.repo.get_settings() if state.repo else {},
            "analytics": state.latest_analytics,
            "sensor": state.sensor.status() if state.sensor else None,
        })
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
