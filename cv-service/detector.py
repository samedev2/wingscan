"""
Detector YOLOv8/YOLO11 generico com filtro opcional por classe.

Por padrao detecta TODAS as classes do modelo. Se `only_class` for
informado no construtor (ex.: "galinha"), o `model.predict` recebe
`classes=[idx]` para que o YOLO retorne APENAS deteccoes daquela
classe. Isso economiza CPU no tracker/namer/ReID e ainda deixa o
video limpo (so bboxes de galinha aparecem).

Como o indice da classe depende do modelo carregado, resolvemos
o indice a partir de `model.names` (dict {idx: name}). Se o nome
nao existir no modelo, lancamos ValueError com lista das classes
disponiveis.
"""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO


class Detector:
    def __init__(self, model_name: str, conf: float, only_class: str | None = None):
        self.model = YOLO(model_name)
        self.conf = conf
        self.only_class = only_class

        names = self.model.names  # dict {int: str}
        # mapa reverso case-insensitive pra aceitar "Galinha", "GALINHA", "galinha"
        self._idx_by_name_ci = {str(n).lower(): idx for idx, n in names.items()}

        self._filter_indices: list[int] | None = None
        if only_class:
            key = only_class.strip().lower()
            if key not in self._idx_by_name_ci:
                raise ValueError(
                    f"[detector] classe '{only_class}' nao existe no modelo "
                    f"{model_name!r}. classes disponiveis: "
                    f"{list(names.values())}"
                )
            self._filter_indices = [self._idx_by_name_ci[key]]
            print(
                f"[detector] {model_name} carregado — filtro ATIVO: "
                f"somente '{only_class}' (idx={self._filter_indices[0]}); "
                f"{len(names)} classes no modelo"
            )
        else:
            print(
                f"[detector] {model_name} carregado — "
                f"{len(names)} classes (todas ativas, sem filtro)"
            )

    def detect(self, frame: np.ndarray) -> sv.Detections:
        results = self.model.predict(
            frame,
            conf=self.conf,
            verbose=False,
            classes=self._filter_indices,  # None = todas; lista = filtra
        )
        if not results:
            return sv.Detections.empty()
        det = sv.Detections.from_ultralytics(results[0])
        # Preserva o mapeamento class_id -> class_name (pinto/galinha/galo) que se perde
        # ao converter de ultralytics para supervision
        try:
            det.names = dict(results[0].names)
        except Exception:
            pass
        return det
