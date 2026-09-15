/**
 * HeatmapOverlay: canvas que sobrepõe o heatmap retornado pelo backend
 * (PNG via /api/heatmap.png) sobre o vídeo. Refetch a cada ~1s.
 */
export class HeatmapOverlay {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private baseUrl: string;
  private timer: number | null = null;
  private disposed = false;
  private currentCls: string | null = null;
  private classes: string[] = [];

  constructor(canvas: HTMLCanvasElement, baseUrl: string) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d context");
    this.ctx = ctx;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  setClasses(classes: string[]): void {
    this.classes = classes;
  }

  setFilter(cls: string | null): void {
    this.currentCls = cls;
    this.clear();
    this.fetchOnce();
  }

  start(intervalMs = 1000): void {
    if (this.timer !== null) return;
    this.fetchOnce();
    this.timer = window.setInterval(() => this.fetchOnce(), intervalMs);
  }

  stop(): void {
    this.disposed = true;
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
    if (this.disposed) return;
    const url = this.currentCls
      ? `${this.baseUrl}/api/heatmap.png?cls=${encodeURIComponent(this.currentCls)}&t=${Date.now()}`
      : `${this.baseUrl}/api/heatmap.png?t=${Date.now()}`;
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return;
      const blob = await r.blob();
      if (this.disposed) return;
      const url2 = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        this.clear();
        this.ctx.globalAlpha = 0.55;
        this.ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height);
        this.ctx.globalAlpha = 1.0;
        URL.revokeObjectURL(url2);
      };
      img.src = url2;
    } catch {
      /* offline ok */
    }
  }
}
