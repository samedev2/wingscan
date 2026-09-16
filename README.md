# Monitoramento do Pinteiro

Painel web simples para detectar e acompanhar continuamente cada ave do pinteiro (pinto, galinha e galo), ver o comportamento de cada uma (comendo, bebendo, andando, descansando, agitado) e acompanhar **em tempo real o log de tudo o que está entrando no sistema**.

- **Modo demo**: simula um lote com pintos saudáveis e doentes. Não precisa de câmera, modelo nem de instalar nada.
- **Modo real**: arquivo de vídeo, webcam ou câmera IP (RTSP), com um detector YOLO treinado para o pinteiro (`modelos/pinteiro.pt`) e rastreamento por ave.

## Rodar (demo)

Requer Python 3.10+.

```bash
python servidor.py --abrir
```

Ou dê dois cliques em `iniciar.bat`. O painel abre em http://localhost:8765. Escolha **Demo (simulada)** e clique em **Iniciar**.

Na demo, os limites dos alertas são curtos (sem comer > 100 s, sem beber > 150 s, parado > 50 s). Assim, em 1 a 2 minutos os pintos "doentes" (que quase não comem e ficam parados) já disparam alertas. A cada 1,5 a 3 minutos acontece um evento de pânico no lote.

## A tela

| Área | O que mostra |
|---|---|
| **Fonte** | Escolha da entrada, envio de vídeo e checagem do que falta (OpenCV, Ultralytics, modelos). |
| **Indicadores** | Aves detectadas no quadro atual (por tipo: pintos, galinhas, galo), contagem por comportamento e alertas ativos. |
| **Visão ao vivo** | Quadro com caixas, ID, tipo (galinha/galo) e comportamento. Zonas de comedouro e bebedouro tracejadas. Ave com alerta fica com contorno vermelho tracejado. |
| **Aves rastreadas** | Por ave: tipo, estado atual, há quanto tempo, tempo sem comer e sem beber, distribuição do tempo e status. |
| **Log** | Stream em tempo real, com filtros, busca, pausa e download. Clique em uma linha para ver os dados em JSON. |

### Tipos de evento do log

| Tipo | Quando aparece |
|---|---|
| **Sistema** | Início e fim, fonte aberta (resolução, fps), modelos carregados (com as classes), erros. |
| **Entrada** | A cada 2 s: frame, tempo do vídeo, aves detectadas por tipo, contagem por comportamento, fps e tempo de inferência. No JSON vêm as detecções cruas (ID, classe, confiança). |
| **Comportamento** | Mudança de estado de uma ave (ex.: `Galinha #3: andando → comendo (após 12s)`). Entrada e saída de cena ficam em *Debug*. |
| **Alerta** | Disparo e encerramento de alertas (sem comer, sem beber, parada, lote agitado). |

Todo evento também é gravado em `logs/eventos-AAAA-MM-DD.jsonl`, uma linha JSON por evento.

## Vídeo real

1. Instale as dependências. Isso baixa o PyTorch, então demora na primeira vez:

   ```bash
   pip install -r requirements.txt
   ```

2. O detector já vem em `modelos/pinteiro.pt` (YOLO11n com 3 classes: `pinto`, `galinha`, `galo`).

3. No painel, escolha **Arquivo de vídeo** (envie pelo botão ou copie para a pasta `videos/`), **Webcam** (índice 0, 1…) ou **Câmera IP** (URL `rtsp://…`).

- **Arquivo**: processado na ordem, usando o tempo do próprio vídeo. Se a CPU for mais lenta que o vídeo, ele só demora mais; os tempos da análise continuam corretos.
- **Webcam e câmera IP**: sempre é processado o quadro mais recente. Os quadros intermediários são descartados, então o painel não fica atrasado em relação à câmera. A senha da URL RTSP aparece mascarada no painel e no log.

A linha de checagem abaixo da fonte mostra ✓ ou ✗ para cada requisito. Se faltar algo, o erro aparece no log como *Sistema*.

