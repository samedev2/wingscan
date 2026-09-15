"""
ReID: extrator de embedding visual usando MobileNetV2 pré-treinado em ImageNet.

O embedding é um vetor 1280-d (saída da última camada convolucional) que captura
a aparência visual do item. Usamos distância cosseno para comparar embeddings
e decidir se duas detecções são do mesmo "tipo de item".
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms


class ReIDEncoder:
    """Extrai embedding L2-normalizado do crop de uma bbox."""

    EMBEDDING_DIM = 1280

    def __init__(self, device: str | None = None):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        weights = models.MobileNet_V2_Weights.IMAGENET1K_V1
        base = models.mobilenet_v2(weights=weights)
        # Mantém features + pooling. Descarta o classificador (não queremos classe ImageNet).
        self.model = nn.Sequential(
            base.features,
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
        )
        self.model.eval()
        self.model.to(self.device)

        self.preprocess = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

    def encode(self, frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        """
        Recebe frame BGR (H,W,3) e bbox (x1,y1,x2,y2) em pixels.
        Retorna embedding 1280-d L2-normalizado.
        """
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1 = max(0, min(int(x1), w - 1))
        y1 = max(0, min(int(y1), h - 1))
        x2 = max(0, min(int(x2), w))
        y2 = max(0, min(int(y2), h))
        if x2 <= x1 or y2 <= y1:
            return np.zeros(self.EMBEDDING_DIM, dtype=np.float32)

        crop_bgr = frame[y1:y2, x1:x2]
        crop_rgb = crop_bgr[:, :, ::-1]  # BGR -> RGB
        img = Image.fromarray(crop_rgb)
        tensor = self.preprocess(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            emb = self.model(tensor).squeeze().cpu().numpy()

        # L2-normaliza para similaridade cosseno virar dot product
        norm = float(np.linalg.norm(emb))
        if norm > 1e-9:
            emb = emb / norm
        return emb.astype(np.float32)
