"""Coleta, revisão e treino incremental do detector (pinto, galinha, galo).

Fluxo pensado para treinos leves e iterativos, não um treino único e massivo:

1. `extrair_quadros`   — tira quadros dos vídeos em videos/ (poucos por rodada).
2. `pre_rotular`       — o modelo atual já rotula os quadros novos (só precisa corrigir).
3. revisão no painel   — `carregar_rotulos` / `salvar_rotulos`, um quadro por vez.
4. `iniciar_treino`    — parte do modelos/pinteiro.pt (fine-tuning, não do zero),
                         poucas épocas, e treina de novo em cima do resultado revisado.
5. `promover_modelo`   — só troca o modelo em uso se as métricas da rodada convencerem.

Cada rodada deve ser pequena (dezenas a poucas centenas de quadros): como parte de um
modelo já treinado, isso já ajusta bem à câmera/luz nova sem precisar de milhares de imagens.
"""

import json
import random
import shutil
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from .analise import Deteccao
from .heuristica import corrigir_por_tamanho

CLASSES = ["pinto", "galinha", "galo"]
EXTENSOES_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}

_job_lock = threading.Lock()
_job = {"rodando": False, "epoca": 0, "epocas": 0, "mensagem": "", "concluido": None, "erro": None}


# ---- caminhos --------------------------------------------------------------
def _pasta_treino(raiz: Path) -> Path:
    return raiz / "treino"


def pasta_imagens(raiz: Path) -> Path:
    return _pasta_treino(raiz) / "dataset" / "images"


def _pasta_labels(raiz: Path) -> Path:
    return _pasta_treino(raiz) / "dataset" / "labels"


def _arquivo_revisados(raiz: Path) -> Path:
    return _pasta_treino(raiz) / "dataset" / "revisado.json"


def _pasta_split(raiz: Path) -> Path:
    return _pasta_treino(raiz) / "dataset" / "split"


def _pasta_runs(raiz: Path) -> Path:
    return _pasta_treino(raiz) / "runs"


def _carregar_revisados(raiz: Path) -> set[str]:
    caminho = _arquivo_revisados(raiz)
    if not caminho.is_file():
        return set()
    return set(json.loads(caminho.read_text(encoding="utf-8")))


def _salvar_revisados(raiz: Path, revisados: set[str]):
    caminho = _arquivo_revisados(raiz)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(sorted(revisados), ensure_ascii=False, indent=2), encoding="utf-8")


# ---- 1. extração de quadros dos vídeos -------------------------------------
def extrair_quadros(raiz: Path, videos: list[str], intervalo_s: float = 1.5, limite_por_video: int = 150) -> list[str]:
    import cv2

    pasta_imgs = pasta_imagens(raiz)
    pasta_imgs.mkdir(parents=True, exist_ok=True)
    novos = []
    for nome in videos:
        caminho = raiz / "videos" / nome
        if not caminho.is_file():
            continue
        captura = cv2.VideoCapture(str(caminho))
        try:
            fps = captura.get(cv2.CAP_PROP_FPS) or 24
            passo = max(1, round(fps * intervalo_s))
            indice, salvos = 0, 0
            while salvos < limite_por_video:
                ok, quadro = captura.read()
                if not ok:
                    break
                if indice % passo == 0:
                    nome_arquivo = f"{caminho.stem}_{indice:06d}.jpg"
                    destino = pasta_imgs / nome_arquivo
                    if not destino.exists():
                        cv2.imwrite(str(destino), quadro, [cv2.IMWRITE_JPEG_QUALITY, 92])
                        novos.append(nome_arquivo)
                    salvos += 1
                indice += 1
        finally:
            captura.release()
    return novos


