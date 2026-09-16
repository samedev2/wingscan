"""
Estados comportamentais possíveis por track (galinha/ave/item).

Cada estado tem cor BGR (OpenCV) para o bounding box + cor CSS para o front.
"""
from __future__ import annotations

from dataclasses import dataclass


class State:
    NORMAL = "normal"
    ATIVA = "ativa"
    REPOUSO = "repouso"
    ANOMALA = "anomala"


# BGR (OpenCV) para o bounding box desenhado no JPEG anotado
STATE_COLOR_BGR = {
    State.NORMAL: (60, 220, 60),    # verde vivo
    State.ATIVA: (50, 165, 245),   # laranja/azul claro
    State.REPOUSO: (180, 180, 180),# cinza
    State.ANOMALA: (60, 60, 230),   # vermelho
}

# CSS para o front (canvas overlays, painel lateral)
STATE_COLOR_CSS = {
    State.NORMAL: "#3fb950",
    State.ATIVA: "#f59e0b",
    State.REPOUSO: "#9ca3af",
    State.ANOMALA: "#f85149",
}


@dataclass
class BehaviorMetrics:
    """Métricas agregadas do turno."""
    total: int
    ativos: int
    repouso: int
    anomalo: int
    normal: int

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "normal": self.normal,
            "ativa": self.ativos,
            "repouso": self.repouso,
            "anomalo": self.anomalo,
        }
