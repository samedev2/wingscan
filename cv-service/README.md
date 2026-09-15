# cv-service

Webcam → YOLOv8 + ByteTrack → contagem IN/OUT por linha virtual → stream MJPEG anotado + WebSocket de eventos.

## Endpoints

| Método | URL              | O quê                                               |
|--------|------------------|-----------------------------------------------------|
| GET    | `/api/state`     | Estado atual (contagens, classes, linha)            |
| GET    | `/video_feed`    | Stream MJPEG com bounding boxes e HUD desenhados    |
| GET    | `/api/frame.jpg` | Snapshot JPEG do último frame                       |
| WS     | `/ws/events`     | WebSocket com eventos de cruzamento de linha        |

## Configuração (env vars)

| Variável              | Default                | Descrição                                     |
|-----------------------|------------------------|-----------------------------------------------|
| `CV_CAMERA_INDEX`     | `0`                    | Índice da webcam (0 = primeira, 1 = segunda)  |
| `CV_WIDTH`            | `1280`                 | Largura requisitada                           |
| `CV_HEIGHT`           | `720`                  | Altura requisitada                            |
| `CV_FPS`              | `30`                   | FPS alvo                                      |
| `CV_MODEL`            | `yolov8n.pt`           | Modelo YOLO (auto-download no 1º uso)         |
| `CV_CONFIDENCE`       | `0.4`                  | Limiar de confiança                           |
| `CV_CLASSES`          | `bird,person`          | Classes COCO a detectar (CSV)                 |
| `CV_LINE_ORIENTATION` | `horizontal`           | `horizontal` ou `vertical`                    |
| `CV_LINE_POSITION`    | `0.5`                  | Posição da linha (0..1 do frame)              |
| `CV_CAMERA_ID`        | `webcam-0`             | Identificador da câmera (vai no JSON/CSV)     |
| `CV_TURNO_DIR`        | `data/turnos`          | Pasta onde grava `<turno>.json` + `.csv`      |
| `CV_HOST`             | `127.0.0.1`            | Host do uvicorn                               |
| `CV_PORT`             | `8000`                 | Porta do uvicorn                              |

## Como rodar (Windows)

```bat
cd cv-service
run.bat
```

Na primeira vez ele cria `.venv`, instala deps e inicia em `http://127.0.0.1:8000`.

Acesse `http://127.0.0.1:8000/api/state` para ver o estado.
Abra `http://127.0.0.1:8000/video_feed` no navegador para ver o stream anotado.

## Como rodar (manual / Linux / macOS)

```bash
cd cv-service
python -m venv .venv
source .venv/bin/activate     # ou .venv\Scripts\activate no Windows
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

## GPU (opcional, RTX 3050)

```bash
pip install -r requirements.txt
pip install -r requirements-gpu.txt
```

Valide que está usando GPU:

```python
import torch
print(torch.cuda.is_available())  # True
print(torch.cuda.get_device_name(0))
```

## Saída por turno

Em `data/turnos/`:

- `<turno_id>.csv` — uma linha por cruzamento (timestamp, track_id, classe, direção, conf)
- `<turno_id>.json` — metadados do turno + lista completa de eventos
