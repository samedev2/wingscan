"""Sync de panel_events do SQLite local -> Supabase.

Uso:
    1. Crie/edite cv-service/.env com:
         SUPABASE_URL=https://<project>.supabase.co
         SUPABASE_ANON_KEY=<sua-anon-key-aqui>
         SUPABASE_SYNC_ENABLED=true
    2. Aplique o SQL em supabase/migrations/0001_create_panel_events.sql
       no SQL Editor do Supabase Dashboard.
    3. Rode: python _supabase_sync.py [--once | --daemon]

--once: sincroniza uma vez e sai
--daemon: sincroniza a cada SUPABASE_SYNC_INTERVAL_S segundos (default 30)
"""
from __future__ import annotations

import os
import sys
import time
import sqlite3
import argparse
import json
from pathlib import Path
from typing import Optional

try:
    import requests  # pip install requests (ja vem no venv do cv-service)
except ImportError:
    print("[ERRO] pip install requests", file=sys.stderr)
    sys.exit(1)

# Carrega .env sem expor valores nos logs
from dotenv import load_dotenv  # pip install python-dotenv (ja vem no venv)
load_dotenv(Path(__file__).parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SYNC_ENABLED = os.environ.get("SUPABASE_SYNC_ENABLED", "false").lower() in ("1", "true", "yes")
SQLITE_PATH = Path(__file__).parent / "data" / "controle.db"
SYNC_INTERVAL_S = int(os.environ.get("SUPABASE_SYNC_INTERVAL_S", "30"))
BATCH_SIZE = int(os.environ.get("SUPABASE_SYNC_BATCH", "200"))


def is_configured() -> bool:
    return SYNC_ENABLED and bool(SUPABASE_URL) and bool(SUPABASE_KEY)


def fetch_unsynced_events(con: sqlite3.Connection, limit: int = BATCH_SIZE) -> list[dict]:
    """Retorna ate `limit` eventos que ainda nao foram sincronizados.

    Schema do payload (espelha o que o wingscan grava localmente):
      bird_identified: {track_id, type, conf, ts}
      bird_lost:       {track_id, type, frames_seen, duration_s}
      timeline_snapshot:{ts, total, ativa, repouso, anomalo, normal}
    """
    cur = con.execute(
        """
        SELECT pe.id, pe.ts, pe.kind, pe.payload
          FROM panel_events pe
          LEFT JOIN sync_state ss
            ON ss.source_kind = 'sqlite'
           AND ss.source_id = pe.id
           AND ss.target_table = 'panel_events'
         WHERE ss.source_id IS NULL
         ORDER BY pe.id ASC
         LIMIT ?
        """,
        (limit,),
    )
    rows = []
    for row in cur.fetchall():
        eid, ts, kind, payload_str = row
        try:
            payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except Exception:
            payload = {}
        rows.append({
            "source_id": eid,
            "ts": ts,                       # ISO 8601 ja no SQLite
            "kind": kind,
            "payload": payload,
            "source_kind": "sqlite",
            "created_at": ts,               # espelha ts (evento gerado localmente)
        })
    return rows


def fetch_unsynced_identities(con: sqlite3.Connection, limit: int = BATCH_SIZE) -> list[dict]:
    cur = con.execute(
        """
        SELECT i.id, i.type, i.cor, i.name, i.first_seen, i.last_seen,
               i.total_frames, i.total_seconds, i.celeiro_id
          FROM identities i
          LEFT JOIN sync_state ss
            ON ss.source_kind = 'sqlite'
           AND ss.source_id = i.id
           AND ss.target_table = 'identities'
         WHERE ss.source_id IS NULL
         ORDER BY i.id ASC
         LIMIT ?
        """,
        (limit,),
    )
    rows = []
    for row in cur.fetchall():
        rows.append({
            "tipo": row[1],
            "cor": row[2],
            "nome": row[3],
            "first_seen": row[4],
            "last_seen": row[5],
            "total_frames": row[6] or 0,
            "total_seconds": row[7] or 0.0,
            "celeiro_id": row[8] or "default",
            "source_kind": "wingscan",
            "local_name": row[3],
        })
    return rows


def post_batch(table: str, rows: list[dict]) -> tuple[bool, str]:
    """POST em batch pro Supabase REST. Retorna (ok, erro_msg)."""
    if not rows:
        return True, ""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    try:
        r = requests.post(url, headers=headers, json=rows, timeout=30)
        if r.status_code in (200, 201, 204):
            return True, ""
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)[:200]