**Ajuste as zonas.** Em `config.json`, `zonas.comedouro` e `zonas.bebedouro` são listas de retângulos `[x1, y1, x2, y2]` em coordenadas normalizadas (0 a 1). Eles aparecem tracejados na visão ao vivo: ajuste até cobrirem o comedouro e o bebedouro da sua câmera. As zonas atuais estão ajustadas para o pinteiro usado no treino.

## Como funciona

```
Fonte (demo | arquivo | webcam | RTSP)
   └─► Detector YOLO treinado (modelos/pinteiro.pt: pinto, galinha, galo)
          └─► Rastreador (monitor/rastreadores/pinteiro_bytetrack.yaml)
                 └─► Identidade persistente (monitor/identidade.py): o mesmo número para a mesma ave
                        └─► Análise: classe da trilha, comportamento por zona/movimento, tempos por ave, alertas
                               └─► Barramento de eventos ─► log JSONL + painel (Server-Sent Events)
```

- **Foco atual: acompanhamento.** O objetivo é que cada ave (pinto, galinha, galo) mantenha o mesmo ID do começo ao fim. Essa é a base para, numa próxima etapa, treinar a detecção de ave doente em cima do histórico de cada uma.
- **Rastreador:** o cercado é fechado e a câmera é fixa, então as aves não saem de cena. O rastreador mantém uma ave sumida (oclusão, embaixo de uma galinha) por ~6 s antes de desistir e só cria trilha nova com detecção confiante.
- **Identidade persistente:** mesmo assim o rastreador às vezes cria um ID novo, principalmente com pintos amontoados. Como nenhuma ave entra nem sai, esse ID novo é ligado à ave perdida mais próxima da mesma classe, num raio que cresce com o tempo que ela ficou sumida (`rastreio.raio_base` + `rastreio.raio_por_segundo`, em fração da diagonal do quadro). Para classes de população conhecida (`rastreio.populacao`, hoje 7 galinhas e 1 galo), uma classe completa nunca ganha ave nova. Caixas duplicadas da mesma ave são descartadas.
- **Classe da trilha:** a classe mostrada é a mais votada ao longo da trilha, então uma troca galinha/galo num quadro isolado não muda a ave.
- **Comportamento** (comendo, bebendo, andando, descansando, agitado) vem de uma heurística: centro da caixa numa zona de comedouro vira *comendo*, numa zona de bebedouro vira *bebendo*, e a velocidade separa *agitado*, *andando* e *descansando*. O estado é o mais frequente nas últimas `janela_suavizacao` observações, para não ficar "piscando".
- **Modelo de comportamento/doença:** desligado (`"comportamento": ""`). O `best_seg.pt` do chicken-detector se mostrou ruidoso (indicava `newcastle` e `Breathing` em aves só paradas) e foi treinado com galinhas, não pintos. A detecção de ave doente será treinada numa etapa própria.

### Resultado do acompanhamento

Num vídeo de teste do pinteiro (câmera de cima, 362 quadros, ~15 s):

| | Só rastreador | + identidade persistente (configuração atual) |
|---|---|---|
| Galinhas (7) | 7 IDs, 7/7 do início ao fim | **7 IDs, 7/7** |
| Galo (1) | 2 IDs, trocou de número | **1 ID, 1/1** |
| Pintos (~40–45) | 78 IDs, 30 do início ao fim | **48 IDs, 38 do início ao fim** |
| Velocidade (CPU) | 16 quadros/s | 16 quadros/s (~12 no painel, que também envia a imagem) |

O número de pintos visíveis varia ao longo do vídeo (pintos se amontoando no anel do comedouro), então parte dos IDs a mais é esperada. O BoT-SORT com re-identificação por aparência (`monitor/rastreadores/pinteiro_botsort.yaml`) deu resultado equivalente e é mais lento.

Detector (`modelos/pinteiro.pt`, validação em quadros fora do treino): precisão/revocação de 0,99/1,00 para galo, 0,96/0,99 para galinha e 0,86/0,88 para pinto.

### Alertas (`config.json`)

