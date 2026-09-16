"""
Path tracker: guarda últimos N pontos (x, y, t) de cada track_id.
Persiste no SQLite via PanelRepo (paths table) para sobreviver a restart.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PathTracker:
    """Mantém trilhas em memória + persiste no DB."""

    max_points_per_track: int = 80
    max_age_seconds: float = 8.0
    _tracks: dict[int, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=80)))
    _cls: dict[int, str] = field(default_factory=dict)
    _last_seen: dict[int, float] = field(default_factory=dict)

    def update(self, track_id: int, cls_name: str, point: tuple[float, float]) -> None:
        """Adiciona ponto e classifica o track."""
        now = time.time()
        if track_id not in self._tracks or self._tracks[track_id].maxlen != self.max_points_per_track:
            self._tracks[track_id] = deque(maxlen=self.max_points_per_track)
        self._tracks[track_id].append((point[0], point[1], now))
        self._cls[track_id] = cls_name
        self._last_seen[track_id] = now

    def expire(self) -> list[int]:
        """Remove tracks que sumiram do frame. Retorna os IDs removidos."""
        now = time.time()
        removed = []
        for tid in list(self._tracks.keys()):
            if now - self._last_seen.get(tid, now) > self.max_age_seconds:
                del self._tracks[tid]
                self._cls.pop(tid, None)
                self._last_seen.pop(tid, None)
                removed.append(tid)
        return removed

    def to_dict_list(self) -> list[dict]:
        return [
            {
                "track_id": tid,
                "cls_name": self._cls.get(tid, "?"),
                # Coerção explícita pra float Python nativo — evita TypeError
                # "Object of type float32 is not JSON serializable" se algum
                # valor vier como np.float32 (ex: vindo de bbox do ultralytics).
                "points": [[float(p[0]), float(p[1]), float(p[2])] for p in pts],
            }
            for tid, pts in self._tracks.items()
            if len(pts) >= 2
        ]

    def clear(self) -> None:
        self._tracks.clear()
        self._cls.clear()
        self._last_seen.clear()
