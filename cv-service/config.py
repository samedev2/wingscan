"""Configuração do cv-service via env vars (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} precisa ser float, recebi {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} precisa ser int, recebi {raw!r}") from exc


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw is not None and raw != "" else default


@dataclass(frozen=True)
class Settings:
    # Câmera
    camera_index: int = _env_int("CV_CAMERA_INDEX", 0)
    width: int = _env_int("CV_WIDTH", 1280)
    height: int = _env_int("CV_HEIGHT", 720)
    fps: int = _env_int("CV_FPS", 30)

    # Modelo
    model: str = _env_str("CV_MODEL", "yolov8n.pt")
    confidence: float = _env_float("CV_CONFIDENCE", 0.35)

    # Contagem (linha virtual)
    line_orientation: str = _env_str("CV_LINE_ORIENTATION", "horizontal")
    line_position: float = _env_float("CV_LINE_POSITION", 0.5)

    # Storage
    camera_id: str = _env_str("CV_CAMERA_ID", "webcam-0")
    turno_dir: str = _env_str("CV_TURNO_DIR", "data/turnos")

    # ReID
    reid_threshold: float = _env_float("CV_REID_THRESHOLD", 0.65)
    reid_device: str = _env_str("CV_REID_DEVICE", "auto")  # auto|cuda|cpu
    labels_path: str = _env_str("CV_LABELS_PATH", "data/labels.json")

    # Server
    host: str = _env_str("CV_HOST", "127.0.0.1")
    port: int = _env_int("CV_PORT", 8000)


settings = Settings()
