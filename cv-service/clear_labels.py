"""Limpa a tabela labels (mantem settings, paths, events)."""
import sqlite3, os, sys
db = r"C:\Users\Administrador\Downloads\PROJETOS CONSELHO TECH\wingscam\cv-service\data\controle.db"
print(f"banco: {db}")
print(f"existe: {os.path.exists(db)}")
print(f"tamanho: {os.path.getsize(db)}")
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tabelas:", [r[0] for r in cur.fetchall()])
for t in ["labels","panel_settings","paths","panel_events"]:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]} rows")
    except Exception as e:
        print(f"  {t}: ERR {e}")
cur.execute("DELETE FROM labels")
print(f"labels removidas: {cur.rowcount}")
con.commit()
con.close()
print("OK")
