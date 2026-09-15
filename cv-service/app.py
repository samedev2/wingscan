"""
cv-service: webcam -> YOLOv8 + ByteTrack -> contagem IN/OUT por linha virtual
   -> stream MJPEG anotado + WebSocket de eventos.

Roda como FastAPI em http://127.0.0.1:8000 (ajustável por CV_HOST/CV_PORT).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import cv2
import numpy as np
import supervision as sv
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config import settings
from counter import Counter
from detector import Detector
from storage import Storage
from tracker import Tracker


class AppState:
    cap: cv2.VideoCapture | None = None
    detector: Detector | None = None
    tracker: Tracker | None = None
    counter: Counter | None = None
    storage: Storage | None = None
    running: bool = False
    latest_jpeg: bytes = b""
    latest_contagens: dict = {}
    frame_h: int = 720
    frame_w: int = 1280
    ws_clients: set[WebSocket] = set()


state = AppState()


async def _process_loop() -> None:
    """Loop principal: lê frame, detecta, rastreia, conta, anota."""
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

        detections = state.detector.detect(frame)
        tracks = state.tracker.update(detections)
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
                # Broadcast para todos os WS conectados
                for ws in list(state.ws_clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        state.ws_clients.discard(ws)

        # Anota frame
        annotated = frame.copy()
        try:
            if len(tracks) > 0 and tracks.tracker_id is not None:
                labels = []
                for i in range(len(tracks)):
                    tid = tracks.tracker_id[i]
                    cls_name = state.counter.cls_name(tracks, i)
                    conf = float(tracks.confidence[i]) if tracks.confidence is not None else 0.0
                    labels.append(f"#{int(tid)} {cls_name} {conf:.0%}")
                annotated = box_annotator.annotate(annotated, tracks)
                annotated = label_annotator.annotate(annotated, tracks, labels=labels)
        except Exception as e:
            print(f"[warn] annotator error: {e}")

        # Linha de contagem
        if state.counter.line_orientation == "horizontal":
            y_line = int(h * state.counter.line_position)
            cv2.line(annotated, (0, y_line), (w, y_line), (0, 255, 255), 2)
            cv2.putText(
                annotated,
                f"IN/OUT line @ y={y_line}px",
                (10, max(20, y_line - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                1,
            )
        else:
            x_line = int(w * state.counter.line_position)
            cv2.line(annotated, (x_line, 0), (x_line, h), (0, 255, 255), 2)
            cv2.putText(
                annotated,
                f"IN/OUT line @ x={x_line}px",
                (max(10, x_line + 4), 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                1,
            )

        # HUD com contagens
        y = 24
        if state.storage is not None:
            cv2.putText(
                annotated,
                f"camera: {state.storage.camera_id}  turno: {state.storage.turno_id}",
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (200, 200, 200),
                1,
            )
            y += 20
        for cls, st in state.counter.estado().items():
            txt = f"{cls}: IN={st['in']}  OUT={st['out']}  ATIVOS={st['current']}"
            cv2.putText(
                annotated,
                txt,
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (50, 255, 50),
                2,
            )
            y += 22

        state.latest_jpeg = _to_jpeg(annotated)
        state.latest_contagens = state.counter.estado()
        await asyncio.sleep(0)


def _to_jpeg(frame: np.ndarray, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    print("[startup] cv-service inicializando...")
    print(
        f"  camera_index={settings.camera_index} "
        f"{settings.width}x{settings.height}@{settings.fps}fps"
    )
    print(
        f"  model={settings.model} conf={settings.confidence} "
        f"classes={settings.target_classes}"
    )

    state.cap = cv2.VideoCapture(settings.camera_index)
    if not state.cap.isOpened():
        raise RuntimeError(
            f"Não consegui abrir a câmera index={settings.camera_index}. "
            "Confira se outra app não está usando e se o índice está certo "
            "(experimente CV_CAMERA_INDEX=1 para a segunda webcam)."
        )
    state.cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    state.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
    state.cap.set(cv2.CAP_PROP_FPS, settings.fps)

    state.detector = Detector(
        settings.model, settings.target_classes, settings.confidence
    )
    state.tracker = Tracker()
    state.counter = Counter(
        line_orientation=settings.line_orientation,
        line_position=settings.line_position,
    )
    state.storage = Storage(
        turno_dir=settings.turno_dir, camera_id=settings.camera_id
    )
    state.storage.iniciar_turno()

    # Dimensões reais
    ok, probe = await asyncio.to_thread(state.cap.read)
    if ok and probe is not None:
        state.frame_h, state.frame_w = probe.shape[:2]
        state.counter.configure_frame(state.frame_h, state.frame_w)
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
            print(f"[shutdown] Turno finalizado: {state.storage.json_path}")
        if state.cap:
            state.cap.release()
        print("[shutdown] OK")


app = FastAPI(
    title="controle-de-movimento cv-service",
    version="0.1.0",
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
        "version": "0.1.0",
        "endpoints": [
            "/api/state",
            "/video_feed (MJPEG)",
            "/api/frame.jpg",
            "/ws/events (WebSocket)",
        ],
    }


@app.get("/api/state")
async def api_state():
    return JSONResponse({
        "camera_id": settings.camera_id,
        "camera_index": settings.camera_index,
        "resolution": f"{state.frame_w}x{state.frame_h}",
        "model": settings.model,
        "classes": list(settings.target_classes),
        "line_orientation": settings.line_orientation,
        "line_position": settings.line_position,
        "contagens": state.latest_contagens,
    })


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
