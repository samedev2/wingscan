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


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    # Câmera
    camera_index: int = _env_int("CV_CAMERA_INDEX", 0)
    width: int = _env_int("CV_WIDTH", 1280)
    height: int = _env_int("CV_HEIGHT", 720)
    fps: int = _env_int("CV_FPS", 30)

    # Modelo
    model: str = os.getenv("CV_MODEL", "yolov8n.pt")
    confidence: float = _env_float("CV_CONFIDENCE", 0.4)
    target_classes: tuple[str, ...] = _env_tuple("CV_CLASSES", ("bird", "person"))

    # Contagem (linha virtual)
    line_orientation: str = os.getenv("CV_LINE_ORIENTATION", "horizontal")  # horizontal | vertical
    line_position: float = _env_float("CV_LINE_POSITION", 0.5)  # 0..1 do frame

    # Storage
    camera_id: str = os.getenv("CV_CAMERA_ID", "webcam-0")
    turno_dir: str = os.getenv("CV_TURNO_DIR", "data/turnos")

    # Server
    host: str = os.getenv("CV_HOST", "127.0.0.1")
    port: int = _env_int("CV_PORT", 8000)


settings = Settings()
