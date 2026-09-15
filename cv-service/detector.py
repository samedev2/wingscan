"""Wrapper YOLOv8 (ultralytics) -> supervision.Detections."""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO


class Detector:
    def __init__(self, model_name: str, target_classes: tuple[str, ...], conf: float):
        self.model = YOLO(model_name)
        self.conf = conf
        names = self.model.names  # dict[int, str]
        self.class_ids: list[int] = [
            i for i, name in names.items() if name in target_classes
        ]
        if not self.class_ids:
            print(
                f"[WARN] Nenhuma classe-alvo encontrada no modelo {model_name}."
                f" Disponíveis: {sorted(set(names.values()))}"
            )
        else:
            resolved = [names[i] for i in self.class_ids]
            print(f"[detector] {model_name} ativo para classes={resolved} (ids={self.class_ids})")

    def detect(self, frame: np.ndarray) -> sv.Detections:
        results = self.model.predict(
            frame,
            conf=self.conf,
            classes=self.class_ids,
            verbose=False,
        )
        if not results:
            return sv.Detections.empty()
        return sv.Detections.from_ultralytics(results[0])
