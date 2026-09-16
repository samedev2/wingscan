"""Fontes de entrada: simulação (demo) e vídeo real (arquivo, webcam, RTSP)."""

import math
import random
import re
import threading
import time
from pathlib import Path

from .analise import Amostra, Deteccao
from .eventos import Barramento


class ErroFonte(Exception):
    """Erro de configuração/abertura que deve aparecer no log para o usuário."""


# ---------------------------------------------------------------------------
# Demo: pintos simulados que imitam a saída dos modelos YOLO
# ---------------------------------------------------------------------------

# Nomes iguais aos das classes do modelo de comportamento do chicken-detector (best_seg.pt).
ROTULO_MODELO = {
    "comer": "feeding",
    "beber": "drinking",
    "andar": "walking",
    "descansar": "sleeping chickens",
    "bicar": "pecking",
    "panico": "running",
}

PESOS_SAUDAVEL = {"comer": 0.30, "beber": 0.25, "andar": 0.22, "descansar": 0.12, "bicar": 0.11}
PESOS_DOENTE = {"descansar": 0.80, "andar": 0.10, "beber": 0.07, "comer": 0.03}


def _ponto_em(ret, margem=0.02):
    return (random.uniform(ret[0] + margem, ret[2] - margem), random.uniform(ret[1] + margem, ret[3] - margem))


class PintoSimulado:
    def __init__(self, pid: int, doente: bool, zonas: dict):
        self.id = pid
        self.doente = doente
        self.zonas = zonas
        self.x, self.y = random.uniform(0.35, 0.65), random.uniform(0.35, 0.65)
        self.tamanho = random.uniform(0.05, 0.065)
        self.vel = random.uniform(0.04, 0.06) if doente else random.uniform(0.10, 0.15)
        self.atividade = "andar"
        self.alvo = _ponto_em((0.05, 0.05, 0.95, 0.95))
        self.chegou = False
        self.fim = math.inf
        self.direcao = (0.0, 0.0)

    def _duracao(self, atividade):
        faixas = {
            "comer": (6, 14), "beber": (3, 7), "bicar": (3, 7), "panico": (3, 5),
            "descansar": (25, 70) if self.doente else (5, 15),
        }
        return random.uniform(*faixas.get(atividade, (5, 10)))

    def escolher(self, t):
        pesos = PESOS_DOENTE if self.doente else PESOS_SAUDAVEL
        self.atividade = random.choices(list(pesos), weights=list(pesos.values()))[0]
        self.chegou = False
        self.alvo = None
        self.fim = math.inf
        if self.atividade == "comer":
            self.alvo = _ponto_em(random.choice(self.zonas["comedouro"]))
        elif self.atividade == "beber":
            self.alvo = _ponto_em(random.choice(self.zonas["bebedouro"]))
        elif self.atividade == "andar":
            self.alvo = _ponto_em((0.05, 0.05, 0.95, 0.95))
        else:
            self.chegou = True
            self.fim = t + self._duracao(self.atividade)

    def entrar_em_panico(self, t):
        if self.doente:
            return
        ang = random.uniform(0, 2 * math.pi)
        self.direcao = (math.cos(ang), math.sin(ang))
        self.atividade, self.chegou, self.alvo = "panico", True, None
        self.fim = t + self._duracao("panico")

    def passo(self, t, dt):
        if self.atividade == "panico":
            v = 0.45
            self.x += self.direcao[0] * v * dt
            self.y += self.direcao[1] * v * dt
            if not 0.04 < self.x < 0.96:
                self.direcao = (-self.direcao[0], self.direcao[1])
            if not 0.04 < self.y < 0.96:
                self.direcao = (self.direcao[0], -self.direcao[1])
        elif not self.chegou and self.alvo:
            dx, dy = self.alvo[0] - self.x, self.alvo[1] - self.y
            dist = math.hypot(dx, dy)
            if dist < 0.01:
                self.chegou = True
                if self.atividade == "andar":
                    self.fim = t
                else:
                    self.fim = t + self._duracao(self.atividade)
            else:
                s = min(self.vel * dt, dist)
                self.x += dx / dist * s
                self.y += dy / dist * s
        else:
            tremida = 0.0004 if self.atividade == "descansar" else 0.0015
            self.x += random.uniform(-tremida, tremida)
            self.y += random.uniform(-tremida, tremida)
        self.x = min(max(self.x, 0.03), 0.97)
        self.y = min(max(self.y, 0.03), 0.97)
        if t >= self.fim:
            self.escolher(t)

    def rotulo(self):
        if not self.chegou and self.atividade in ("comer", "beber", "andar"):
            return ROTULO_MODELO["andar"]
        return ROTULO_MODELO[self.atividade]


