"""
Detector YOLOv8 genérico (todas as 80 classes COCO).

Na v2 o classificador semântico é o classificador de embeddings (namer),
não mais o nome da classe COCO. Aqui só pegamos TUDO que aparece.
"""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO


class Detector:
    def __init__(self, model_name: str, conf: float):
        self.model = YOLO(model_name)
        self.conf = conf
        names = self.model.names
        print(
            f"[detector] {model_name} carregado — "
            f"{len(names)} classes disponíveis (genérico, todas ativas)"
        )

    def detect(self, frame: np.ndarray) -> sv.Detections:
        results = self.model.predict(
            frame,
            conf=self.conf,
            verbose=False,
        )
        if not results:
            return sv.Detections.empty()
        return sv.Detections.from_ultralytics(results[0])
