"""Servidor local do Monitoramento do Pinteiro.

Só usa a biblioteca padrão do Python: o modo demo funciona sem instalar nada.
Uso:  python servidor.py [--porta 8765] [--host 127.0.0.1] [--abrir]
"""

import argparse
import importlib.util
import json
import mimetypes
import queue
import re
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from monitor import treino
from monitor.analise import COMPORTAMENTOS
from monitor.eventos import Barramento
from monitor.pipeline import Monitor

RAIZ = Path(__file__).resolve().parent
WEB = RAIZ / "web"
VIDEOS = RAIZ / "videos"
EXTENSOES_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
TAMANHO_MAX_UPLOAD = 4 * 1024**3


def carregar_config() -> dict:
    with open(RAIZ / "config.json", encoding="utf-8") as f:
        return json.load(f)


class App:
    def __init__(self):
        self.cfg = carregar_config()
        self.bus = Barramento(RAIZ / "logs")
        self.monitor = Monitor(self.cfg, RAIZ, self.bus)
        VIDEOS.mkdir(exist_ok=True)

    def info(self) -> dict:
        modelos = self.cfg["modelos"]
        return {
            "status": self.monitor.status,
            "zonas": self.cfg["zonas"],
            "comportamentos": COMPORTAMENTOS,
            "videos": sorted(p.name for p in VIDEOS.iterdir() if p.suffix.lower() in EXTENSOES_VIDEO),
            "dependencias": {nome: importlib.util.find_spec(nome) is not None for nome in ("cv2", "ultralytics")},
            "modelos": {
                chave: {"caminho": modelos.get(chave), "existe": bool(modelos.get(chave)) and (RAIZ / modelos[chave]).is_file()}
                for chave in ("deteccao", "comportamento")
            },
        }

    def treino_resumo(self) -> dict:
        imagens = treino.listar_imagens(RAIZ)
        revisados = sum(1 for i in imagens if i["revisado"])
        return {
            "classes": treino.CLASSES,
            "imagens": imagens,
            "revisados": revisados,
            "pendentes": len(imagens) - revisados,
            "videos": sorted(p.name for p in VIDEOS.iterdir() if p.suffix.lower() in EXTENSOES_VIDEO),
            "job": treino.status_job(),
            "estatisticas": treino.estatisticas_classes(RAIZ),
        }


