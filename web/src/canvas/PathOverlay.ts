/**
 * PathOverlay: canvas que sobrepõe trilhas (linhas) por cima do vídeo.
 * Refetch a cada 250ms de /api/paths.
 */
type Path = { track_id: number; cls_name: string; points: [number, number, number][] };

const PALETTE: Record<string, string> = {
  galinha: "#fde047",
  pessoa: "#60a5fa",
  caixa: "#22d3ee",
  default: "#58a6ff",
};

function colorFor(cls: string): string {
  const k = cls.toLowerCase();
  if (k in PALETTE) return PALETTE[k];
  // hash determinístico
  let h = 0;
  for (let i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}, 70%, 60%)`;
}

export class PathOverlay {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private baseUrl: string;
  private timer: number | null = null;
  private disposed = false;
  private enabled = true;

  constructor(canvas: HTMLCanvasElement, baseUrl: string) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d context");
    this.ctx = ctx;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    if (!on) {
      this.clear();
      this.stop();
    } else if (this.timer === null && !this.disposed) {
      this.start();
    }
  }

  start(intervalMs = 250): void {
    if (this.timer !== null) return;
    this.fetchOnce();
    this.timer = window.setInterval(() => this.fetchOnce(), intervalMs);
  }

  stop(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private resize(): void {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.round(rect.height * dpr));
  }

  private clear(): void {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  private async fetchOnce(): Promise<void> {
    if (this.disposed || !this.enabled) return;
    try {
      const r = await fetch(`${this.baseUrl}/api/paths?t=${Date.now()}`, {
        cache: "no-store",
      });
      if (!r.ok) return;
      const data = (await r.json()) as { paths: Path[] };
      this.render(data.paths || []);
    } catch {
      /* offline ok */
    }
  }

  /**
   * Recebe dimensões do frame original (do cv-service) pra escalar
   * proporcionalmente ao tamanho do canvas.
   */
  private render(paths: Path[]): void {
    this.clear();
    if (paths.length === 0) return;
    // Assumimos frame 1280x720 (do cv-service config) e canvas em qualquer tamanho.
    // A escala é feita por drawImage/stretch no canvas, então os pontos do
    // backend (em coords do frame) precisam ser mapeados pro tamanho do canvas.
    // Como o canvas tem o tamanho do vídeo renderizado, e o vídeo usa object-fit:
    // contain, a escala pode ser diferente. Para MVP, usamos stretch uniforme
    // (frame sempre ocupa o canvas) — funciona se vídeo está sem letterbox.
    const frameW = 1280;
    const frameH = 720;
    const sx = this.canvas.width / frameW;
    const sy = this.canvas.height / frameH;
    for (const p of paths) {
      if (p.points.length < 2) continue;
      this.ctx.strokeStyle = colorFor(p.cls_name);
      this.ctx.lineWidth = 2;
      this.ctx.lineCap = "round";
      this.ctx.lineJoin = "round";
      this.ctx.beginPath();
      const first = p.points[0];
      this.ctx.moveTo(first[0] * sx, first[1] * sy);
      for (let i = 1; i < p.points.length; i++) {
        this.ctx.lineTo(p.points[i][0] * sx, p.points[i][1] * sy);
      }
      this.ctx.stroke();
      // ponta
      const last = p.points[p.points.length - 1];
      this.ctx.fillStyle = colorFor(p.cls_name);
      this.ctx.beginPath();
      this.ctx.arc(last[0] * sx, last[1] * sy, 4, 0, Math.PI * 2);
      this.ctx.fill();
    }
  }
}