# ---- 2. pré-rotulagem com o modelo atual -----------------------------------
def pre_rotular(raiz: Path, cfg_modelos: dict, nomes: list[str] | None = None) -> int:
    """Roda o detector atual sobre quadros sem rótulo, gerando um ponto de partida para revisão."""
    from ultralytics import YOLO

    caminho_modelo = raiz / cfg_modelos["deteccao"]
    if not caminho_modelo.is_file():
        raise ValueError(f"Modelo de detecção não encontrado em '{cfg_modelos['deteccao']}'.")
    modelo = YOLO(str(caminho_modelo))
    extra = {"device": cfg_modelos["dispositivo"]} if cfg_modelos.get("dispositivo") else {}

    pasta_imgs = pasta_imagens(raiz)
    pasta_lbls = _pasta_labels(raiz)
    pasta_lbls.mkdir(parents=True, exist_ok=True)
    alvo = nomes if nomes is not None else [p.name for p in pasta_imgs.glob("*.jpg")]

    rotulados = 0
    for nome in alvo:
        destino = pasta_lbls / f"{Path(nome).stem}.txt"
        if destino.is_file():
            continue  # não sobrescreve rótulo (pode já ter sido revisado)
        r = modelo.predict(str(pasta_imgs / nome), conf=0.25, verbose=False, **extra)[0]
        deteccoes = []
        if r.boxes is not None and len(r.boxes):
            caixas = r.boxes.xyxyn.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), cls in zip(caixas, classes):
                nome_classe = CLASSES[cls] if 0 <= cls < len(CLASSES) else str(cls)
                deteccoes.append(Deteccao(caixa=(float(x1), float(y1), float(x2), float(y2)), conf=0.0, classe=nome_classe))
        corrigir_por_tamanho(deteccoes)  # mesma correção geométrica da detecção ao vivo (ver heuristica.py)
        linhas = []
        for d in deteccoes:
            if d.classe not in CLASSES:
                continue
            idx = CLASSES.index(d.classe)
            x1, y1, x2, y2 = d.caixa
            cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
            linhas.append(f"{idx} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
        destino.write_text("\n".join(linhas), encoding="utf-8")
        rotulados += 1
    return rotulados


# ---- 3. listagem e edição de rótulos (usado pelo painel de revisão) -------
def listar_imagens(raiz: Path) -> list[dict]:
    pasta_imgs = pasta_imagens(raiz)
    pasta_lbls = _pasta_labels(raiz)
    revisados = _carregar_revisados(raiz)
    itens = []
    for p in sorted(pasta_imgs.glob("*.jpg")):
        lbl = pasta_lbls / f"{p.stem}.txt"
        areas = []
        if lbl.is_file():
            for linha in lbl.read_text(encoding="utf-8").splitlines():
                partes = linha.split()
                if len(partes) == 5:
                    areas.append(float(partes[3]) * float(partes[4]))
        itens.append({
            "nome": p.name, "revisado": p.name in revisados, "caixas": len(areas),
            "menor_area": min(areas) if areas else None,
        })
    return itens


def estatisticas_classes(raiz: Path) -> dict:
    """Área (normalizada, 0-1) das caixas já revisadas, por classe.

    Serve para checar o problema de galinha distante virar pinto: se a faixa de área da
    galinha não encosta na do pinto, o modelo só tem exemplo de galinha grande/perto e vai
    aprender "caixa pequena = pinto" em vez de olhar a forma. O ideal é que as faixas se
    sobreponham (galinha pequena/distante rotulada como galinha mesmo).
    """
    revisados = _carregar_revisados(raiz)
    pasta_lbls = _pasta_labels(raiz)
    areas = {c: [] for c in CLASSES}
    for nome in revisados:
        lbl = pasta_lbls / f"{Path(nome).stem}.txt"
        if not lbl.is_file():
            continue
        for linha in lbl.read_text(encoding="utf-8").splitlines():
            partes = linha.split()
            if len(partes) != 5:
                continue
            cls = int(partes[0])
            if 0 <= cls < len(CLASSES):
                areas[CLASSES[cls]].append(float(partes[3]) * float(partes[4]))
    return {
        c: {"n": len(v), "area_min": min(v), "area_media": sum(v) / len(v), "area_max": max(v)} if v
        else {"n": 0, "area_min": None, "area_media": None, "area_max": None}
        for c, v in areas.items()
    }


def carregar_rotulos(raiz: Path, nome: str) -> list[dict]:
    lbl = _pasta_labels(raiz) / f"{Path(nome).stem}.txt"
    caixas = []
    if lbl.is_file():
        for linha in lbl.read_text(encoding="utf-8").splitlines():
            partes = linha.split()
            if len(partes) != 5:
                continue
            cls = int(partes[0])
            cx, cy, w, h = (float(v) for v in partes[1:])
            caixas.append({
                "classe": CLASSES[cls] if 0 <= cls < len(CLASSES) else str(cls),
                "x1": cx - w / 2, "y1": cy - h / 2, "x2": cx + w / 2, "y2": cy + h / 2,
            })
    return caixas


def salvar_rotulos(raiz: Path, nome: str, caixas: list[dict]):
    nome = Path(nome).name
    if not (pasta_imagens(raiz) / nome).is_file():
        raise ValueError("Imagem não encontrada")
    lbl = _pasta_labels(raiz) / f"{Path(nome).stem}.txt"
    lbl.parent.mkdir(parents=True, exist_ok=True)
    linhas = []
    for c in caixas:
        if c["classe"] not in CLASSES:
            continue
        idx = CLASSES.index(c["classe"])
        x1, y1, x2, y2 = c["x1"], c["y1"], c["x2"], c["y2"]
        cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
        linhas.append(f"{idx} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    lbl.write_text("\n".join(linhas), encoding="utf-8")
    revisados = _carregar_revisados(raiz)
    revisados.add(nome)
    _salvar_revisados(raiz, revisados)


# ---- 4. preparação do dataset e treino -------------------------------------
def preparar_split(raiz: Path, fracao_treino: float = 0.85, semente: int = 42):
    revisados = sorted(_carregar_revisados(raiz))
    if len(revisados) < 20:
        raise ValueError(f"Poucas imagens revisadas ({len(revisados)}). Revise pelo menos 20 antes de treinar.")

    random.Random(semente).shuffle(revisados)
    corte = max(1, int(len(revisados) * fracao_treino))
    val = revisados[corte:] or revisados[-max(1, len(revisados) // 10):]
    grupos = {"train": revisados[:corte], "val": val}

    base = _pasta_split(raiz)
    if base.exists():
        shutil.rmtree(base)
    contagem = Counter()
    for grupo, nomes in grupos.items():
        (base / grupo / "images").mkdir(parents=True, exist_ok=True)
        (base / grupo / "labels").mkdir(parents=True, exist_ok=True)
        for nome in nomes:
            shutil.copy2(pasta_imagens(raiz) / nome, base / grupo / "images" / nome)
            lbl_origem = _pasta_labels(raiz) / f"{Path(nome).stem}.txt"
            lbl_destino = base / grupo / "labels" / f"{Path(nome).stem}.txt"
            if lbl_origem.is_file():
                shutil.copy2(lbl_origem, lbl_destino)
                for linha in lbl_origem.read_text(encoding="utf-8").splitlines():
                    if linha.strip():
                        contagem[CLASSES[int(linha.split()[0])]] += 1
            else:
                lbl_destino.write_text("", encoding="utf-8")

    data_yaml = base / "data.yaml"
    data_yaml.write_text(yaml.safe_dump({
        "path": str(base.resolve()),
        "train": "train/images",
        "val": "val/images",
        "names": {i: c for i, c in enumerate(CLASSES)},
    }, allow_unicode=True), encoding="utf-8")
    return data_yaml, len(grupos["train"]), len(grupos["val"]), dict(contagem)


def status_job() -> dict:
    with _job_lock:
        return dict(_job)


def iniciar_treino(raiz: Path, cfg_modelos: dict, bus, epocas: int = 25, imgsz: int = 960, batch: int = 4):
    with _job_lock:
        if _job["rodando"]:
            raise ValueError("Já existe um treino em andamento.")
        data_yaml, n_treino, n_val, contagem = preparar_split(raiz)
        if not contagem:
            raise ValueError("Nenhuma caixa marcada nas imagens revisadas — revise ao menos algumas aves antes de treinar.")
        _job.update(rodando=True, epoca=0, epocas=epocas, mensagem="preparando…", concluido=None, erro=None)

    thread = threading.Thread(
        target=_treinar_em_thread,
        args=(raiz, cfg_modelos, bus, data_yaml, epocas, imgsz, batch, n_treino, n_val, contagem),
        daemon=True,
    )
    thread.start()
    return {"treino": n_treino, "validacao": n_val, "por_classe": contagem}


def _treinar_em_thread(raiz, cfg_modelos, bus, data_yaml, epocas, imgsz, batch, n_treino, n_val, contagem):
    from ultralytics import YOLO

    bus.log("sistema", "info",
             f"Treino iniciado: {n_treino} imagens de treino, {n_val} de validação",
             {"por_classe": contagem, "epocas": epocas, "imgsz": imgsz})
    try:
        modelo_base = raiz / cfg_modelos["deteccao"]
        modelo = YOLO(str(modelo_base))

        def ao_fim_da_epoca(trainer):
            with _job_lock:
                _job["epoca"] = trainer.epoch + 1
                _job["epocas"] = trainer.epochs
                _job["mensagem"] = f"época {_job['epoca']}/{_job['epocas']}"

        modelo.add_callback("on_train_epoch_end", ao_fim_da_epoca)

        nome_run = datetime.now().strftime("v%Y%m%d-%H%M%S")
        extra = {"device": cfg_modelos["dispositivo"]} if cfg_modelos.get("dispositivo") else {"device": "cpu"}
        resultado = modelo.train(
            data=str(data_yaml), epochs=epocas, imgsz=imgsz, batch=batch,
            project=str(_pasta_runs(raiz)), name=nome_run, exist_ok=True,
            workers=0, patience=15, verbose=False, plots=False,
            scale=0.9,  # jitter de escala mais forte: força a rede a não confiar no tamanho em pixels
            **extra,
        )
        melhor = Path(resultado.save_dir) / "weights" / "best.pt"
        if not melhor.is_file():
            raise RuntimeError("Treino terminou sem gerar pesos (best.pt).")

        modelo_treinado = YOLO(str(melhor))
        metricas = modelo_treinado.val(data=str(data_yaml), imgsz=imgsz, verbose=False, plots=False, **extra)
        por_classe = {
            nome: {"precisao": round(float(p), 3), "revocacao": round(float(r), 3)}
            for nome, p, r in zip(metricas.names.values(), metricas.box.p, metricas.box.r)
        }

        destino = raiz / "modelos" / f"pinteiro_{nome_run}.pt"
        shutil.copy2(melhor, destino)

        with _job_lock:
            _job.update(rodando=False, mensagem="concluído",
                        concluido={"pesos": str(destino.relative_to(raiz)).replace("\\", "/"), "metricas": por_classe})
        bus.log("sistema", "info", f"Treino concluído: {destino.name}", {"metricas": por_classe})
    except Exception as e:  # thread própria: precisa registrar o erro em vez de deixar subir
        with _job_lock:
            _job.update(rodando=False, mensagem="erro", erro=str(e))
        bus.log("sistema", "erro", f"Treino falhou: {e}")


def promover_modelo(raiz: Path, caminho_pesos: str):
    """Coloca `caminho_pesos` (relativo à raiz, dentro de modelos/) em uso, guardando o anterior."""
    origem = (raiz / caminho_pesos).resolve()
    pasta_modelos = (raiz / "modelos").resolve()
    if not origem.is_relative_to(pasta_modelos) or not origem.is_file():
        raise ValueError("Arquivo de pesos inválido.")
    atual = raiz / "modelos" / "pinteiro.pt"
    if atual.is_file():
        historico = raiz / "modelos" / "historico"
        historico.mkdir(exist_ok=True)
        shutil.copy2(atual, historico / f"pinteiro_{datetime.now().strftime('%Y%m%d-%H%M%S')}.pt")
    shutil.copy2(origem, atual)
