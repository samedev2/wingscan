"""Conta IN/OUT por classe a partir do cruzamento de uma linha virtual."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import supervision as sv


@dataclass
class CrossingEvent:
    ts: float
    track_id: int
    cls_name: str
    direction: str  # "in" | "out"
    conf: float


@dataclass
class _TrackInfo:
    cx: float
    cy: float
    last_ts: float


@dataclass
class _ClassState:
    cls_name: str
    in_count: int = 0
    out_count: int = 0
    current: int = 0
    tracks: dict[int, _TrackInfo] = field(default_factory=dict)


class Counter:
    """
    Conta cruzamentos de uma linha virtual por classe.
    - line_orientation="horizontal": linha Y constante; cima->baixo = in, baixo->cima = out
    - line_orientation="vertical":   linha X constante; esquerda->direita = in, direita->esquerda = out
    """

    EXPIRY_SECONDS = 3.0

    def __init__(self, line_orientation: str = "horizontal", line_position: float = 0.5):
        if line_orientation not in ("horizontal", "vertical"):
            raise ValueError(f"line_orientation deve ser horizontal|vertical, recebi {line_orientation!r}")
        if not 0.0 <= line_position <= 1.0:
            raise ValueError(f"line_position deve estar em [0,1], recebi {line_position!r}")
        self.line_orientation = line_orientation
        self.line_position = line_position
        self.frame_h: int = 720
        self.frame_w: int = 1280
        self.states: dict[str, _ClassState] = {}

    def configure_frame(self, h: int, w: int) -> None:
        self.frame_h = h
        self.frame_w = w

    def _line_coord(self) -> float:
        if self.line_orientation == "horizontal":
            return self.frame_h * self.line_position
        return self.frame_w * self.line_position

    def _state_for(self, cls_name: str) -> _ClassState:
        st = self.states.get(cls_name)
        if st is None:
            st = _ClassState(cls_name=cls_name)
            self.states[cls_name] = st
        return st

    @staticmethod
    def cls_name(tracks: sv.Detections, i: int) -> str:
        if tracks.data and "class_name" in tracks.data:
            try:
                value = tracks.data["class_name"][i]
                if value is not None:
                    return str(value)
            except (KeyError, IndexError, TypeError):
                pass
        if tracks.class_id is not None:
            try:
                return f"class_{int(tracks.class_id[i])}"
            except (KeyError, IndexError, TypeError):
                pass
        return "unknown"

    def update(self, tracks: sv.Detections) -> list[CrossingEvent]:
        events: list[CrossingEvent] = []
        if tracks.tracker_id is None or len(tracks) == 0:
            self._expire()
            return events

        line_pos = self._line_coord()
        now = time.time()

        for i, track_id in enumerate(tracks.tracker_id):
            if track_id is None:
                continue
            tid = int(track_id)

            x1, y1, x2, y2 = tracks.xyxy[i]
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0

            cls_name = self.cls_name(tracks, i)
            state = self._state_for(cls_name)

            info = state.tracks.get(tid)
            if info is None:
                # Primeira aparição desse track nessa classe
                state.tracks[tid] = _TrackInfo(cx=cx, cy=cy, last_ts=now)
                state.current = len(state.tracks)
                continue

            prev_cy, prev_cx = info.cy, info.cx
            info.cx, info.cy, info.last_ts = cx, cy, now

            direction: str | None = None
            if self.line_orientation == "horizontal":
                if prev_cy < line_pos and cy >= line_pos:
                    direction = "in"
                elif prev_cy > line_pos and cy <= line_pos:
                    direction = "out"
            else:
                if prev_cx < line_pos and cx >= line_pos:
                    direction = "in"
                elif prev_cx > line_pos and cx <= line_pos:
                    direction = "out"

            if direction is None:
                continue

            if direction == "in":
                state.in_count += 1
            else:
                state.out_count += 1

            conf = float(tracks.confidence[i]) if tracks.confidence is not None else 0.0
            events.append(
                CrossingEvent(
                    ts=now,
                    track_id=tid,
                    cls_name=cls_name,
                    direction=direction,
                    conf=conf,
                )
            )

        self._expire(now)
        return events

    def _expire(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for state in self.states.values():
            expired_ids = [
                tid for tid, info in state.tracks.items()
                if now - info.last_ts > self.EXPIRY_SECONDS
            ]
            for tid in expired_ids:
                del state.tracks[tid]
            state.current = len(state.tracks)

    def estado(self) -> dict[str, dict[str, int]]:
        return {
            s.cls_name: {"in": s.in_count, "out": s.out_count, "current": s.current}
            for s in self.states.values()
        }
