"""
Heatmap por classe. Acumula gaussianas nos centros dos bboxes ao longo do turno,
com decay temporal opcional. Renderiza PNG colorizado por classe.
"""
from __future__ import annotations

import time
from typing import Iterable

import cv2
import numpy as np


# Paleta fixa por classe (BGR para OpenCV)
_PALETTE_BGR = {
    "default": (80, 80, 255),     # vermelho
    "galinha": (0, 200, 255),     # amarelo
    "pessoa": (255, 120, 80),     # azul claro
    "caixa": (200, 200, 0),       # ciano
    "item": (180, 80, 220),       # rosa
}


def _palette_color(cls_name: str) -> tuple[int, int, int]:
    cls = cls_name.lower()
    if cls in _PALETTE_BGR:
        return _PALETTE_BGR[cls]
    # Hash determinístico → cor estável por classe desconhecida
    h = abs(hash(cls)) % 180
    return (h, (h * 7) % 255, 255 - h)


class Heatmap:
    """Acumulador de heatmap por classe, com decay gaussiano."""

    def __init__(self, frame_shape: tuple[int, int], kernel_radius: int = 12, decay: float = 0.985):
        """
        frame_shape: (H, W) do frame.
        kernel_radius: raio da gaussiana em pixels.
        decay: multiplicador aplicado por frame (1.0 = sem decay, 0.95 = rápido).
        """
        self.h, self.w = frame_shape
        self.kernel_radius = kernel_radius
        self.decay = decay
        # dict de classe -> matriz float32 [H, W]
        self.maps: dict[str, np.ndarray] = {}
        # pré-computa kernel gaussiano
        r = kernel_radius
        k = 2 * r + 1
        ax = np.arange(-r, r + 1, dtype=np.float32)
        xx, yy = np.meshgrid(ax, ax)
        self._kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * (r / 2.0) ** 2 + 1e-6))
        self._kernel /= self._kernel.max()  # normaliza para [0, 1]

    def add(self, cls_name: str, point: tuple[int, int], weight: float = 1.0) -> None:
        """Acumula gaussiana no ponto (x, y) na classe."""
        if cls_name not in self.maps:
            self.maps[cls_name] = np.zeros((self.h, self.w), dtype=np.float32)
        m = self.maps[cls_name]
        # decay global leve
        if self.decay < 1.0:
            m *= self.decay
        x, y = point
        r = self.kernel_radius
        x0 = max(0, x - r)
        y0 = max(0, y - r)
        x1 = min(self.w, x + r + 1)
        y1 = min(self.h, y + r + 1)
        kx0 = x0 - (x - r)
        ky0 = y0 - (y - r)
        kx1 = kx0 + (x1 - x0)
        ky1 = ky0 + (y1 - y0)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] += weight * self._kernel[ky0:ky1, kx0:kx1]

    def reset(self, cls_name: str | None = None) -> None:
        if cls_name is None:
            self.maps.clear()
        elif cls_name in self.maps:
            self.maps[cls_name] = np.zeros((self.h, self.w), dtype=np.float32)

    def render(
        self,
        cls_name: str | None = None,
        alpha: float = 0.55,
    ) -> np.ndarray:
        """
        Renderiza heatmap em BGR (OpenCV) sobre fundo preto.
        Se cls_name=None, mistura todas as classes.
        """
        out = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        if cls_name is not None:
            classes = [cls_name] if cls_name in self.maps else []
        else:
            classes = list(self.maps.keys())
        for cls in classes:
            m = self.maps[cls]
            if m.max() <= 0:
                continue
            # normaliza 0..1 por classe
            norm = (m / m.max() * 255).astype(np.uint8)
            # aplica colormap JET-like (mas usa cor da classe como tinte)
            color = _palette_color(cls)
            tinted = np.zeros_like(out)
            tinted[..., 0] = (norm * (color[0] / 255.0)).astype(np.uint8)
            tinted[..., 1] = (norm * (color[1] / 255.0)).astype(np.uint8)
            tinted[..., 2] = (norm * (color[2] / 255.0)).astype(np.uint8)
            # alpha blend aditivo
            mask = norm > 0
            out[mask] = cv2.addWeighted(out[mask], 1.0, tinted[mask], alpha, 0)
        return out

    def classes(self) -> list[str]:
        return list(self.maps.keys())
