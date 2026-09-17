"""Detecção + rastreamento com Ultralytics YOLO (modelos do chicken-detector ou treinados por você)."""

import time
from pathlib import Path

from .analise import Deteccao
from .eventos import Barramento
from .fontes import ErroFonte
from .heuristica import corrigir_por_tamanho


class DetectorYOLO:
    def __init__(self, cfg_modelos: dict, raiz: Path, barramento: Barramento):
        self.cfg = cfg_modelos
        self.bus = barramento
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ErroFonte("Pacote 'ultralytics' não instalado. Rode: pip install -r requirements.txt") from e

        caminho_det = raiz / cfg_modelos["deteccao"]
        if not caminho_det.is_file():
            raise ErroFonte(f"Modelo de detecção não encontrado em '{cfg_modelos['deteccao']}'. "
                            "Baixe os pesos (veja README, seção Modelos).")
        inicio = time.monotonic()
        self.modelo = YOLO(str(caminho_det))
        self.bus.log("sistema", "info",
                     f"Modelo de detecção carregado: {caminho_det.name} ({time.monotonic() - inicio:.1f}s)",
                     {"classes": self.modelo.names})

        self.modelo_comp = None
        caminho_comp = cfg_modelos.get("comportamento")
        if caminho_comp and (raiz / caminho_comp).is_file():
            self.modelo_comp = YOLO(str(raiz / caminho_comp))
            self.bus.log("sistema", "info", f"Modelo de comportamento carregado: {Path(caminho_comp).name}",
                         {"classes": self.modelo_comp.names})
        else:
            self.bus.log("sistema", "aviso",
                         "Sem modelo de comportamento — usando heurística por zona (comedouro/bebedouro) e movimento")

        self.conf = float(cfg_modelos.get("confianca", 0.4))
        self.conf_comp = float(cfg_modelos.get("confianca_comportamento", 0.4))
        self.a_cada = max(1, int(cfg_modelos.get("comportamento_a_cada_n_frames", 8)))
        self.max_por_quadro = max(1, int(cfg_modelos.get("comportamento_max_por_quadro", 2)))
        # vazio = todas as classes; o best_seg.pt foi treinado com galinhas e erra muito em recortes de pintos
        self.classes_comp = {c.lower() for c in cfg_modelos.get("comportamento_classes", [])}
        self.extra = {"device": cfg_modelos["dispositivo"]} if cfg_modelos.get("dispositivo") else {}
        rastreador = cfg_modelos.get("rastreador", "bytetrack.yaml")
        self.rastreador = str(raiz / rastreador) if (raiz / rastreador).is_file() else rastreador
        self._cache: dict[int, tuple[str | None, int]] = {}
        self._n = 0
        self.ultima_inferencia_ms = 0.0

    def detectar(self, imagem) -> list[Deteccao]:
        inicio = time.monotonic()
        self._n += 1
        h, w = imagem.shape[:2]
        r = self.modelo.track(imagem, persist=True, conf=self.conf, tracker=self.rastreador,
                              verbose=False, **self.extra)[0]
        deteccoes = []
        if r.boxes is not None and len(r.boxes):
            caixas = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy().astype(int)
            ids = r.boxes.id.cpu().numpy().astype(int).tolist() if r.boxes.id is not None else [None] * len(caixas)
            for (x1, y1, x2, y2), conf, cls, tid in zip(caixas, confs, classes, ids):
                classe = r.names.get(int(cls), str(cls))
                deteccoes.append(Deteccao(
                    caixa=(float(x1 / w), float(y1 / h), float(x2 / w), float(y2 / h)),
                    conf=round(float(conf), 2), id=tid, classe=classe, rotulo=classe,
                ))
            corrigir_por_tamanho(deteccoes)
            if self.modelo_comp is not None:
                self._classificar_comportamentos(imagem, deteccoes)
        self._limpar_cache()
        self.ultima_inferencia_ms = (time.monotonic() - inicio) * 1000
        return deteccoes

    def _classificar_comportamentos(self, imagem, deteccoes: list[Deteccao]):
        """Classifica o recorte de cada ave (abordagem do chicken-detector), com cache por ID.

        Na CPU cada recorte custa ~120 ms, então no máximo `comportamento_max_por_quadro` são
        reclassificados por quadro, começando pelos que estão há mais tempo sem atualização.
        """
        pendentes = []
        for d in deteccoes:
            if d.id is None or (self.classes_comp and (d.classe or "").lower() not in self.classes_comp):
                continue
            em_cache = self._cache.get(d.id)
            idade = self._n - em_cache[1] if em_cache else float("inf")
            if idade >= self.a_cada:
                pendentes.append((idade, d, d.rotulo))
            if em_cache and em_cache[0]:
                d.rotulo = em_cache[0]  # quem ficar fora do limite deste quadro usa o último rótulo conhecido
        pendentes.sort(key=lambda item: item[0], reverse=True)

        h, w = imagem.shape[:2]
        for _, d, classe_deteccao in pendentes[:self.max_por_quadro]:
            x1, y1, x2, y2 = d.caixa[0] * w, d.caixa[1] * h, d.caixa[2] * w, d.caixa[3] * h
            folga_x, folga_y = (x2 - x1) * 0.1, (y2 - y1) * 0.1
            x1, y1 = int(max(0, x1 - folga_x)), int(max(0, y1 - folga_y))
            x2, y2 = int(min(w, x2 + folga_x)), int(min(h, y2 + folga_y))
            rotulo = None
            if x2 - x1 >= 8 and y2 - y1 >= 8:
                r = self.modelo_comp.predict(imagem[y1:y2, x1:x2], conf=self.conf_comp, verbose=False, **self.extra)[0]
                if r.boxes is not None and len(r.boxes):
                    melhor = int(r.boxes.conf.argmax())
                    rotulo = r.names.get(int(r.boxes.cls[melhor]))
            self._cache[d.id] = (rotulo, self._n)
            d.rotulo = rotulo or classe_deteccao

    def _limpar_cache(self):
        if self._n % 100 == 0:
            self._cache = {k: v for k, v in self._cache.items() if self._n - v[1] < 300}
