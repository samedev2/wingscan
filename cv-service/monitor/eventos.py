"""Barramento de eventos: guarda o log em memória, grava em JSONL e distribui para o front (SSE)."""

import json
import queue
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

TIPOS = ("sistema", "entrada", "comportamento", "alerta")
NIVEIS = ("debug", "info", "aviso", "erro")


class Barramento:
    def __init__(self, pasta_logs: Path, max_historico: int = 2000):
        self._lock = threading.Lock()
        self._assinantes: set[queue.Queue] = set()
        self._historico: deque = deque(maxlen=max_historico)
        self._retidos: dict = {}
        self._seq = 0
        self._pasta = Path(pasta_logs)
        self._pasta.mkdir(parents=True, exist_ok=True)
        self._arquivo = None
        self._data_arquivo = None

    # ---- log -------------------------------------------------------------
    def log(self, tipo: str, nivel: str, mensagem: str, dados: dict | None = None) -> dict:
        agora = datetime.now()
        with self._lock:
            self._seq += 1
            evento = {
                "seq": self._seq,
                "ts": agora.isoformat(timespec="milliseconds"),
                "tipo": tipo,
                "nivel": nivel,
                "msg": mensagem,
                "dados": dados or {},
            }
            self._historico.append(evento)
            self._gravar(agora, evento)
            assinantes = list(self._assinantes)
        self._entregar(assinantes, "log", evento)
        if tipo in ("sistema", "alerta") and nivel != "debug":
            print(f"{agora:%H:%M:%S} [{tipo}/{nivel}] {mensagem}", flush=True)
        return evento

    def _gravar(self, agora: datetime, evento: dict):
        data = agora.date()
        if self._data_arquivo != data:
            if self._arquivo:
                self._arquivo.close()
            caminho = self._pasta / f"eventos-{data:%Y-%m-%d}.jsonl"
            self._arquivo = open(caminho, "a", encoding="utf-8", buffering=1)
            self._data_arquivo = data
        self._arquivo.write(json.dumps(evento, ensure_ascii=False) + "\n")

    def historico(self) -> list[dict]:
        with self._lock:
            return list(self._historico)

    # ---- publicação / assinatura -----------------------------------------
    def publicar(self, nome: str, payload, reter: bool = False):
        """Envia um evento que não entra no log (status, estado do quadro)."""
        with self._lock:
            if reter:
                self._retidos[nome] = payload
            assinantes = list(self._assinantes)
        self._entregar(assinantes, nome, payload)

    def _entregar(self, assinantes, nome, payload):
        for fila in assinantes:
            # Quadros de vídeo são descartáveis: se o cliente está atrasado, pula.
            if nome == "estado" and fila.qsize() > 20:
                continue
            try:
                fila.put_nowait((nome, payload))
            except queue.Full:
                pass

    def assinar(self, qtd_historico: int = 400):
        fila: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._assinantes.add(fila)
            historico = list(self._historico)[-qtd_historico:]
            retidos = dict(self._retidos)
        return fila, historico, retidos

    def cancelar(self, fila: queue.Queue):
        with self._lock:
            self._assinantes.discard(fila)

    def fechar(self):
        with self._lock:
            if self._arquivo:
                self._arquivo.close()
                self._arquivo = None
                self._data_arquivo = None
