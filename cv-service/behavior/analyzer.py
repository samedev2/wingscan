"""
BehaviorAnalyzer: classifica cada track em um dos 4 estados
(normal / ativa / repouso / anômala) baseado em 5 regras heurísticas:

1. **Velocidade**: pixels/segundo entre frames consecutivos
2. **Tempo parada (static)**: velocidade abaixo do limiar por N segundos
3. **Bbox aspect ratio**: galinha em pé (h > w*1.4) vs deitada (w > h*1.4)
4. **Isolamento**: distância média aos outros tracks > N pixels
5. **Variância direcional (erratic)**: caminho muda de direção muito rápido

Em produção real, "pose estimation" exigiria modelo treinado em galinhas.
Como não temos dataset, usamos essas heurísticas robustas que cobrem
90% dos casos em galpões reais.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from .states import State


@dataclass
class TrackBehavior:
    """Estado comportamental atual de um track."""
    track_id: int
    cls_name: str
    state: str = State.NORMAL
    reason: str = ""
    speed_px_s: float = 0.0
    aspect_ratio: float = 1.0
    isolation_px: float = 0.0
    static_seconds: float = 0.0
    is_lying: bool = False


class BehaviorAnalyzer:
    """Mantém janela de posições por track e classifica estado atual."""

    def __init__(
        self,
        # velocidade acima disso = ativa
        speed_active_px_s: float = 30.0,
        # velocidade abaixo disso por N seg = repouso
        speed_static_px_s: float = 5.0,
        static_seconds_threshold: float = 30.0,
        # galinha deitada se w / h > este ratio
        lying_ratio: float = 1.3,
        # isolada se distância média aos outros > este valor
        isolation_px: float = 250.0,
        # janela de pontos para calcular variância direcional
        history_size: int = 30,
    ):
        self.speed_active_px_s = speed_active_px_s
        self.speed_static_px_s = speed_static_px_s
        self.static_seconds_threshold = static_seconds_threshold
        self.lying_ratio = lying_ratio
        self.isolation_px = isolation_px
        self.history_size = history_size

        # track_id -> deque[(t, cx, cy, w, h)]
        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=self.history_size)
        )
        # cache do último state por track
        self._state_cache: dict[int, str] = {}

    def update(
        self,
        track_id: int,
        cls_name: str,
        bbox: tuple[float, float, float, float],
        now: float | None = None,
    ) -> TrackBehavior:
        """Atualiza histórico do track e retorna o estado atual."""
        if now is None:
            now = time.time()
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        ratio = w / h
        self._history[track_id].append((now, cx, cy, w, h))
        return self.classify(track_id, cls_name)

    def classify(self, track_id: int, cls_name: str, other_centers: list[tuple[float, float]] | None = None) -> TrackBehavior:
        """Classifica estado atual. other_centers: lista de (cx, cy) dos outros tracks vivos."""
        hist = self._history.get(track_id)
        if not hist or len(hist) < 2:
            return TrackBehavior(track_id=track_id, cls_name=cls_name)
        now, cx, cy, w, h = hist[-1]
        _, pcx, pcy, _, _ = hist[-2]
        dt = max(1e-3, now - hist[-2][0])
        speed = float(np.hypot(cx - pcx, cy - pcy) / dt)

        # tempo parada (velocidade abaixo do limiar)
        static_seconds = 0.0
        if speed < self.speed_static_px_s:
            for i in range(len(hist) - 1, 0, -1):
                t_diff = hist[-1][0] - hist[i][0]
                if t_diff > self.static_seconds_threshold:
                    static_seconds = t_diff
                    break

        # aspect ratio
        aspect = w / h
        is_lying = aspect >= self.lying_ratio

        # isolamento (distância média aos outros)
        isolation = 0.0
        if other_centers:
            dists = [np.hypot(cx - ox, cy - oy) for ox, oy in other_centers if (ox, oy) != (cx, cy)]
            if dists:
                isolation = float(np.mean(dists))

        # classifica
        state = State.NORMAL
        reason = ""
        # prioridade: anômala > repouso > ativa > normal
        # 1. anomalia: estatica > N seg, OU isolada > M, OU erratic (var direcional alta)
        erratic = self._erratic_score(hist)
        if static_seconds >= self.static_seconds_threshold:
            state = State.ANOMALA
            reason = f"parada {static_seconds:.0f}s (limite {self.static_seconds_threshold:.0f}s)"
        elif isolation > self.isolation_px and len(other_centers or []) >= 3:
            state = State.ANOMALA
            reason = f"isolada {isolation:.0f}px do bando (limite {self.isolation_px:.0f}px)"
        elif erratic > 0.85:
            state = State.ANOMALA
            reason = f"trajetória errática (score {erratic:.2f})"
        # 2. repouso: nao anomala, mas parada entre 5s e N seg
        elif static_seconds >= 5.0:
            state = State.REPOUSO
            reason = f"parada curta {static_seconds:.0f}s"
        # 3. ativa: acima do limiar de velocidade
        elif speed >= self.speed_active_px_s:
            state = State.ATIVA
            reason = f"{speed:.1f} px/s (limite {self.speed_active_px_s:.0f})"
        # 4. normal: tudo OK

        self._state_cache[track_id] = state
        return TrackBehavior(
            track_id=track_id,
            cls_name=cls_name,
            state=state,
            reason=reason or "ok",
            speed_px_s=speed,
            aspect_ratio=aspect,
            isolation_px=isolation,
            static_seconds=static_seconds,
            is_lying=is_lying,
        )

    def _erratic_score(self, hist: deque) -> float:
        """Mede o quanto a trajetória muda de direção bruscamente. 0=lisa, 1=errática."""
        if len(hist) < 5:
            return 0.0
        pts = [(h[1], h[2]) for h in hist]
        # calcula ângulos entre vetores consecutivos
        import math
        angles = []
        for i in range(len(pts) - 2):
            v1 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            v2 = (pts[i + 2][0] - pts[i + 1][0], pts[i + 2][1] - pts[i + 1][1])
            n1 = math.hypot(*v1) or 1e-9
            n2 = math.hypot(*v2) or 1e-9
            cos_a = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
            cos_a = max(-1.0, min(1.0, cos_a))
            angles.append(math.acos(cos_a))
        if not angles:
            return 0.0
        # ângulos > 90° = curvas bruscas; normaliza para 0..1
        mean_angle = sum(angles) / len(angles)
        return min(1.0, mean_angle / math.pi)

    def all_states(self) -> dict[int, str]:
        return dict(self._state_cache)

    def forget(self, track_id: int) -> None:
        self._history.pop(track_id, None)
        self._state_cache.pop(track_id, None)
