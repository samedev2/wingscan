"""
Persistência global de labels: classe nomeada → lista de embeddings.

O usuário pode renomear "Item3" para "galinha" — todos os embeddings existentes
daquela classe passam a ser da nova classe. O arquivo é gravado atomicamente
para evitar corrupção em queda de energia.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

import numpy as np


class LabelsStore:
    """
    Estrutura em disco (data/labels.json):
    {
      "galinha": {
        "embeddings": [[...], [...]],
        "created_at": "2026-09-15T12:00:00"
      },
      "caixa": {
        "embeddings": [[...]],
        "created_at": "2026-09-15T12:01:00"
      }
    }
    """

    def __init__(self, path: str = "data/labels.json"):
        self.path = Path(path)
        self.lock = Lock()
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            for name, info in raw.items():
                self.data[name] = {
                    "embeddings": [
                        np.array(e, dtype=np.float32)
                        for e in info.get("embeddings", [])
                    ],
                    "created_at": info.get("created_at"),
                }
            print(f"[labels] carregadas {len(self.data)} classes de {self.path}")
        except Exception as e:
            print(f"[labels] falha ao carregar {self.path}: {e}")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            name: {
                "embeddings": [emb.tolist() for emb in info["embeddings"]],
                "created_at": info.get("created_at"),
            }
            for name, info in self.data.items()
        }
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    def list_names(self) -> list[str]:
        with self.lock:
            return sorted(self.data.keys())

    def summary(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "name": name,
                    "samples": len(info["embeddings"]),
                    "created_at": info.get("created_at"),
                }
                for name, info in sorted(self.data.items())
            ]

    def has(self, name: str) -> bool:
        with self.lock:
            return name in self.data

    def add_embedding(self, name: str, embedding: np.ndarray) -> None:
        with self.lock:
            if name not in self.data:
                self.data[name] = {
                    "embeddings": [],
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            self.data[name]["embeddings"].append(embedding)
            self._save()

    def rename(self, old: str, new: str) -> bool:
        with self.lock:
            if old not in self.data or new == old:
                return False
            if new in self.data:
                # Faz merge: junta embeddings
                self.data[new]["embeddings"].extend(self.data[old]["embeddings"])
                del self.data[old]
            else:
                self.data[new] = self.data.pop(old)
            self._save()
            return True

    def remove(self, name: str) -> bool:
        with self.lock:
            if name not in self.data:
                return False
            del self.data[name]
            self._save()
            return True

    def get_all(self) -> dict[str, list[np.ndarray]]:
        with self.lock:
            return {name: list(info["embeddings"]) for name, info in self.data.items()}
