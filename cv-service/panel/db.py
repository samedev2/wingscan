"""
SQLite + schema. Camada fina de baixo nível — só abre conexão e cria tabelas.
Toda a lógica de negócio fica em repo.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS panel_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    name TEXT PRIMARY KEY,
    embeddings_json TEXT NOT NULL,
    samples INTEGER NOT NULL DEFAULT 1,
    fixed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paths (
    track_id INTEGER PRIMARY KEY,
    cls_name TEXT NOT NULL,
    points_json TEXT NOT NULL,
    last_update TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS panel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_paths_cls ON paths(cls_name);
CREATE INDEX IF NOT EXISTS idx_paths_update ON paths(last_update);
"""


class Database:
    def __init__(self, path: str = "data/controle.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; usamos transações explícitas quando preciso
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database não conectada — chame connect() primeiro")
        return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_list)

    def executescript(self, sql: str) -> None:
        self.conn.executescript(sql)

    def query(self, sql: str, params: tuple = ()) -> list:
        """SELECT helper: executa e retorna todas as linhas como lista de tuplas."""
        cur = self.conn.execute(sql, params)
        return cur.fetchall()

    def commit(self) -> None:
        self.conn.commit()