class FonteDemo:
    def __init__(self, cfg_demo: dict, zonas: dict, barramento: Barramento):
        self.cfg = cfg_demo
        self.zonas = zonas
        self.bus = barramento
        self.fps = float(cfg_demo.get("fps", 10))
        n, doentes = int(cfg_demo.get("pintos", 12)), int(cfg_demo.get("doentes", 2))
        self.descricao = f"Demo simulada ({n} pintos, {doentes} doentes)"
        ids_doentes = set(random.sample(range(1, n + 1), min(doentes, n)))
        self.pintos = [PintoSimulado(i, i in ids_doentes, zonas) for i in range(1, n + 1)]
        self.ids_doentes = sorted(ids_doentes)

    def amostras(self, parar):
        simular_modelo = bool(self.cfg.get("simular_modelo_comportamento", True))
        falha = float(self.cfg.get("taxa_falha_deteccao", 0.02))
        ruido = float(self.cfg.get("taxa_rotulo_errado", 0.04))
        self.bus.log("sistema", "info",
                     f"Fonte aberta: {self.descricao} a {self.fps:.0f} fps — "
                     f"{'rótulos simulando o modelo de comportamento' if simular_modelo else 'sem rótulos (heurística por zona/movimento)'}",
                     {"doentes_simulados": self.ids_doentes})
        dt = 1.0 / self.fps
        inicio = time.monotonic()
        proximo_panico = random.uniform(60, 120)
        indice = 0
        while not parar.is_set():
            t = time.monotonic() - inicio
            if t >= proximo_panico:
                self.bus.log("sistema", "debug", "Demo: disparando evento de pânico no lote")
                for p in self.pintos:
                    p.entrar_em_panico(t)
                proximo_panico = t + random.uniform(90, 180)

            deteccoes = []
            for p in self.pintos:
                p.passo(t, dt)
                if random.random() < falha:
                    continue
                meio = p.tamanho / 2
                caixa = (max(p.x - meio, 0), max(p.y - meio, 0), min(p.x + meio, 1), min(p.y + meio, 1))
                if simular_modelo:
                    rotulo = p.rotulo()
                    if random.random() < ruido:
                        rotulo = random.choice(list(ROTULO_MODELO.values()))
                else:
                    rotulo = "chick"
                deteccoes.append(Deteccao(caixa=caixa, conf=round(random.uniform(0.62, 0.96), 2), id=p.id,
                                          classe="pinto", rotulo=rotulo))

            yield Amostra(t=t, indice=indice, deteccoes=deteccoes, largura=800, altura=600)
            indice += 1
            espera = inicio + indice * dt - time.monotonic()
            if espera > 0:
                parar.wait(espera)


# ---------------------------------------------------------------------------
# Vídeo real via OpenCV
# ---------------------------------------------------------------------------

