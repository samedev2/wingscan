"""Wrapper ByteTrack via supervision."""
from __future__ import annotations

import supervision as sv


class Tracker:
    """Mantém IDs estáveis entre frames."""

    def __init__(self) -> None:
        self._tracker = sv.ByteTrack()

    def update(self, detections: sv.Detections) -> sv.Detections:
        return self._tracker.update_with_detections(detections)
