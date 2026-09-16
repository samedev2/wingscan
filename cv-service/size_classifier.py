"""
SizeClassifier: separa 'bird' (classe 14 do COCO) em 'pintainho' ou 'galinha'
baseado no tamanho da bbox. Heuristica simples mas robusta em galpoes:

  area_bbox < threshold    -> 'pintainho'  (filhote, ainda pequeno)
  area_bbox >= threshold   -> 'galinha'    (adulta)

Threshold configuravel por env (CV_CHICK_AREA_PX2). O padrao 8000 px2 funciona
bem em 720p e 2K (a escala relativa e similar para pintos vs galinhas adultas).

Quando a classe original NAO e 'bird' (ex: 'person', caixote), retorna o nome
original sem mexer.
"""
from __future__ import annotations

from typing import Tuple


class SizeClassifier:
    """Classificador de tamanho (pintainho vs galinha) por bbox."""

    def __init__(
        self,
        area_threshold_px2: float = 8000.0,
        chick_label: str = "pintainho",
        hen_label: str = "galinha",
    ):
        self.area_threshold_px2 = float(area_threshold_px2)
        self.chick_label = chick_label
        self.hen_label = hen_label

    def set_threshold(self, area_threshold_px2: float) -> None:
        self.area_threshold_px2 = float(area_threshold_px2)

    def classify(self, bbox: Tuple[float, float, float, float]) -> str:
        """Recebe (x1, y1, x2, y2) e retorna 'pintainho' ou 'galinha'."""
        x1, y1, x2, y2 = bbox
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        area = w * h
        return self.chick_label if area < self.area_threshold_px2 else self.hen_label

    def stats(self) -> dict:
        return {
            "area_threshold_px2": self.area_threshold_px2,
            "chick_label": self.chick_label,
            "hen_label": self.hen_label,
        }