class FonteVideo:
    def __init__(self, tipo: str, origem, cfg_video: dict, barramento: Barramento):
        try:
            import cv2
        except ImportError as e:
            raise ErroFonte("OpenCV não instalado. Rode: pip install -r requirements.txt") from e
        self.cv2 = cv2
        self.tipo = tipo  # arquivo | webcam | rtsp
        self.origem = origem
        self.cfg = cfg_video
        self.bus = barramento
        nomes = {"arquivo": "Arquivo", "webcam": "Webcam", "rtsp": "Câmera IP"}
        if tipo == "arquivo":
            exibido = Path(origem).name
        elif tipo == "rtsp":
            exibido = re.sub(r"//([^:/@]+):[^/]*@", r"//\1:***@", origem)  # não expor a senha da câmera
        else:
            exibido = f"índice {origem}"
        self.descricao = f"{nomes[tipo]}: {exibido}"

    def amostras(self, parar):
        cv2 = self.cv2
        cap = cv2.VideoCapture(self.origem)
        if not cap.isOpened():
            raise ErroFonte(f"Não foi possível abrir a fonte de vídeo ({self.descricao})")
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            if not 1 <= fps <= 120:
                fps = 25.0
            largura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            altura = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.tipo == "arquivo" else 0
            extra = f", {total} frames ({total / fps:.0f}s)" if total > 0 else ""
            self.bus.log("sistema", "info", f"Fonte aberta: {self.descricao} — {largura}x{altura} @ {fps:.1f} fps{extra}",
                         {"largura": largura, "altura": altura, "fps": fps, "frames": total})

            if self.tipo == "arquivo":
                yield from self._ler_arquivo(cap, fps, parar)
            else:
                yield from self._ler_ao_vivo(cap, parar)
        finally:
            cap.release()

    def _ler_arquivo(self, cap, fps, parar):
        """Arquivo: processa na ordem, usando o tempo do vídeo (a análise fica correta mesmo se a CPU for lenta)."""
        pular = max(1, int(self.cfg.get("processar_a_cada_n_frames", 2)))
        tempo_real = bool(self.cfg.get("tempo_real_arquivo", True))
        inicio = time.monotonic()
        indice = -1
        while not parar.is_set():
            ok, frame = cap.read()
            if not ok:
                self.bus.log("sistema", "info", f"Fim do vídeo ({indice + 1} frames lidos)")
                return
            indice += 1
            t = indice / fps
            espera = t - (time.monotonic() - inicio)
            if tempo_real and espera > 0:
                parar.wait(espera)
            if indice % pular:
                continue
            altura, largura = frame.shape[:2]
            yield Amostra(t=t, indice=indice, imagem=frame, largura=largura, altura=altura)

    def _ler_ao_vivo(self, cap, parar):
        """Câmera: uma thread lê sem parar e guarda só o quadro mais recente, para o atraso não se acumular."""
        slot = {"frame": None, "n": 0, "falhas": 0}
        cond = threading.Condition()
        fim = threading.Event()

        def leitor():
            while not (parar.is_set() or fim.is_set()):
                ok, frame = cap.read()
                with cond:
                    if ok:
                        slot["frame"], slot["n"], slot["falhas"] = frame, slot["n"] + 1, 0
                    else:
                        slot["falhas"] += 1
                    cond.notify()
                if not ok:
                    fim.wait(0.1)

        thread = threading.Thread(target=leitor, name="leitor-camera", daemon=True)
        thread.start()
        inicio = time.monotonic()
        visto = 0
        descartados = 0
        ultimo_aviso = inicio
        try:
            while not parar.is_set():
                with cond:
                    cond.wait_for(lambda: slot["n"] != visto or slot["falhas"] > 50 or parar.is_set(), timeout=1.0)
                    if slot["falhas"] > 50:
                        raise ErroFonte("Sem sinal da câmera (50 leituras falharam seguidas)")
                    if slot["n"] == visto:
                        continue
                    descartados += slot["n"] - visto - 1
                    frame, visto = slot["frame"], slot["n"]
                agora = time.monotonic()
                if descartados and agora - ultimo_aviso >= 30:
                    self.bus.log("sistema", "debug",
                                 f"{descartados} quadros descartados nos últimos 30s (processamento mais lento que a câmera)")
                    descartados, ultimo_aviso = 0, agora
                altura, largura = frame.shape[:2]
                yield Amostra(t=agora - inicio, indice=visto, imagem=frame, largura=largura, altura=altura)
        finally:
            fim.set()
            thread.join(timeout=3)
