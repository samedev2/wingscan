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
        sqlite_matcher=None,
    ):
        self.reid = reid
        self.classifier = classifier
        self.labels = labels
        self.on_new_item = on_new_item  # callback opcional
        self.sqlite_matcher = sqlite_matcher  # se setado, bypassa classifier (ReID SQLite ativo)
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

    def _next_named_item_name(self, classe: str) -> str:
        """Auto-nomeia usando a classe do detector como prefixo (pinteiro.pt).
        Ex: classe='pinto' -> 'pinto-1', 'pinto-2', ...
            classe='galinha' -> 'galinha-1', 'galinha-2', ...
        """
        existing = set(self.labels.list_names())
        prefix = classe.lower().strip()
        # sanitiza: só letras/numeros/hifen
        prefix = "".join(c for c in prefix if c.isalnum() or c == "-")
        if not prefix:
            prefix = "item"
        i = 0
        while True:
            i += 1
            candidate = f"{prefix}-{i}"
            if candidate not in existing:
                return candidate

    def forget(self, track_id: int) -> None:
        self._cache.pop(track_id, None)

    def resolve(self, tracks: sv.Detections, frame: np.ndarray) -> sv.Detections:
        """
        Adiciona `tracks.data["class_name"]` com o nome resolvido para cada track.
        Modifica o Detections in-place.

        Branch v5-pinteiro: quando o detector eh o pinteiro.pt, cada track ja
        vem com classe nativa (pinto/galinha/galo). Usamos essa classe como
        prefixo do nome (ex: 'pinto-1', 'galinha-2', 'galo-1') e o ReID
        ancora o mesmo individuo entre frames (match pelo embedding).
        """
        if tracks.tracker_id is None or len(tracks) == 0:
            return tracks

        # classes que vieram do detector (pode ser 'pinto'/'galinha'/'galo'/'bird'/etc)
        detector_classes: list[str | None] = []
        if tracks.class_id is not None and getattr(tracks, "names", None):
            for ci in tracks.class_id:
                try:
                    detector_classes.append(tracks.names.get(int(ci), None))
                except Exception:
                    detector_classes.append(None)
        else:
            detector_classes = [None] * len(tracks)

        names: list[str] = []
        for i, track_id in enumerate(tracks.tracker_id):
            if track_id is None:
                names.append("?")
                continue
            tid = int(track_id)
            cached = self._cache.get(tid)
            if cached is not None:
                # se a classe do detector mudou mas a ave eh a mesma (pelo cache),
                # mantemos o nome antigo (evita trocar galinha<->galo por ruido)
                names.append(cached)
                continue

            bbox = tuple(tracks.xyxy[i].tolist())
            classe = (detector_classes[i] or "item").lower()
            sim = 0.0

            if self.sqlite_matcher is not None:
                # ReID SQLite ativo: usa matcher (ignora V1 labels.json)
                embedding = self.reid.encode(frame, bbox)
                match = self.sqlite_matcher.find_match(embedding, type_filter=classe)
                if match is not None:
                    cls_name = match["name"]
                    sim = float(match.get("score", 0.0))
                    self.sqlite_matcher.update_seen(match["id"], track_id=tid)
                    # captura individual full-res em cache rotativo
                    self.sqlite_matcher.save_capture(int(match["id"]), frame, bbox)
                else:
                    # Nova identidade: registra embedding + thumbnail no SQLite
                    cls_name = self.sqlite_matcher.next_name(classe)
                    new = self.sqlite_matcher.register(frame, bbox, embedding, classe, camera_id=None)
                    # captura inicial
                    if new and "id" in new:
                        self.sqlite_matcher.save_capture(int(new["id"]), frame, bbox)
                    sim = 1.0  # primeira aparicao = certeza
            else:
                # V1 legacy: usa EmbeddingClassifier + labels.json
                embedding = self.reid.encode(frame, bbox)
                cls_name, sim = self.classifier.classify(embedding)
                if cls_name is None:
                    # Item novo: usa a classe do detector como prefixo
                    cls_name = self._next_named_item_name(classe)
                    self.labels.add_embedding(cls_name, embedding)

            if self.on_new_item is not None and sim >= 1.0:
                # so dispara callback em primeira aparicao (sim==1.0 significa nova identidade)
                try:
                    self.on_new_item(tid, cls_name, sim, frame, bbox, embedding)
                except Exception as e:
                    print(f"[namer] on_new_item callback falhou: {e}")

            self._cache[tid] = cls_name
            names.append(cls_name)

        if tracks.data is None:
            tracks.data = {}
        tracks.data["class_name"] = names
        return tracks
