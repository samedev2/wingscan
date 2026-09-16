"""Análise de comportamento por pinto e regras de saúde."""

import math
from collections import Counter, deque
from dataclasses import dataclass, field

from .eventos import Barramento

COMPORTAMENTOS = ("comendo", "bebendo", "bicando", "andando", "descansando", "agitado")

DESCRICAO_ALERTA = {
    "sem_comer": ("sem comer há {tempo}", "voltou a comer"),
    "sem_beber": ("sem beber há {tempo}", "voltou a beber"),
    "imovel": ("parado há {tempo} (possível fraqueza/doença)", "voltou a se movimentar"),
}


@dataclass
class Deteccao:
    caixa: tuple[float, float, float, float]  # x1, y1, x2, y2 normalizados (0-1)
    conf: float
    id: int | None = None
    classe: str | None = None  # tipo de ave vindo do detector (ex.: "pinto", "galinha", "galo")
    rotulo: str | None = None  # rótulo de comportamento, se houver modelo de comportamento (ex.: "feeding")
    comportamento: str | None = None  # preenchido pela análise


@dataclass
class Amostra:
    t: float  # segundos desde o início da fonte (tempo do vídeo ou relógio)
    indice: int
    imagem: object = None  # ndarray BGR (vídeo real) ou None (demo)
    deteccoes: list[Deteccao] | None = None  # já pronto na demo; None = rodar detector
    largura: int = 800
    altura: int = 600


def formatar_duracao(segundos: float) -> str:
    s = int(max(0, segundos))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _centro(caixa):
    x1, y1, x2, y2 = caixa
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _dentro(ponto, retangulos):
    x, y = ponto
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in retangulos)


@dataclass
class Pinto:
    id: int
    primeiro: float
    ultimo: float
    janela: int
    classe: str | None = None
    estado: str | None = None
    estado_desde: float = 0.0
    ult_comendo: float = 0.0
    ult_bebendo: float = 0.0
    velocidade: float = 0.0
    trilha: deque = field(default_factory=lambda: deque(maxlen=60))
    rotulos: deque = None
    classes: Counter = field(default_factory=Counter)  # votos de classe do detector ao longo da trilha
    tempo: dict = field(default_factory=lambda: {c: 0.0 for c in COMPORTAMENTOS})
    alertas: set = field(default_factory=set)

    def __post_init__(self):
        self.rotulos = deque(maxlen=self.janela)
        self.estado_desde = self.ult_comendo = self.ult_bebendo = self.primeiro


