# Controle de Movimento

Sistema de **monitoramento + contagem** de itens passando pela câmera, com controle
**PTZ virtual** sobre o stream de vídeo. Pensado para granjas (galinhas, estoque,
movimentação de pessoal etc.) — mas genérico para qualquer cenário de contagem por
cruzamento de linha.

## Arquitetura

```
controle-de-movimento/
├── spatial-controls/   ← lib third-party (clonada, editável). Fornece input/damping/keybindings
├── cv-service/         ← Python + YOLOv8 + ByteTrack + FastAPI (porta 8000)
│   ├── detector.py     ← wrapper YOLOv8 (COCO pré-treinado: bird, person, …)
│   ├── tracker.py      ← wrapper ByteTrack via supervision
│   ├── counter.py      ← conta IN/OUT por cruzamento de linha virtual
│   ├── storage.py      ← grava JSON + CSV por turno
│   └── app.py          ← expõe /video_feed (MJPEG), /ws/events, /api/state
└── web/                ← Node + Vite + Three.js (porta 5175)
    └── src/
        ├── ptz/VirtualPTZ.ts  ← usa spatial-controls como cérebro de pan/tilt/zoom
        ├── ptz/Overlay.ts      ← canvas com viewport PTZ + HUD
        ├── stream/MjpegClient.ts / EventsClient.ts
        └── counter/CounterPanel.ts
```

Fluxo:
1. Webcam → YOLOv8 detecta (bird/person) → ByteTrack mantém IDs estáveis
2. Cruzamento de linha virtual por track gera evento IN/OUT
3. Stream MJPEG anotado + WebSocket de eventos vão para o front
4. Front mostra vídeo, painel de contagem e overlay PTZ (controle WASD/↑↓/R/0 + drag/scroll)
5. Cada turno grava `<turno_id>.json` + `<turno_id>.csv` em `cv-service/data/turnos/`

## Como rodar (Windows)

Em **dois terminais**:

**Terminal 1 — cv-service (Python):**
```bat
cd cv-service
run.bat
```
Na primeira vez cria `.venv`, instala deps e sobe `http://127.0.0.1:8000`.

**Terminal 2 — web (Node):**
```bat
cd web
npm install
npm run dev
```
Abre `http://127.0.0.1:5175`.

O Vite faz proxy de `/cv/*` → `http://127.0.0.1:8000`, então o front fala direto.

## Configuração por env vars (cv-service)

| Variável              | Default        |
|-----------------------|----------------|
| `CV_CAMERA_INDEX`     | `0`            |
| `CV_WIDTH`            | `1280`         |
| `CV_HEIGHT`           | `720`          |
| `CV_FPS`              | `30`           |
| `CV_MODEL`            | `yolov8n.pt`   |
| `CV_CONFIDENCE`       | `0.4`          |
| `CV_CLASSES`          | `bird,person`  |
| `CV_LINE_ORIENTATION` | `horizontal`   |
| `CV_LINE_POSITION`    | `0.5`          |
| `CV_CAMERA_ID`        | `webcam-0`     |
| `CV_TURNO_DIR`        | `data/turnos`  |

Exemplo: linha vertical a 70% do frame, contar só galinhas:
```bat
set CV_LINE_ORIENTATION=vertical
set CV_LINE_POSITION=0.7
set CV_CLASSES=bird
run.bat
```

## GPU (RTX 3050) — opcional

Após instalar o requirements.txt normal:
```bat
pip install -r requirements-gpu.txt
```
Valide com `python -c "import torch; print(torch.cuda.is_available())"`.

## Atalhos do front

- **A/D** ou **←/→** — pan (esquerda/direita)
- **W/S** — zoom in / zoom out
- **↑/↓** — tilt (cima/baixo)
- **R** — boost (velocidade 2x)
- **0** — reset PTZ
- **drag** no vídeo — pan livre
- **scroll** no vídeo — zoom

## Limitações conhecidas

- COCO pré-treinado detecta **bird** (classe 14). Galinha conta como ave — funciona
  razoavelmente bem em ambiente controlado. Para produção real, treine um modelo
  fine-tune com fotos da sua granja.
- Webcam única no MVP. Suporte a múltiplas câmeras exige rodar mais de uma instância
  do cv-service (porta diferente) e um pequeno agregador.
- Persistência por enquanto em JSON/CSV por turno. Quando o formato estabilizar,
  migramos para SQLite/Postgres.

## Próximos passos

- [ ] Modelo fine-tune pra galinha/caixa
- [ ] Multi-câmera (1 cv-service por porta + agregador)
- [ ] Migrar CSV → SQLite
- [ ] Suporte a RTSP (câmeras IP) — vai precisar de ffmpeg/go2rtc
- [ ] Alertas (e-mail/Telegram) quando IN/OUT ultrapassa limite