| Regra | Chave | Padrão | Demo |
|---|---|---|---|
| Ave sem comer | `limites.sem_comer_s` | 30 min | 100 s |
| Ave sem beber | `limites.sem_beber_s` | 30 min | 150 s |
| Ave parada | `limites.imovel_s` | 20 min | 50 s |
| Lote agitado | `analise.agitacao_fracao` | ≥ 50% das aves visíveis agitadas por 1,5 s | igual |

Outras chaves úteis: `modelos.confianca`, `modelos.rastreador`, `rastreio.identidade_persistente`, `rastreio.populacao`, `modelos.dispositivo` (`""` = automático, `"cpu"`, `"0"` para a GPU), `video.processar_a_cada_n_frames` (use 1 para o rastreador não perder aves), `video.tempo_real_arquivo`, `analise.vel_andando` e `analise.vel_agitado` (fração da largura do quadro por segundo), `analise.esquecer_apos_s`.

## Sobre os repositórios de referência

- **[drcardinal/chicken-detector](https://github.com/drcardinal/chicken-detector)**: pesos `best.pt` (YOLOv8n, classe única `chicken`) e `best_seg.pt` (YOLO11n, 21 classes de comportamento e doença). Nos testes com o pinteiro, o `best.pt` achou só 3 de ~35 pintos e o `best_seg.pt` foi muito ruidoso, então foram substituídos pelo detector treinado.
- **[FlorianShepherd/ChickEye](https://github.com/FlorianShepherd/ChickEye)**: os pesos dele identificam 4 galinhas específicas, então não servem para outro lote. A arquitetura (RTSP ou webcam com streaming) e a ferramenta de rotulagem e treino são boas referências.

## Limitações conhecidas

- **Treinado com um único pinteiro.** Em outra câmera, luz ou lote o detector provavelmente vai errar mais; o ideal é retreinar com imagens do local.
- **Pintos amontoados** (anel em volta do comedouro) ainda geram caixas encavaladas e algumas trocas de ID. Nesses casos os tempos daquela ave recomeçam no ID novo.
- A **população de adultos** (`rastreio.populacao`) está fixada para este lote (7 galinhas, 1 galo). Mude se o lote mudar.
- Não há autenticação. Por padrão o servidor só escuta em `127.0.0.1`. Use `--host 0.0.0.0` apenas em rede confiável.

## Estrutura

```
servidor.py            servidor HTTP + API + SSE (biblioteca padrão)
config.json            modelos, zonas, limites e parâmetros
monitor/
  fontes.py            demo simulada e leitura de vídeo (OpenCV)
  detector.py          YOLO + rastreador (+ modelo de comportamento opcional)
  analise.py           estado por ave, classe da trilha, suavização e regras de alerta
  identidade.py        identidade persistente: liga IDs novos do rastreador à ave perdida
  rastreadores/        configurações do rastreador ajustadas para o cercado
  pipeline.py          laço fonte → detector → análise → eventos
  eventos.py           barramento: histórico, JSONL e distribuição
web/                   painel (HTML, CSS e JS puros)
modelos/pinteiro.pt    detector treinado (pinto, galinha, galo)
videos/                vídeos enviados pelo painel (não versionado)
logs/                  eventos-AAAA-MM-DD.jsonl (não versionado)
```

### API

| Método | Rota | Uso |
|---|---|---|
| GET | `/api/info` | status, zonas, vídeos disponíveis, dependências e modelos |
| GET | `/api/eventos` | SSE: `historico`, `log`, `status`, `estado` (quadro + detecções + resumo) |
| GET | `/api/log.jsonl` | baixa o histórico em memória |
| POST | `/api/iniciar` | `{"tipo":"demo"}`, `{"tipo":"arquivo","nome":"x.mp4"}`, `{"tipo":"webcam","indice":0}` ou `{"tipo":"rtsp","url":"rtsp://..."}` |
| POST | `/api/parar` | para o monitoramento |
| POST | `/api/upload` | corpo = arquivo de vídeo, header `X-Nome-Arquivo` |