class Analisador:
    def __init__(self, cfg: dict, limites: dict, barramento: Barramento):
        a = cfg["analise"]
        self.janela = int(a["janela_suavizacao"])
        self.vel_andando = float(a["vel_andando"])
        self.vel_agitado = float(a["vel_agitado"])
        self.esquecer_apos = float(a["esquecer_apos_s"])
        self.agitacao_fracao = float(a["agitacao_fracao"])
        self.limites = limites
        self.zonas = cfg["zonas"]
        self.mapa = {k.strip().lower(): v for k, v in cfg["mapa_comportamentos"].items()}
        self.bus = barramento
        self.pintos: dict[int, Pinto] = {}
        self.grupo_agitado = False
        self._agitacao_desde = None

    @staticmethod
    def _nome(p: Pinto) -> str:
        return f"{(p.classe or 'ave').capitalize()} #{p.id}"

    # ---- classificação ----------------------------------------------------
    def _mapear(self, rotulo: str | None) -> str | None:
        if not rotulo:
            return None
        chave = rotulo.strip().lower()
        if chave in COMPORTAMENTOS:
            return chave
        valor = self.mapa.get(chave)
        return valor if valor in COMPORTAMENTOS else None

    def _heuristica(self, centro, velocidade) -> str:
        if velocidade >= self.vel_agitado:
            return "agitado"
        if _dentro(centro, self.zonas.get("comedouro", [])):
            return "comendo"
        if _dentro(centro, self.zonas.get("bebedouro", [])):
            return "bebendo"
        if velocidade >= self.vel_andando:
            return "andando"
        return "descansando"

    # ---- ciclo principal ----------------------------------------------------
    def atualizar(self, t: float, deteccoes: list[Deteccao]) -> dict:
        for d in deteccoes:
            c = _centro(d.caixa)
            if d.id is None:
                d.comportamento = self._mapear(d.rotulo) or self._heuristica(c, 0.0)
                continue
            p = self.pintos.get(d.id)
            nova = p is None
            if nova:
                p = Pinto(id=d.id, primeiro=t, ultimo=t, janela=self.janela)
                self.pintos[d.id] = p
            dt = min(max(t - p.ultimo, 0.0), 2.0)
            p.ultimo = t
            if d.classe:
                # classe da trilha = a mais votada (o detector pode oscilar entre galinha e galo num quadro)
                p.classes[d.classe] += 1
                p.classe = p.classes.most_common(1)[0][0]
            if nova:
                self.bus.log("comportamento", "debug", f"{self._nome(p)} entrou em cena", {"id": d.id, "classe": p.classe})
            self._atualizar_velocidade(p, t, c)

            p.rotulos.append(self._mapear(d.rotulo) or self._heuristica(c, p.velocidade))
            novo = Counter(p.rotulos).most_common(1)[0][0]
            if p.estado is None:
                p.estado, p.estado_desde = novo, t
            elif novo != p.estado:
                self.bus.log(
                    "comportamento", "info",
                    f"{self._nome(p)}: {p.estado} → {novo} (após {formatar_duracao(t - p.estado_desde)})",
                    {"id": p.id, "classe": p.classe, "de": p.estado, "para": novo, "duracao_s": round(t - p.estado_desde, 1)},
                )
                p.estado, p.estado_desde = novo, t
            p.tempo[p.estado] += dt
            if p.estado == "comendo":
                p.ult_comendo = t
            elif p.estado == "bebendo":
                p.ult_bebendo = t
            d.comportamento = p.estado
            self._verificar_regras(p, t)

        self._esquecer(t)
        visiveis = [p for p in self.pintos.values() if p.ultimo == t]
        self._verificar_grupo(t, visiveis)
        return self.resumo(t, deteccoes)

    def _atualizar_velocidade(self, p: Pinto, t: float, centro):
        # Velocidade medida numa janela de ~1s para não confundir tremida da caixa com movimento.
        p.trilha.append((t, centro))
        while len(p.trilha) > 2 and t - p.trilha[0][0] > 1.2:
            p.trilha.popleft()
        t0, c0 = p.trilha[0]
        if t - t0 >= 0.4:
            p.velocidade = math.dist(c0, centro) / (t - t0)

    def _verificar_regras(self, p: Pinto, t: float):
        condicoes = {
            "sem_comer": (t - p.ult_comendo, self.limites["sem_comer_s"]),
            "sem_beber": (t - p.ult_bebendo, self.limites["sem_beber_s"]),
            "imovel": (t - p.estado_desde if p.estado == "descansando" else 0.0, self.limites["imovel_s"]),
        }
        for chave, (decorrido, limite) in condicoes.items():
            ativo = decorrido > limite
            texto_on, texto_off = DESCRICAO_ALERTA[chave]
            if ativo and chave not in p.alertas:
                p.alertas.add(chave)
                self.bus.log(
                    "alerta", "aviso",
                    f"{self._nome(p)} {texto_on.format(tempo=formatar_duracao(decorrido))}",
                    {"id": p.id, "regra": chave, "decorrido_s": round(decorrido, 1), "limite_s": limite},
                )
            elif not ativo and chave in p.alertas:
                p.alertas.discard(chave)
                self.bus.log("alerta", "info", f"{self._nome(p)} {texto_off} — alerta encerrado",
                             {"id": p.id, "regra": chave})

    def _verificar_grupo(self, t: float, visiveis: list[Pinto]):
        agitados = sum(1 for p in visiveis if p.estado == "agitado")
        acima = len(visiveis) >= 3 and agitados / len(visiveis) >= self.agitacao_fracao
        if acima and not self.grupo_agitado:
            self._agitacao_desde = self._agitacao_desde if self._agitacao_desde is not None else t
            if t - self._agitacao_desde >= 1.5:
                self.grupo_agitado = True
                self.bus.log(
                    "alerta", "aviso",
                    f"Agitação no lote: {agitados}/{len(visiveis)} aves agitadas "
                    "(verificar ruído, predador ou temperatura)",
                    {"regra": "agitacao_lote", "agitados": agitados, "visiveis": len(visiveis)},
                )
        elif not acima:
            self._agitacao_desde = None
            if self.grupo_agitado:
                self.grupo_agitado = False
                self.bus.log("alerta", "info", "Lote acalmou — alerta de agitação encerrado",
                             {"regra": "agitacao_lote"})

    def _esquecer(self, t: float):
        for pid in [pid for pid, p in self.pintos.items() if t - p.ultimo > self.esquecer_apos]:
            p = self.pintos.pop(pid)
            nivel = "info" if p.alertas else "debug"
            extra = f" (tinha alertas: {', '.join(sorted(p.alertas))})" if p.alertas else ""
            self.bus.log("comportamento", nivel,
                         f"{self._nome(p)} saiu de vista após {formatar_duracao(p.ultimo - p.primeiro)}{extra}",
                         {"id": pid})

    # ---- saída para o front -------------------------------------------------
    def _textos_alertas(self, p: Pinto, t: float) -> list[dict]:
        tempos = {"sem_comer": t - p.ult_comendo, "sem_beber": t - p.ult_bebendo, "imovel": t - p.estado_desde}
        textos = {"sem_comer": "sem comer há {}", "sem_beber": "sem beber há {}", "imovel": "imóvel há {}"}
        saida = []
        for chave in sorted(p.alertas):
            saida.append({"chave": chave, "texto": textos[chave].format(formatar_duracao(tempos[chave]))})
        return saida

    def resumo(self, t: float, deteccoes: list[Deteccao]) -> dict:
        contagem = Counter(d.comportamento for d in deteccoes)
        pintos = []
        for p in self.pintos.values():
            total = sum(p.tempo.values()) or 1.0
            pintos.append({
                "id": p.id,
                "classe": p.classe,
                "visivel": p.ultimo == t,
                "estado": p.estado,
                "estado_ha_s": round(t - p.estado_desde, 1),
                "sem_comer_s": round(t - p.ult_comendo, 1),
                "sem_beber_s": round(t - p.ult_bebendo, 1),
                "velocidade": round(p.velocidade, 3),
                "observado_s": round(t - p.primeiro, 1),
                "pct": {c: round(v / total, 3) for c, v in p.tempo.items()},
                "alertas": self._textos_alertas(p, t),
            })
        pintos.sort(key=lambda x: (not x["alertas"], x["id"]))
        return {
            "detectados": len(deteccoes),
            "por_classe": dict(Counter(d.classe or "ave" for d in deteccoes).most_common()),
            "por_comportamento": {c: contagem.get(c, 0) for c in COMPORTAMENTOS},
            "alertas_ativos": sum(len(p["alertas"]) for p in pintos) + (1 if self.grupo_agitado else 0),
            "grupo_agitado": self.grupo_agitado,
            "pintos": pintos,
        }
