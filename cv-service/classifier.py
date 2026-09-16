"""
KNN cosseno: dado um embedding novo, retorna o nome da classe mais próxima
se a similaridade passar do threshold. Senão retorna None (item desconhecido).

Usamos a média dos embeddings conhecidos por classe como "vetor representante".
"""
from __future__ import annotations

import numpy as np

from labels import LabelsStore


class EmbeddingClassifier:
    def __init__(self, store: LabelsStore, threshold: float = 0.65):
        self.store = store
        self.threshold = threshold

    def classify(self, embedding: np.ndarray) -> tuple[str | None, float]:
        """
        Retorna (nome_classe, similaridade).
        Se nenhuma classe passar do threshold, retorna (None, melhor_sim).
        """
        all_labels = self.store.get_all()
        if not all_labels:
            return None, 0.0

        best_name: str | None = None
        best_sim = -1.0

        for name, embeddings in all_labels.items():
            if not embeddings:
                continue
            arr = np.stack(embeddings)
            mean = arr.mean(axis=0)
            norm = float(np.linalg.norm(mean))
            if norm < 1e-9:
                continue
            mean = mean / norm

            sim = float(np.dot(embedding, mean))
            if sim > best_sim:
                best_sim = sim
                best_name = name

        if best_sim < self.threshold:
            return None, best_sim
        return best_name, best_sim
