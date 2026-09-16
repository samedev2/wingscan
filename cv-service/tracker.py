"""Wrapper ByteTrack via supervision."""
from __future__ import annotations

import supervision as sv


class Tracker:
    """Mantém IDs estáveis entre frames."""

    def __init__(self) -> None:
        self._tracker = sv.ByteTrack()

    def update(self, detections: sv.Detections) -> sv.Detections:
        tracked = self._tracker.update_with_detections(detections)
        # Preserva o mapeamento class_id -> class_name que se perde
        # ao passar pelo ByteTrack (pinto/galinha/galo do pinteiro.pt)
        try:
            if getattr(detections, "names", None) and not getattr(tracked, "names", None):
                tracked.names = dict(detections.names)
        except Exception:
            pass
        return tracked
