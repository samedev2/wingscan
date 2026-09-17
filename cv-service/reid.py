"""ReID visual para wingscam.

Usa ResNet50 ImageNet (sem fine-tune) para extrair embeddings 2048-D de cada
crop de deteccao. Compara com identidades conhecidas no SQLite e:
  - Se similaridade > threshold (0.85): atribui o MESMO identity_id
  - Se dissimilar: cria novo identity_id

Vantagens vs label.json:
  - Sobrevive a reset de loop (track_id local eh descartado, identity_id persiste)
  - Sobrevive entre cameras (multi-camera source)
  - Nao precisa treinar nada pra comecar

Captura individual por galinha:
  - Cache em memoria com os ultimos N crops (full-res JPEG) por identidade
  - Endpoints: /api/identity/{id}/thumbnail, /capture, /captures

Backward compat: ReIDEncoder.encode(frame, bbox) -> embedding
  (interface usada por namer.py na versao anterior)
"""
from __future__ import annotations

import io
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Optional
import numpy as np
import cv2
import torch
import torchvision
from torchvision.models import resnet50, ResNet50_Weights


# Threshold de similaridade pra considerar mesma identidade
DEFAULT_THRESHOLD = 0.85
# Quantos segundos pra "lembrar" de uma identidade recente (evita recontar)
DEFAULT_RECENT_WINDOW_S = 5.0
# Quantos crops full-res manter por identidade no cache (rotating buffer)
DEFAULT_CAPTURES_PER_IDENTITY = 8


