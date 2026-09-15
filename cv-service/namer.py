"""
TrackNamer: para cada track novo, extrai embedding, classifica e decide o nome.
- Se classifier retornar classe conhecida → usa o nome dela.
- Se classifier retornar None (novo) → auto-nomeia como "ItemN" e salva embedding.

Mantém cache por track_id (não recalcula embedding todo frame).
Quando detecta item novo, dispara callback `on_new_item(track_id, name, sim, frame, bbox)`
para o app notificar via WebSocket.
"""
from __future__ import annotations

import numpy as np
import supervision as sv

from classifier import EmbeddingClassifier
from labels import LabelsStore
from reid import ReIDEncoder


class TrackNamer:
    def __init__(
        self,
        reid: ReIDEncoder,
        classifier: EmbeddingClassifier,
        labels: LabelsStore,
        on_new_item=None,
    ):
        self.reid = reid
        self.classifier = classifier
        self.labels = labels
        self.on_new_item = on_new_item  # callback opcional
        self._cache: dict[int, str] = {}
        self._counter: int = 0

    def _next_item_name(self) -> str:
        """Auto-nomeia Item1, Item2, ... evitando colisão com classes já existentes."""
        existing = set(self.labels.list_names())
        while True:
            self._counter += 1
            candidate = f"Item{self._counter}"
            if candidate not in existing:
                return candidate

    def forget(self, track_id: int) -> None:
        self._cache.pop(track_id, None)

    def resolve(self, tracks: sv.Detections, frame: np.ndarray) -> sv.Detections:
        """
        Adiciona `tracks.data["class_name"]` com o nome resolvido para cada track.
        Modifica o Detections in-place.
        """
        if tracks.tracker_id is None or len(tracks) == 0:
            return tracks

        names: list[str] = []
        for i, track_id in enumerate(tracks.tracker_id):
            if track_id is None:
                names.append("?")
                continue
            tid = int(track_id)
            cached = self._cache.get(tid)
            if cached is not None:
                names.append(cached)
                continue

            bbox = tuple(tracks.xyxy[i].tolist())
            embedding = self.reid.encode(frame, bbox)
            cls_name, sim = self.classifier.classify(embedding)

            if cls_name is None:
                # Item novo: auto-nomeia e notifica
                cls_name = self._next_item_name()
                self.labels.add_embedding(cls_name, embedding)
                if self.on_new_item is not None:
                    try:
                        self.on_new_item(
                            tid, cls_name, sim, frame, bbox, embedding
                        )
                    except Exception as e:
                        print(f"[namer] on_new_item callback falhou: {e}")

            self._cache[tid] = cls_name
            names.append(cls_name)

        if tracks.data is None:
            tracks.data = {}
        tracks.data["class_name"] = names
        return tracks
