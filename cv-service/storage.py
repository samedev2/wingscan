"""Grava eventos do turno em CSV + JSON."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from counter import CrossingEvent


class Storage:
    """
    Cada turno gera dois arquivos em `turno_dir`:
      - <turno_id>.csv  (uma linha por evento)
      - <turno_id>.json (metadados do turno + lista de eventos)
    """

    def __init__(self, turno_dir: str, camera_id: str):
        self.turno_dir = Path(turno_dir)
        self.camera_id = camera_id
        self.turno_id = self._novo_turno_id()
        self.csv_path = self.turno_dir / f"{self.turno_id}.csv"
        self.json_path = self.turno_dir / f"{self.turno_id}.json"
        self.eventos: list[dict] = []
        self.inicio: str | None = None
        self.fim: str | None = None

    def _novo_turno_id(self) -> str:
        return datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{self.camera_id}"

    def iniciar_turno(self) -> None:
        self.turno_dir.mkdir(parents=True, exist_ok=True)
        self.inicio = datetime.now().isoformat(timespec="seconds")
        self.fim = None
        # Cria CSV com header
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["timestamp", "track_id", "classe", "direcao", "conf"]
            )
        self._salvar_json()

    def registrar_evento(self, ev: CrossingEvent) -> None:
        ts_iso = datetime.fromtimestamp(ev.ts).isoformat(timespec="milliseconds")
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                ts_iso,
                ev.track_id,
                ev.cls_name,
                ev.direction,
                f"{ev.conf:.3f}",
            ])
        self.eventos.append({
            "timestamp": ts_iso,
            "track_id": ev.track_id,
            "classe": ev.cls_name,
            "direcao": ev.direction,
            "conf": round(ev.conf, 3),
        })
        # JSON fica meio pesado se atualizamos a cada evento — para MVP tudo bem
        self._salvar_json()

    def _salvar_json(self) -> None:
        data = {
            "turno": self.turno_id,
            "camera_id": self.camera_id,
            "inicio": self.inicio,
            "fim": self.fim,
            "csv_path": str(self.csv_path.name),
            "total_eventos": len(self.eventos),
            "eventos": self.eventos,
        }
        with self.json_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def finalizar_turno(self) -> None:
        self.fim = datetime.now().isoformat(timespec="seconds")
        self._salvar_json()
