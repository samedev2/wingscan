/**
 * PTZOverlay: desenha em cima do vídeo:
 *  - retângulo que mostra a "janela de zoom" (onde o foco do PTZ está);
 *  - HUD com valores de pan/tilt/zoom e dicas de teclas;
 *  - crosshair central.
 *
 * Não processa pixels do MJPEG: o vídeo real fica no <img>, este canvas
 * serve apenas como guia visual do PTZ.
 */
import type { PTZState } from "./VirtualPTZ";

export class PTZOverlay {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private state: PTZState = { pan: 0, tilt: 0, zoom: 1 };

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Falha ao obter contexto 2D do canvas overlay");
    this.ctx = ctx;
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  update(state: PTZState): void {
    this.state = state;
    this.render();
  }

  private resize(): void {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.round(rect.height * dpr));
  }

  private render(): void {
    const { ctx, canvas } = this;
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const zoom = Math.max(1, this.state.zoom);
    const boxW = w / zoom;
    const boxH = h / zoom;
    // Pan desloca o centro da viewport para a esquerda/direita.
    // Tilt: +Y = olhar para cima (viewport sobe) — convenção espacial.
    const cx = w / 2 - this.state.pan * (w - boxW);
    const cy = h / 2 + this.state.tilt * (h - boxH);

    // Área escurecida fora da janela de zoom
    ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
    ctx.fillRect(0, 0, w, h);
    ctx.clearRect(cx, cy, boxW, boxH);

    // Borda da janela de zoom
    ctx.strokeStyle = "#58a6ff";
    ctx.lineWidth = 2;
    ctx.strokeRect(cx, cy, boxW, boxH);

    // Crosshair central da viewport
    ctx.strokeStyle = "rgba(88, 166, 255, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx + boxW / 2 - 14, cy + boxH / 2);
    ctx.lineTo(cx + boxW / 2 + 14, cy + boxH / 2);
    ctx.moveTo(cx + boxW / 2, cy + boxH / 2 - 14);
    ctx.lineTo(cx + boxW / 2, cy + boxH / 2 + 14);
    ctx.stroke();

    // HUD canto superior esquerdo
    const hudW = 240;
    const hudH = 78;
    ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
    ctx.fillRect(8, 8, hudW, hudH);
    ctx.fillStyle = "#3fb950";
    ctx.font = "14px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.fillText(`pan  = ${this.state.pan.toFixed(3)}`, 16, 28);
    ctx.fillText(`tilt = ${this.state.tilt.toFixed(3)}`, 16, 48);
    ctx.fillText(`zoom = ${this.state.zoom.toFixed(2)}x`, 16, 68);
  }
}
