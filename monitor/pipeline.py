"""Orquestra fonte → detector → análise → eventos para o front."""

import base64
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from .analise import COMPORTAMENTOS, Analisador, formatar_duracao
from .detector import DetectorYOLO
from .eventos import Barramento
from .fontes import ErroFonte, FonteDemo, FonteVideo


class Monitor:
    def __init__(self, cfg: dict, raiz: Path, barramento: Barramento):
        self.cfg = cfg
        self.raiz = raiz
        self.bus = barramento
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self.status = {"rodando": False, "fonte": None, "tipo": None, "desde": None}
        self._publicar_status()

    # ---- controle -------------------------------------------------------------
    def iniciar(self, tipo: str, origem=None):
        with self._lock:
            self._encerrar_thread()
            self._parar = threading.Event()
            self._thread = threading.Thread(target=self._executar, args=(tipo, origem, self._parar),
                                            name="monitor", daemon=True)
            self._thread.start()

    def parar(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.bus.log("sistema", "info", "Parada solicitada pelo usuário")
            self._encerrar_thread()

    def _encerrar_thread(self):
        if self._thread and self._thread.is_alive():
            self._parar.set()
            self._thread.join(timeout=10)
        self._thread = None

    def _publicar_status(self, **mudancas):
        self.status.update(mudancas)
        self.bus.publicar("status", dict(self.status), reter=True)

    # ---- laço principal ------------------------------------------------------------
    def _executar(self, tipo: str, origem, parar: threading.Event):
        cfg = self.cfg
        limites = dict(cfg["limites"])
        try:
            self.bus.log("sistema", "info", f"Iniciando monitoramento (fonte: {tipo})")
            detector = identidade = None
            if tipo == "demo":
                fonte = FonteDemo(cfg["demo"], cfg["zonas"], self.bus)
                limites.update(cfg["demo"].get("limites", {}))
                self.bus.log("sistema", "info",
                             "Modo demo usa limites curtos para os alertas aparecerem rápido: "
                             + ", ".join(f"{k.removesuffix('_s').replace('_', ' ')} > {formatar_duracao(v)}"
                                         for k, v in limites.items()), limites)
            else:
                fonte = FonteVideo(tipo, origem, cfg["video"], self.bus)
                detector = DetectorYOLO(cfg["modelos"], self.raiz, self.bus)
                r = cfg.get("rastreio", {})
                if r.get("identidade_persistente", True):
                    from .identidade import IdentidadePersistente  # usa numpy/scipy: só no modo real

                    identidade = IdentidadePersistente(float(r.get("raio_base", 0.08)), float(r.get("raio_por_segundo", 0.15)),
                                                       populacao=r.get("populacao") or {})
                    self.bus.log("sistema", "info",
                                 "Identidade persistente ativa: IDs novos do rastreador são ligados à ave perdida mais próxima"
                                 + (f" (população fixa: {r['populacao']})" if r.get("populacao") else ""), r)

            analisador = Analisador(cfg, limites, self.bus)
            self._publicar_status(rodando=True, fonte=fonte.descricao, tipo=tipo,
                                  desde=datetime.now().isoformat(timespec="seconds"))
            self._laco(fonte, detector, identidade, analisador, parar)
        except ErroFonte as e:
            self.bus.log("sistema", "erro", str(e))
        except Exception as e:  # noqa: BLE001 - qualquer falha precisa chegar ao log do front
            self.bus.log("sistema", "erro", f"Falha inesperada: {e.__class__.__name__}: {e}",
                         {"traceback": traceback.format_exc()})
        finally:
            self._publicar_status(rodando=False)
            self.bus.log("sistema", "info", "Monitoramento parado")

    def _laco(self, fonte, detector, identidade, analisador, parar):
        cv = self.cfg["video"]
        a = self.cfg["analise"]
        intervalo_envio = 1.0 / float(cv.get("max_fps_envio", 8))
        intervalo_log = float(a.get("log_entrada_a_cada_s", 2))
        ultimo_envio = ultimo_log = 0.0
        anterior = None
        fps = 0.0
        frames = 0

        for amostra in fonte.amostras(parar):
            agora = time.monotonic()
            if anterior is not None and agora > anterior:
                fps = 0.85 * fps + 0.15 * (1.0 / (agora - anterior)) if fps else 1.0 / (agora - anterior)
            anterior = agora
            frames += 1

            rastreio = None
            if amostra.deteccoes is None:
                amostra.deteccoes = detector.detectar(amostra.imagem)
                if identidade:
                    dets = identidade.atualizar(amostra.t, amostra.deteccoes, amostra.largura, amostra.altura)
                    amostra.deteccoes = [d for d in dets if d.id is not None]
                    rastreio = identidade.resumo(amostra.t)
            inferencia_ms = detector.ultima_inferencia_ms if detector else 0.0
            resumo = analisador.atualizar(amostra.t, amostra.deteccoes)

            if agora - ultimo_log >= intervalo_log:
                ultimo_log = agora
                self._log_entrada(amostra, resumo, fps, inferencia_ms)

            if agora - ultimo_envio >= intervalo_envio:
                ultimo_envio = agora
                self.bus.publicar("estado", {
                    "t": round(amostra.t, 2),
                    "frame": amostra.indice,
                    "fps": round(fps, 1),
                    "inferencia_ms": round(inferencia_ms, 1),
                    "dim": [amostra.largura, amostra.altura],
                    "imagem": self._codificar(amostra.imagem) if amostra.imagem is not None else None,
                    "deteccoes": [
                        {"id": d.id, "caixa": [round(v, 4) for v in d.caixa], "conf": d.conf,
                         "classe": d.classe, "rotulo": d.rotulo, "comportamento": d.comportamento}
                        for d in amostra.deteccoes
                    ],
                    "rastreio": rastreio,
                    **resumo,
                })
        self.bus.log("sistema", "debug", f"Fonte encerrada após {frames} frames processados")

    def _log_entrada(self, amostra, resumo, fps, inferencia_ms):
        partes = [f"{c} {n}" for c, n in resumo["por_comportamento"].items() if n]
        minutos, segundos = divmod(amostra.t, 60)
        classes = ", ".join(f"{n} {c}" for c, n in resumo["por_classe"].items()) or "0 aves"
        msg = (f"Frame {amostra.indice} | t={int(minutos):02d}:{segundos:04.1f} | "
               f"{classes}" + (f" ({', '.join(partes)})" if partes else "")
               + f" | {fps:.1f} fps")
        if inferencia_ms:
            msg += f" | inferência {inferencia_ms:.0f} ms"
        self.bus.log("entrada", "info", msg, {
            "frame": amostra.indice,
            "t": round(amostra.t, 2),
            "resolucao": f"{amostra.largura}x{amostra.altura}",
            "detectados": resumo["detectados"],
            "por_classe": resumo["por_classe"],
            "por_comportamento": {c: resumo["por_comportamento"][c] for c in COMPORTAMENTOS},
            "fps": round(fps, 1),
            "inferencia_ms": round(inferencia_ms, 1),
            "deteccoes": [{"id": d.id, "classe": d.classe, "rotulo": d.rotulo, "conf": d.conf}
                          for d in amostra.deteccoes],
        })

    def _codificar(self, imagem) -> str | None:
        import cv2

        cv = self.cfg["video"]
        largura_max = int(cv.get("largura_envio", 960))
        h, w = imagem.shape[:2]
        if w > largura_max:
            imagem = cv2.resize(imagem, (largura_max, int(h * largura_max / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, int(cv.get("qualidade_jpeg", 70))])
        return base64.b64encode(buf).decode("ascii") if ok else None
