"""Identidade persistente das aves num cercado fechado.

As aves não entram nem saem de cena, então quando o rastreador cria um ID novo quase sempre é uma ave
que já existia e foi perdida por um momento (oclusão, pintos amontoados no comedouro). Esta camada
"costura" cada ID novo do rastreador à ave perdida mais próxima da mesma classe, e o painel passa a
mostrar sempre o mesmo número para a mesma ave.
"""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment


class IdentidadePersistente:
    def __init__(self, raio_base: float = 0.03, raio_por_segundo: float = 0.06, raio_max: float = 0.25,
                 populacao: dict[str, int] | None = None):
        """Raios em fração da diagonal do quadro. `populacao` (opcional) = número real de aves por classe:
        com ela, uma classe já completa nunca ganha ave nova, o ID novo vai para a perdida mais próxima."""
        self.raio_base = raio_base
        self.raio_por_segundo = raio_por_segundo
        self.raio_max = raio_max
        self.populacao = populacao or {}
        # teto por classe: começa no valor configurado (se houver) e cresce sozinho até o maior
        # número de aves vistas ao mesmo tempo — a partir daí, ID novo tenta religar numa ave
        # sumida antes de nascer, mesmo pra classes sem população fixa (ex.: pinto, que varia).
        self.pico: dict[str, int] = dict(self.populacao)
        self.aves: dict[int, dict] = {}  # id persistente -> {"classe", "centro", "visto", "criado", "costuras"}
        self.mapa: dict[int, int] = {}   # id do rastreador -> id persistente
        self.proximo = 1
        self.costuras = 0

    def atualizar(self, t: float, deteccoes, largura: int, altura: int) -> list:
        """Troca `d.id` (id do rastreador) pelo id persistente. Retorna as detecções sem duplicatas."""
        diagonal = math.hypot(largura, altura)
        deteccoes = self._sem_duplicatas(deteccoes)
        itens = []  # (deteccao, id do rastreador, centro em pixels)
        for d in deteccoes:
            if d.id is not None:
                x1, y1, x2, y2 = d.caixa
                itens.append((d, d.id, np.array([(x1 + x2) / 2 * largura, (y1 + y2) / 2 * altura])))

        atribuido: dict[int, int] = {}  # índice em itens -> id persistente
        vistas: set[int] = set()
        for k, (d, tid, _) in enumerate(itens):
            pid = self.mapa.get(tid)
            if pid is not None and pid not in vistas:
                atribuido[k] = pid
                vistas.add(pid)

        novas = [k for k in range(len(itens)) if k not in atribuido]
        perdidas = [pid for pid in self.aves if pid not in vistas]
        if novas and perdidas:
            custo = np.full((len(novas), len(perdidas)), 1e6)
            for a, k in enumerate(novas):
                d, _, c = itens[k]
                for b, pid in enumerate(perdidas):
                    ave = self.aves[pid]
                    if ave["classe"] != d.classe:
                        continue
                    dist = np.linalg.norm(c - ave["centro"]) / diagonal
                    if dist <= min(self.raio_max, self.raio_base + self.raio_por_segundo * (t - ave["visto"])):
                        custo[a, b] = dist
            for a, b in zip(*linear_sum_assignment(custo)):
                if custo[a, b] < 1e6:
                    pid = perdidas[b]
                    atribuido[novas[a]] = pid
                    vistas.add(pid)
                    self.costuras += 1
                    self.aves[pid]["costuras"] += 1
            novas = [k for k in novas if k not in atribuido]

        for k in novas:
            d, _, c = itens[k]
            limite_configurado = d.classe in self.populacao
            pico = self.pico.get(d.classe, 0)
            existentes = sum(1 for a in self.aves.values() if a["classe"] == d.classe)
            sobrando = [pid for pid, a in self.aves.items() if a["classe"] == d.classe and pid not in vistas]
            no_teto = existentes >= pico
            if no_teto and limite_configurado and not sobrando:
                # população fixa e configurada, já completa e sem ave sumida: detecção espúria, fica sem ID
                d.id = None
                continue
            if no_teto and sobrando:
                # já vimos esse tanto de uma vez antes (configurado ou não): é uma ave que já
                # existe, mesmo longe — religa em vez de nascer um ID novo (evita fragmentar
                # a identidade quando muitas aves da mesma classe se amontoam e se ocluem)
                pid = min(sobrando, key=lambda p: np.linalg.norm(c - self.aves[p]["centro"]))
                self.costuras += 1
                self.aves[pid]["costuras"] += 1
            else:
                pid = self.proximo
                self.proximo += 1
                self.aves[pid] = {"classe": d.classe, "criado": t, "costuras": 0}
                self.pico[d.classe] = max(pico, existentes + 1)
            atribuido[k] = pid
            vistas.add(pid)

        for k, pid in atribuido.items():
            d, tid, c = itens[k]
            self.mapa[tid] = pid
            self.aves[pid].update(centro=c, visto=t)
            d.id = pid
        return deteccoes

    def resumo(self, t: float) -> dict:
        """Estado do rastreamento em si — sem nada de comportamento — pra validar a identidade
        persistente antes de confiar no que vem depois dela na cadeia (análise de comportamento)."""
        return {
            "total_ids_criados": self.proximo - 1,
            "ids_ativos": len(self.aves),
            "costuras_totais": self.costuras,
            "populacao_esperada": sum(self.populacao.values()) if self.populacao else None,
            "pico_por_classe": dict(self.pico),  # inclui classes sem população configurada (ex.: pinto)
            "aves": [
                {"id": pid, "classe": a["classe"], "criado_ha_s": round(t - a["criado"], 1),
                 "visto_ha_s": round(t - a["visto"], 1) if "visto" in a else None, "costuras": a["costuras"]}
                for pid, a in self.aves.items()
            ],
        }

    def _sem_duplicatas(self, deteccoes):
        """Duas caixas da mesma classe com uma quase toda dentro da outra são a mesma ave
        (ex.: o rastreador ressuscita uma trilha antiga do galo ao lado da atual). Fica a trilha já mapeada."""
        area = lambda b: max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])  # noqa: E731
        ordem = sorted(deteccoes, key=lambda d: (d.id not in self.mapa, -d.conf, -area(d.caixa)))
        mantidas = []
        for d in ordem:
            duplicada = False
            for m in mantidas:
                if m.classe != d.classe:
                    continue
                ix = max(0.0, min(d.caixa[2], m.caixa[2]) - max(d.caixa[0], m.caixa[0]))
                iy = max(0.0, min(d.caixa[3], m.caixa[3]) - max(d.caixa[1], m.caixa[1]))
                if ix * iy >= 0.8 * min(area(d.caixa), area(m.caixa)):
                    duplicada = True
                    break
            if not duplicada:
                mantidas.append(d)
        return [d for d in deteccoes if d in mantidas]
