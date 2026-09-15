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
    behavior: BehaviorAnalyzer | None = None
    sensor: SimulatedEnvironmentSensor | None = None
    running: bool = False
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

        ok, frame = await asyncio.to_thread(state.cap.read)
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
        analytics = {
            "total": total_active,
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

        # Linha de contagem
        if state.counter.line_orientation == "horizontal":
            y_line = int(h * state.counter.line_position)
            cv2.line(annotated, (0, y_line), (w, y_line), (0, 255, 255), 2)
            cv2.putText(
                annotated, f"IN/OUT line @ y={y_line}px",
                (10, max(20, y_line - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1,
            )
        else:
            x_line = int(w * state.counter.line_position)
            cv2.line(annotated, (x_line, 0), (x_line, h), (0, 255, 255), 2)
            cv2.putText(
                annotated, f"IN/OUT line @ x={x_line}px",
                (max(10, x_line + 4), 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1,
            )

        # HUD
        y = 24
        if state.storage is not None:
            cv2.putText(
                annotated, f"camera: {state.storage.camera_id}  turno: {state.storage.turno_id}",
                (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
            )
            y += 20
        for cls, st in state.counter.estado().items():
            txt = f"{cls}: IN={st['in']}  OUT={st['out']}  ATIVOS={st['current']}"
            cv2.putText(
                annotated, txt,
                (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (50, 255, 50), 2,
            )
            y += 22

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

    state.cap = cv2.VideoCapture(settings.camera_index)
    if not state.cap.isOpened():
        raise RuntimeError(f"Não consegui abrir câmera index={settings.camera_index}")
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
        print(f"  frame real: {state.frame_w}x{state.frame_h}")

    state.running = True
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
        "resolution": f"{state.frame_w}x{state.frame_h}",
        "model": settings.model,
        "line_orientation": settings.line_orientation,
        "line_position": settings.line_position,
        "contagens": state.latest_contagens,
        "labels_count": len(state.labels.list_names()) if state.labels else 0,
        "heatmap_enabled": settings.heatmap_enabled,
        "paths_enabled": settings.paths_enabled,
        "analytics": state.latest_analytics,
        "sensor": sensor,
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