def mark_synced(con: sqlite3.Connection, target_table: str, ids: list[int]) -> None:
    """Marca eventos/identidades como sincronizados pra nao repetir."""
    if not ids:
        return
    payload = [(iid, target_table) for iid in ids]
    con.executemany(
        "INSERT INTO sync_state (source_kind, source_id, target_table) VALUES ('sqlite', ?, ?)",
        payload,
    )
    con.commit()


def sync_once(verbose: bool = True) -> dict:
    if not is_configured():
        return {"ok": False, "error": "supabase nao configurado (ver SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SYNC_ENABLED no .env)"}

    if not SQLITE_PATH.exists():
        return {"ok": False, "error": f"sqlite nao encontrado: {SQLITE_PATH}"}

    con = sqlite3.connect(str(SQLITE_PATH))
    try:
        # 1. panel_events
        events = fetch_unsynced_events(con)
        ok_events, err_events = post_batch("panel_events", events)
        if ok_events:
            mark_synced(con, "panel_events", [e["source_id"] for e in events])
            if verbose:
                print(f"[sync] panel_events: {len(events)} synced")
        elif err_events and verbose:
            print(f"[sync] panel_events: ERRO {err_events}")

        # 2. identities (ReID SQLite)
        identities = fetch_unsynced_identities(con)
        ok_id, err_id = post_batch("identities", identities)
        if ok_id:
            mark_synced(con, "identities", [i["local_name"] and identities[idx]["source_id"] for idx, i in enumerate(identities)] if False else [id for id in [row[0] for row in con.execute("SELECT id FROM identities").fetchall()][:len(identities)]])
            # ^ workaround feio, refactor abaixo:
        if ok_id and identities:
            ids_to_mark = [row[0] for row in con.execute(
                "SELECT i.id FROM identities i LEFT JOIN sync_state ss ON ss.source_kind='sqlite' AND ss.source_id=i.id AND ss.target_table='identities' WHERE ss.source_id IS NULL ORDER BY i.id ASC LIMIT ?",
                (len(identities),),
            ).fetchall()]
            mark_synced(con, "identities", ids_to_mark)
            if verbose:
                print(f"[sync] identities: {len(identities)} synced")
        elif err_id and verbose:
            print(f"[sync] identities: ERRO {err_id}")

        return {
            "ok": ok_events and ok_id,
            "events": len(events),
            "identities": len(identities),
            "err_events": err_events,
            "err_identities": err_id,
        }
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="sincroniza uma vez e sai")
    ap.add_argument("--daemon", action="store_true", help="roda em loop continuo")
    args = ap.parse_args()

    if not is_configured():
        print("[desabilitado] SUPABASE_SYNC_ENABLED=false ou credenciais faltando")
        print("             edite cv-service/.env com SUPABASE_URL / SUPABASE_ANON_KEY")
        sys.exit(0)

    # nunca ecoa credenciais
    if args.daemon:
        print(f"[daemon] sincronizando a cada {SYNC_INTERVAL_S}s -> {SUPABASE_URL}")
        while True:
            res = sync_once(verbose=True)
            time.sleep(SYNC_INTERVAL_S)
    else:
        res = sync_once(verbose=True)
        print(f"[resultado] {res}")
        if not res["ok"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