def criar_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Pinteiro/1.0"

        def log_message(self, formato, *args):  # silencia o log HTTP padrão
            pass

        # ---- utilitários ----------------------------------------------------
        def _json(self, dados, status=HTTPStatus.OK):
            corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _erro(self, mensagem, status=HTTPStatus.BAD_REQUEST):
            self._json({"ok": False, "erro": mensagem}, status)

        def _ler_json(self) -> dict:
            tamanho = int(self.headers.get("Content-Length") or 0)
            if not tamanho:
                return {}
            return json.loads(self.rfile.read(tamanho).decode("utf-8"))

        def _arquivo_estatico(self, caminho: str):
            alvo = (WEB / (caminho.lstrip("/") or "index.html")).resolve()
            if not alvo.is_relative_to(WEB) or not alvo.is_file():
                return self._erro("Não encontrado", HTTPStatus.NOT_FOUND)
            corpo = alvo.read_bytes()
            tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
            if tipo.startswith("text/") or tipo.endswith("javascript"):
                tipo += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(corpo)

        # ---- rotas ------------------------------------------------------------
        def do_GET(self):
            rota = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if rota == "/api/info":
                return self._json(app.info())
            if rota == "/api/eventos":
                return self._sse()
            if rota == "/api/log.jsonl":
                corpo = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in app.bus.historico()).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="log-pinteiro.jsonl"')
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                return self.wfile.write(corpo)
            if rota == "/api/treino/resumo":
                return self._json(app.treino_resumo())
            if rota == "/api/treino/status":
                return self._json(treino.status_job())
            if rota == "/api/treino/imagem":
                return self._imagem_treino(query.get("nome", [""])[0])
            if rota == "/api/treino/rotulo":
                try:
                    return self._json({"caixas": treino.carregar_rotulos(RAIZ, query.get("nome", [""])[0])})
                except ValueError as e:
                    return self._erro(str(e))
            return self._arquivo_estatico(rota)

        def do_POST(self):
            rota = urlparse(self.path).path
            try:
                if rota == "/api/iniciar":
                    return self._iniciar(self._ler_json())
                if rota == "/api/parar":
                    app.monitor.parar()
                    return self._json({"ok": True})
                if rota == "/api/upload":
                    return self._upload()
                if rota == "/api/treino/extrair":
                    return self._treino_extrair(self._ler_json())
                if rota == "/api/treino/rotulo":
                    return self._treino_salvar_rotulo(self._ler_json())
                if rota == "/api/treino/treinar":
                    return self._treino_treinar(self._ler_json())
                if rota == "/api/treino/promover":
                    return self._treino_promover(self._ler_json())
            except (ValueError, json.JSONDecodeError) as e:
                return self._erro(str(e))
            return self._erro("Rota não encontrada", HTTPStatus.NOT_FOUND)

        # ---- treino -------------------------------------------------------
        def _imagem_treino(self, nome: str):
            alvo = (treino.pasta_imagens(RAIZ) / Path(nome or "").name).resolve()
            if not alvo.is_relative_to(treino.pasta_imagens(RAIZ)) or not alvo.is_file():
                return self._erro("Imagem não encontrada", HTTPStatus.NOT_FOUND)
            corpo = alvo.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(corpo)

        def _treino_extrair(self, pedido: dict):
            videos = pedido.get("videos") or [p.name for p in VIDEOS.iterdir() if p.suffix.lower() in EXTENSOES_VIDEO]
            novos = treino.extrair_quadros(RAIZ, videos, intervalo_s=float(pedido.get("intervalo_s", 1.5)),
                                            limite_por_video=int(pedido.get("limite_por_video", 150)))
            app.bus.log("sistema", "info", f"{len(novos)} quadros novos extraídos de {len(videos)} vídeo(s)")
            rotulados = treino.pre_rotular(RAIZ, app.cfg["modelos"], novos)
            return self._json({"ok": True, "novos": len(novos), "rotulados": rotulados})

        def _treino_salvar_rotulo(self, pedido: dict):
            treino.salvar_rotulos(RAIZ, pedido.get("nome", ""), pedido.get("caixas", []))
            return self._json({"ok": True})

        def _treino_treinar(self, pedido: dict):
            resumo = treino.iniciar_treino(
                RAIZ, app.cfg["modelos"], app.bus,
                epocas=int(pedido.get("epocas", 25)), imgsz=int(pedido.get("imgsz", 960)),
                batch=int(pedido.get("batch", 4)),
            )
            return self._json({"ok": True, **resumo})

        def _treino_promover(self, pedido: dict):
            treino.promover_modelo(RAIZ, pedido.get("pesos", ""))
            app.bus.log("sistema", "info", f"Modelo em uso atualizado a partir de {pedido.get('pesos')}")
            return self._json({"ok": True})

        def _iniciar(self, pedido: dict):
            tipo = pedido.get("tipo")
            if tipo == "demo":
                origem = None
            elif tipo == "arquivo":
                nome = Path(str(pedido.get("nome", ""))).name
                origem = VIDEOS / nome
                if not nome or not origem.is_file():
                    return self._erro(f"Vídeo não encontrado na pasta videos/: {nome or '(vazio)'}")
                origem = str(origem)
            elif tipo == "webcam":
                origem = int(pedido.get("indice", 0))
            elif tipo == "rtsp":
                origem = str(pedido.get("url", "")).strip()
                if not re.match(r"^(rtsp|rtsps|http|https)://", origem):
                    return self._erro("Informe uma URL rtsp:// ou http(s)://")
            else:
                return self._erro("Tipo de fonte inválido")
            app.monitor.iniciar(tipo, origem)
            return self._json({"ok": True})

        def _upload(self):
            nome = Path(unquote(self.headers.get("X-Nome-Arquivo", ""))).name
            nome = re.sub(r"[^\w.\- ]", "_", nome).strip()
            tamanho = int(self.headers.get("Content-Length") or 0)
            if Path(nome).suffix.lower() not in EXTENSOES_VIDEO:
                return self._erro(f"Formato não suportado. Use: {', '.join(sorted(EXTENSOES_VIDEO))}")
            if not 0 < tamanho <= TAMANHO_MAX_UPLOAD:
                return self._erro("Arquivo vazio ou maior que 4 GB")
            destino = VIDEOS / nome
            restante = tamanho
            with open(destino, "wb") as f:
                while restante > 0:
                    bloco = self.rfile.read(min(restante, 1024 * 1024))
                    if not bloco:
                        break
                    f.write(bloco)
                    restante -= len(bloco)
            if restante:
                destino.unlink(missing_ok=True)
                return self._erro("Upload interrompido")
            app.bus.log("sistema", "info", f"Vídeo recebido: {nome} ({tamanho / 1024**2:.1f} MB)", {"arquivo": nome})
            return self._json({"ok": True, "nome": nome})

        def _sse(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            fila, historico, retidos = app.bus.assinar()

            def enviar(nome, payload):
                dados = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(f"event: {nome}\ndata: {dados}\n\n".encode("utf-8"))
                self.wfile.flush()

            try:
                enviar("historico", historico)
                for nome, payload in retidos.items():
                    enviar(nome, payload)
                while True:
                    try:
                        nome, payload = fila.get(timeout=15)
                    except queue.Empty:  # mantém a conexão viva
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    enviar(nome, payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                app.bus.cancelar(fila)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Monitoramento do Pinteiro")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para acessar de outros PCs da rede")
    parser.add_argument("--porta", type=int, default=8765)
    parser.add_argument("--abrir", action="store_true", help="abre o navegador ao iniciar")
    args = parser.parse_args()
    # Console do Windows pode não ser UTF-8: um acento no log não pode derrubar o monitor.
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    app = App()
    servidor = ThreadingHTTPServer((args.host, args.porta), criar_handler(app))
    servidor.daemon_threads = True
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.porta}"
    app.bus.log("sistema", "info", f"Servidor no ar em {url}")
    if args.host == "0.0.0.0":
        app.bus.log("sistema", "aviso", "Servidor aberto para a rede local, sem autenticação")
    if args.abrir:
        webbrowser.open(url)
    try:
        servidor.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.monitor.parar()
        servidor.server_close()
        app.bus.fechar()


if __name__ == "__main__":
    main()
