"""
Repositório de dados: settings, labels, paths, events.
Encapsula SQL e expõe API Python-friendly.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

from panel.db import Database


class PanelRepo:
    """Camada de acesso ao SQLite para o painel de controle."""

    def __init__(self, db: Database):
        self.db = db

    # ----- settings (key/value) -----

    def get_settings(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT key, value FROM panel_settings").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                result[row["key"]] = row["value"]
        return result

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO panel_settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, payload, now),
        )
        self.db.commit()

    def set_settings_bulk(self, settings: dict[str, Any]) -> None:
        for k, v in settings.items():
            self.set_setting(k, v)

    # ----- labels -----

    def list_labels(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT name, samples, fixed, created_at FROM labels ORDER BY name"
        ).fetchall()
        return [
            {
                "name": row["name"],
                "samples": int(row["samples"]),
                "fixed": bool(row["fixed"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_label_embeddings(self, name: str) -> list[np.ndarray] | None:
        row = self.db.execute(
            "SELECT embeddings_json FROM labels WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["embeddings_json"])
            return [np.array(e, dtype=np.float32) for e in data]
        except (json.JSONDecodeError, TypeError):
            return []

    def upsert_label(
        self,
        name: str,
        embeddings: list[np.ndarray],
        *,
        fixed: bool = True,
    ) -> None:
        """Cria ou atualiza uma classe. Se já existir, faz merge dos embeddings."""
        existing = self.get_label_embeddings(name) or []
        merged = existing + [e for e in embeddings if e is not None]
        merged_payload = json.dumps([e.tolist() for e in merged])
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO labels(name, embeddings_json, samples, fixed, created_at) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "embeddings_json=excluded.embeddings_json, "
            "samples=excluded.samples, fixed=excluded.fixed",
            (name, merged_payload, len(merged), 1 if fixed else 0, now),
        )
        self.db.commit()

    def rename_label(self, old: str, new: str) -> bool:
        if old == new:
            return False
        if self.get_label_embeddings(new) is not None:
            # Merge: embeddings existentes + antigos
            old_emb = self.get_label_embeddings(old) or []
            new_emb = self.get_label_embeddings(new) or []
            self.upsert_label(new, old_emb + new_emb)
            self.db.execute("DELETE FROM labels WHERE name = ?", (old,))
            self.db.commit()
            return True
        self.db.execute("UPDATE labels SET name = ? WHERE name = ?", (new, old))
        self.db.commit()
        return True

    def delete_label(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM labels WHERE name = ?", (name,))
        self.db.commit()
        return cur.rowcount > 0

    def set_fixed(self, name: str, fixed: bool) -> bool:
        cur = self.db.execute(
            "UPDATE labels SET fixed = ? WHERE name = ?", (1 if fixed else 0, name)
        )
        self.db.commit()
        return cur.rowcount > 0

    def is_known_name(self, name: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM labels WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return row is not None

    # ----- paths (trilhas) -----

    def upsert_path(self, track_id: int, cls_name: str, points: list[tuple[float, float, float]]) -> None:
        """points: lista de (x, y, timestamp_seconds)."""
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps([[round(x, 2), round(y, 2), round(t, 3)] for (x, y, t) in points])
        self.db.execute(
            "INSERT INTO paths(track_id, cls_name, points_json, last_update) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET "
            "cls_name=excluded.cls_name, points_json=excluded.points_json, last_update=excluded.last_update",
            (track_id, cls_name, payload, now),
        )
        self.db.commit()

    def delete_path(self, track_id: int) -> None:
        self.db.execute("DELETE FROM paths WHERE track_id = ?", (track_id,))
        self.db.commit()

    def list_paths(self, max_age_seconds: float | None = 30.0) -> list[dict]:
        """Retorna trilhas ativas (atualizadas recentemente)."""
        rows = self.db.execute(
            "SELECT track_id, cls_name, points_json, last_update FROM paths ORDER BY last_update DESC"
        ).fetchall()
        result = []
        for row in rows:
            try:
                points = json.loads(row["points_json"])
                result.append({
                    "track_id": int(row["track_id"]),
                    "cls_name": row["cls_name"],
                    "points": points,
                    "last_update": row["last_update"],
                })
            except (json.JSONDecodeError, TypeError):
                continue
        return result

    def prune_old_paths(self, max_age_seconds: float = 30.0) -> int:
        """Remove trilhas sem update há mais de N segundos (track sumiu do frame)."""
        cutoff = datetime.now().timestamp() - max_age_seconds
        rows = self.db.execute("SELECT track_id, last_update FROM paths").fetchall()
        removed = 0
        for row in rows:
            try:
                ts = datetime.fromisoformat(row["last_update"]).timestamp()
                if ts < cutoff:
                    self.db.execute("DELETE FROM paths WHERE track_id = ?", (row["track_id"],))
                    removed += 1
            except (ValueError, TypeError):
                continue
        if removed:
            self.db.commit()
        return removed

    # ----- events (log) -----

    def log_event(self, kind: str, payload: dict | None = None) -> None:
        now = datetime.now().isoformat(timespec="milliseconds")
        self.db.execute(
            "INSERT INTO panel_events(ts, kind, payload) VALUES(?, ?, ?)",
            (now, kind, json.dumps(payload or {})),
        )
        self.db.commit()

    def recent_events(self, limit: int = 50, kind_filter: str | None = None) -> list[dict]:
        """Ultimos N eventos, mais recentes primeiro. kind_filter opcional."""
        if kind_filter:
            rows = self.db.query(
                "SELECT ts, kind, payload FROM panel_events WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind_filter, int(limit)),
            )
        else:
            rows = self.db.query(
                "SELECT ts, kind, payload FROM panel_events ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
        out: list[dict] = []
        for ts, kind, payload in rows:
            try:
                p = json.loads(payload) if payload else {}
            except Exception:
                p = {"raw": payload}
            out.append({"ts": ts, "kind": kind, "payload": p})
        return out