class ReIDDatabase:
    """Persiste identidades + embeddings no SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._ensure_schema()
        self._recent_cache: dict[int, dict] = {}  # identity_id -> {last_seen, embedding}

    def _ensure_schema(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS identities (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_seen      TEXT    NOT NULL,
                    last_seen       TEXT    NOT NULL,
                    type            TEXT,            -- pinto | galinha | galo
                    celeiro_id      INTEGER,
                    cor             TEXT,            -- branca | marrom | preta | carijo | cinza
                    name            TEXT    UNIQUE,  -- identity-N
                    embedding       BLOB    NOT NULL,  -- 2048 * float32 = 8192 bytes
                    thumbnail       BLOB,
                    total_frames    INTEGER DEFAULT 0,
                    total_seconds   REAL    DEFAULT 0.0,
                    camera_id       TEXT             -- ultima camera que viu
                );
                CREATE INDEX IF NOT EXISTS idx_identities_type ON identities(type);

                CREATE TABLE IF NOT EXISTS identity_seen (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_id     INTEGER NOT NULL REFERENCES identities(id),
                    ts              TEXT    NOT NULL,
                    camera_id       TEXT,
                    track_id        INTEGER,
                    confidence      REAL
                );
                CREATE INDEX IF NOT EXISTS idx_identity_seen_id_ts
                    ON identity_seen(identity_id, ts);
            """)
            con.commit()
        finally:
            con.close()

    def list_active(self, type_filter: Optional[str] = None) -> list[dict]:
        """Retorna identidades ativas (vistas nas últimas 24h)."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            q = """SELECT id, name, type, cor, first_seen, last_seen,
                          total_frames, total_seconds, camera_id
                   FROM identities
                   WHERE last_seen > datetime('now', '-1 day')"""
            params: tuple = ()
            if type_filter:
                q += " AND type = ?"
                params = (type_filter,)
            q += " ORDER BY id"
            return [dict(r) for r in con.execute(q, params).fetchall()]
        finally:
            con.close()

    def get_embedding(self, identity_id: int) -> Optional[np.ndarray]:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT embedding FROM identities WHERE id = ?",
                (identity_id,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return np.frombuffer(row[0], dtype=np.float32)
        finally:
            con.close()

    def get_thumbnail(self, identity_id: int) -> Optional[bytes]:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT thumbnail FROM identities WHERE id = ?",
                (identity_id,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return bytes(row[0])
        finally:
            con.close()

    def insert(self, embedding: np.ndarray, type_: str, name: str,
               thumbnail_jpeg: Optional[bytes] = None,
               camera_id: Optional[str] = None) -> int:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.execute(
                """INSERT INTO identities
                    (first_seen, last_seen, type, name, embedding,
                     thumbnail, camera_id, total_frames, total_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0.0)""",
                (now, now, type_, name,
                 embedding.astype(np.float32).tobytes(),
                 thumbnail_jpeg, camera_id),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    def update_seen(self, identity_id: int, camera_id: Optional[str] = None,
                    track_id: Optional[int] = None, confidence: Optional[float] = None):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "UPDATE identities SET last_seen = ?, total_frames = total_frames + 1, camera_id = COALESCE(?, camera_id) WHERE id = ?",
                (now, camera_id, identity_id),
            )
            con.execute(
                "INSERT INTO identity_seen (identity_id, ts, camera_id, track_id, confidence) VALUES (?, ?, ?, ?, ?)",
                (identity_id, now, camera_id, track_id, confidence),
            )
            con.commit()
        finally:
            con.close()


class ReIDEncoder:
    """Backwards-compatible feature extractor (interface usada por namer.py).

    Internamente usa ResNet50 ImageNet (substitui MobileNetV2).
    Mantem o metodo .encode(frame, bbox) -> np.ndarray (2048-D, L2-normalizado).
    """

    EMBEDDING_DIM = 2048

    def __init__(self, device: Optional[str] = None):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        weights = ResNet50_Weights.IMAGENET1K_V2
        base = resnet50(weights=weights)
        # Remove a classifier head; queremos o pooled feature 2048-D
        self.model = torch.nn.Sequential(*list(base.children())[:-1])
        self.model.eval().to(self.device)
        self.transform = weights.transforms()

    @torch.no_grad()
    def encode(self, frame: np.ndarray, bbox) -> np.ndarray:
        """Extrai embedding L2-normalizado do crop definido por bbox.

        frame: HxWxC uint8 (BGR do OpenCV)
        bbox: tuple (x1, y1, x2, y2) ou sv.Detections.xyxy[0]
        """
        from PIL import Image as _PIL
        x1, y1, x2, y2 = [int(round(v)) for v in bbox]
        h, w = frame.shape[:2]
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(w, x2); y2 = min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return np.zeros(self.EMBEDDING_DIM, dtype=np.float32)
        crop_bgr = frame[y1:y2, x1:x2]
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        # torchvision IMAGENET1K_V2 transforms espera PIL.Image, nao ndarray
        pil_img = _PIL.fromarray(rgb)
        x = self.transform(pil_img).unsqueeze(0).to(self.device)
        feat = self.model(x).squeeze(-1).squeeze(-1).cpu().numpy()[0]
        n = np.linalg.norm(feat) + 1e-9
        return (feat / n).astype(np.float32)


class ReIDMatcher:
    """Matcher cross-camera e cross-session: embed vs SQLite identities."""

    def __init__(self, db: ReIDDatabase, threshold: float = DEFAULT_THRESHOLD,
                 recent_window_s: float = DEFAULT_RECENT_WINDOW_S, device: str = "cuda",
                 captures_per_identity: int = DEFAULT_CAPTURES_PER_IDENTITY):
        self.db = db
        self.threshold = threshold
        self.recent_window_s = recent_window_s
        self.encoder = ReIDEncoder(device=device)
        self._cache: dict[int, dict] = {}
        # Capturas full-res por identidade: rotating buffer (JPEGs + timestamps)
        self._captures: dict[int, deque] = {}
        self.captures_per_identity = captures_per_identity
        self._load_cache()

    def _load_cache(self):
        for ident in self.db.list_active():
            emb = self.db.get_embedding(ident["id"])
            if emb is not None:
                self._cache[ident["id"]] = {
                    "embedding": emb,
                    "last_seen": ident["last_seen"],
                    "type": ident["type"],
                    "name": ident["name"],
                    "total_frames": ident["total_frames"],
                }

    def encode(self, frame: np.ndarray, bbox) -> np.ndarray:
        return self.encoder.encode(frame, bbox)

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def find_match(self, embedding: np.ndarray, type_filter: Optional[str] = None) -> Optional[dict]:
        best: Optional[dict] = None
        best_score = -1.0
        for ident_id, info in self._cache.items():
            if type_filter and info["type"] != type_filter:
                continue
            score = self._cosine(embedding, info["embedding"])
            if score > best_score:
                best_score = score
                best = {"id": ident_id, "score": score, **info}
        if best is not None and best_score >= self.threshold:
            return best
        return None

    def next_name(self, type_: str) -> str:
        prefix = type_.lower()
        existing = [k["name"] for k in self._cache.values() if k["name"].startswith(prefix + "-")]
        n = 1
        while f"{prefix}-{n}" in existing:
            n += 1
        return f"{prefix}-{n}"

    def register(self, frame: np.ndarray, bbox, embedding: np.ndarray, type_: str,
                 camera_id: Optional[str] = None) -> dict:
        """Cria nova identidade. Persiste embedding + thumbnail."""
        name = self.next_name(type_)
        thumb_jpeg = None
        try:
            x1, y1, x2, y2 = [int(round(v)) for v in bbox]
            crop_bgr = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            if crop_bgr.size > 0:
                small = cv2.resize(crop_bgr, (96, 96), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    thumb_jpeg = buf.tobytes()
        except Exception:
            pass
        identity_id = self.db.insert(embedding, type_, name, thumb_jpeg, camera_id)
        self._cache[identity_id] = {
            "embedding": embedding,
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": type_,
            "name": name,
            "total_frames": 0,
        }
        return {"id": identity_id, "name": name, "score": 1.0}

    def update_seen(self, identity_id: int, camera_id: Optional[str] = None,
                    track_id: Optional[int] = None, confidence: Optional[float] = None):
        self.db.update_seen(identity_id, camera_id, track_id, confidence)
        if identity_id in self._cache:
            self._cache[identity_id]["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._cache[identity_id]["total_frames"] += 1

    def should_count(self, identity_id: int) -> bool:
        info = self._cache.get(identity_id)
        if not info:
            return True
        try:
            from datetime import datetime
            last = datetime.fromisoformat(info["last_seen"])
            return (datetime.now() - last).total_seconds() >= self.recent_window_s
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Capturas full-res (para analise individual de cada galinha)
    # ------------------------------------------------------------------
    def _encode_crop_jpeg(self, frame: np.ndarray, bbox, quality: int = 90) -> Optional[bytes]:
        """Extrai crop da bbox e codifica como JPEG."""
        try:
            x1, y1, x2, y2 = [int(round(v)) for v in bbox]
            h, w = frame.shape[:2]
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(w, x2); y2 = min(h, y2)
            if x2 <= x1 or y2 <= y1:
                return None
            crop = frame[y1:y2, x1:x2]
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes() if ok else None
        except Exception:
            return None

    def save_capture(self, identity_id: int, frame: np.ndarray, bbox) -> bool:
        """Salva o crop atual no buffer rotativo de capturas da identidade."""
        jpeg = self._encode_crop_jpeg(frame, bbox)
        if not jpeg:
            return False
        if identity_id not in self._captures:
            self._captures[identity_id] = deque(maxlen=self.captures_per_identity)
        self._captures[identity_id].append({
            "ts": time.time(),
            "jpeg": jpeg,
        })
        return True

    def get_capture(self, identity_id: int, index: int = -1) -> Optional[dict]:
        """Retorna uma captura (default: a mais recente). index=-1 = ultimo."""
        buf = self._captures.get(identity_id)
        if not buf:
            return None
        try:
            return list(buf)[index]
        except IndexError:
            return None

    def list_captures(self, identity_id: int) -> list[dict]:
        """Lista todas as capturas em cache (mais antiga -> mais nova, SEM o jpeg bytes)."""
        buf = self._captures.get(identity_id)
        if not buf:
            return []
        return [{"idx": i, "ts": c["ts"], "size": len(c["jpeg"])} for i, c in enumerate(buf)]

    def capture_count(self, identity_id: int) -> int:
        buf = self._captures.get(identity_id)
        return len(buf) if buf else 0
