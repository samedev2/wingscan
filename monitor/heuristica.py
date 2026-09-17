"""Correção geométrica da classe por tamanho relativo (pinto vs. galinha/galo).

Importante: isto NÃO resolve sozinho o caso "galinha longe da câmera vira pinto". Uma caixa pequena
é ambígua — pode ser um pinto (pequeno em qualquer distância) ou uma galinha longe da câmera (pequena
só por perspectiva); nenhuma conta com os 4 números da caixa dá pra distinguir isso com certeza, porque
os dois casos produzem uma caixa do mesmo tamanho. Quem resolve é o próprio detector aprendendo forma e
plumagem (corcunda mais cavada e postura ereta/pescoço grosso da galinha/galo, penugem rala e cores
fracas do pinto) a partir de exemplos revisados de aves pequenas/distantes — por isso a tela de treino
prioriza justamente esses quadros (ver `treino.py` e a dica de revisão em `treino.html`).

O que dá pra fazer com segurança usando só geometria é o caso oposto, mais raro mas real: o detector
classifica uma ave como galinha/galo, mas ela é claramente menor (~1/6 do comprimento, conforme
observado no pinteiro) que as aves adultas bem ao lado dela no mesmo quadro — mesma região, logo
aproximadamente a mesma distância da câmera, então a diferença de tamanho já não pode ser perspectiva.
Nesse caso específico dá pra corrigir para pinto com confiança.
"""

import math

RAZAO_PINTO_ADULTO = 6.0  # pinto tem corpo ~6x menor (comprimento) que uma ave adulta
MARGEM = 0.65  # limiar de disparo = RAZAO * MARGEM (~3.9x): um pouco abaixo do valor típico observado,
# pra pegar a maioria dos casos reais sem confundir com galinha jovem/de porte médio (evita falso positivo)
RAIO_VIZINHANCA = 0.25  # fração da diagonal do quadro considerada "mesma região/distância da câmera"
MIN_VIZINHOS_ADULTOS = 2  # só corrige com pelo menos 2 adultos próximos como referência


def _centro(caixa):
    x1, y1, x2, y2 = caixa
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _tamanho_linear(caixa):
    x1, y1, x2, y2 = caixa
    return max(0.0, (x2 - x1) * (y2 - y1)) ** 0.5


def corrigir_por_tamanho(deteccoes):
    """Reclassifica como 'pinto' galinha/galo muito menor que os adultos vizinhos no mesmo quadro."""
    limite = RAZAO_PINTO_ADULTO * MARGEM
    for d in deteccoes:
        if d.classe not in ("galinha", "galo"):
            continue
        centro = _centro(d.caixa)
        vizinhos = [
            _tamanho_linear(o.caixa) for o in deteccoes
            if o is not d and o.classe in ("galinha", "galo")
            and math.dist(_centro(o.caixa), centro) <= RAIO_VIZINHANCA
        ]
        if len(vizinhos) < MIN_VIZINHOS_ADULTOS:
            continue
        referencia = sorted(vizinhos)[len(vizinhos) // 2]  # mediana: resiste a 1-2 vizinhos ruidosos
        tamanho = _tamanho_linear(d.caixa)
        if referencia > 0 and tamanho <= referencia / limite:
            d.classe = "pinto"
