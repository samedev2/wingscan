"""
PaletteHeatmap: "training computacional" (cold wave azul) sobre a regiao
do palete de racao.

- Define uma ROI (region of interest) sobre o cocho/palete.
- A cada frame, conta quantas galinhas estao DENTRO da ROI.
- Soma um blob gaussiano em cada posicao de galinha dentro da ROI
  (intensidade += 0.4). Fora da ROI, nada.
- Decay temporal (default 0.97) faz a onda sumir quando galinhas se afastam.
- Render final: overlay BGR azul com alpha proporcional a intensidade,
  modulado por uma onda senoidal (respiracao visual).
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Tuple

import numpy as np

import cv2


@dataclass
class _Roi:
    """ROI em coordenadas fracionarias [0..1]."""
    x1: float = 0.05
    y1: float = 0.40
    x2: float = 0.95
    y2: float = 0.60

    def to_pixels(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        return (
            int(self.x1 * frame_w),
            int(self.y1 * frame_h),
            int(self.x2 * frame_w),
            int(self.y2 * frame_h),
        )


class PaletteHeatmap:
    """Mapa de calor azul com decaimento + onda senoidal visual.

    Parametros:
      frame_shape: (h, w) do frame de video
      roi:         _Roi com x1/y1/x2/y2 em coords fracionarias
      decay:       quanto da intensidade anterior se mantem por frame (0..1)
      amp:         quanto cada galinha dentro da ROI soma na intensidade
      gauss_radius_px: raio (em px) do blob gaussiano por galinha
      wave_period_frames: periodo da onda senoidal visual (frames)
    """

    def __init__(
        self,
        frame_shape: Tuple[int, int],
        roi: _Roi | None = None,
        decay: float = 0.97,
        amp: float = 0.4,
        gauss_radius_px: int = 70,
        wave_period_frames: int = 90,
    ):
        self.h, self.w = frame_shape
        self.roi = roi or _Roi()
        self.decay = decay
        self.amp = amp
        self.gauss_radius_px = gauss_radius_px
        self.wave_period_frames = wave_period_frames
        self.heat = np.zeros((self.h, self.w), dtype=np.float32)
        self._t0 = time.time()
        self._frame = 0
        # historico: lista de (ts, inside_count)
        self.occupancy: deque = deque(maxlen=600)  # ~10 min @ 60fps
        self.total_in_frames = 0
        self.frames_with_chickens = 0
        self._gauss_cache: dict[int, np.ndarray] = {}

    def set_roi(self, roi: _Roi) -> None:
        self.roi = roi
        # ao trocar a ROI, reseta o heatmap (senao manchas da ROI antiga persistem)
        self.heat.fill(0.0)

    def get_roi(self) -> _Roi:
        return self.roi

    def update(self, tracks) -> int:
        """Atualiza o heatmap com os tracks do frame. Retorna qtd dentro da ROI."""
        # decay temporal
        self.heat *= self.decay

        rx1, ry1, rx2, ry2 = self.roi.to_pixels(self.w, self.h)
        inside = 0
        if tracks is not None and getattr(tracks, "tracker_id", None) is not None and len(tracks) > 0:
            for i in range(len(tracks)):
                tid = tracks.tracker_id[i]
                if tid is None:
                    continue
                x1, y1, x2, y2 = tracks.xyxy[i]
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                if not (rx1 <= cx <= rx2 and ry1 <= cy <= ry2):
                    continue
                # dentro da ROI → adiciona blob gaussiano
                self._stamp(cx, cy)
                inside += 1

        self._frame += 1
        self.occupancy.append((time.time(), inside))
        self.total_in_frames += 1
        if inside > 0:
            self.frames_with_chickens += 1
        return inside

    def _stamp(self, cx: float, cy: float) -> None:
        """Adiciona um blob gaussiano centrado em (cx, cy)."""
        r = self.gauss_radius_px
        # janela com margem
        x0 = max(0, int(cx - r * 1.5))
        x1 = min(self.w, int(cx + r * 1.5) + 1)
        y0 = max(0, int(cy - r * 1.5))
        y1 = min(self.h, int(cy + r * 1.5) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        h_win, w_win = y1 - y0, x1 - x0
        # cache do gauss por tamanho (otimizacao)
        key = (h_win, w_win, r)
        g = self._gauss_cache.get(key)
        if g is None:
            yy, xx = np.mgrid[0:h_win, 0:w_win]
            g = np.exp(-((xx - w_win / 2) ** 2 + (yy - h_win / 2) ** 2) / (2 * r * r)).astype(np.float32)
            self._gauss_cache[key] = g
        self.heat[y0:y1, x0:x1] += g * self.amp
        np.clip(self.heat, 0.0, 1.0, out=self.heat)

    def reset(self) -> None:
        self.heat.fill(0.0)
        self._gauss_cache.clear()
        self.occupancy.clear()
        self.total_in_frames = 0
        self.frames_with_chickens = 0

    def stats(self) -> dict:
        """Estatisticas atuais e historicas."""
        recent = [v for _, v in list(self.occupancy)[-60:]]  # ~1s
        recent_avg = (sum(recent) / len(recent)) if recent else 0.0
        occupancy_pct = (
            100.0 * self.frames_with_chickens / max(1, self.total_in_frames)
        )
        inside_now = recent[-1] if recent else 0
        return {
            "inside_now": inside_now,
            "recent_avg_per_frame": round(recent_avg, 2),
            "occupancy_pct": round(occupancy_pct, 1),
            "total_frames": self.total_in_frames,
            "frames_with_chickens": self.frames_with_chickens,
            "roi": {
                "x1": self.roi.x1, "y1": self.roi.y1,
                "x2": self.roi.x2, "y2": self.roi.y2,
            },
            "wave_intensity": round(self._wave_value(), 3),
        }

    def _wave_value(self) -> float:
        """Onda senoidal 0..1 (respiracao)."""
        if self.wave_period_frames <= 0:
            return 1.0
        phase = (self._frame / self.wave_period_frames) * 2 * math.pi
        return 0.5 + 0.5 * math.sin(phase)

    def render_overlay(self) -> np.ndarray:
        """Retorna imagem BGR (uint8) com a onda azul + alpha para blending."""
        if self.heat.max() <= 0.001:
            return np.zeros((self.h, self.w, 3), dtype=np.uint8)

        # intensidade modulada pela onda (respiracao)
        wave = self._wave_value()
        mod = (self.heat * (0.55 + 0.45 * wave))
        mod = np.clip(mod, 0.0, 1.0)

        # gera overlay BGR: tons de azul (B alto, R baixo, G medio)
        # quanto mais quente, mais brilhante e mais azul puro
        b = (mod * 255).astype(np.uint8)
        g = (mod * 180).astype(np.uint8)
        r = (mod * 60).astype(np.uint8)
        overlay = np.stack([b, g, r], axis=-1)  # BGR
        return overlay

    def render_alpha(self) -> np.ndarray:
        """Mapa alpha float 0..1 para blending na imagem final."""
        wave = self._wave_value()
        return np.clip(self.heat * (0.55 + 0.45 * wave), 0.0, 1.0).astype(np.float32)

    def draw_roi_box(self, frame: np.ndarray) -> None:
        """Desenha o retangulo da ROI em ciano no frame (in-place)."""
        x1, y1, x2, y2 = self.roi.to_pixels(self.w, self.h)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 80), 2)
        cv2.putText(
            frame, f"PALETE ROI ({self.roi.x1:.2f},{self.roi.y1:.2f})-({self.roi.x2:.2f},{self.roi.y2:.2f})",
            (x1 + 4, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 80), 1, cv2.LINE_AA,
        )

    def render(self) -> np.ndarray:
        """Render final: overlay BGR pronto para visualizar."""
        return self.render_overlay()
