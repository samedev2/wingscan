/**
 * JpegPollingClient: alternativa ao MJPEG. Puxa /api/frame.jpg em loop com
 * cache-buster. Mais robusto que MJPEG em <img> porque evita os quirks de
 * multipart/x-mixed-replace (extensões do navegador às vezes bloqueiam).
 *
 * Trade-off: ~50ms de latência extra vs MJPEG, mas o pipeline (YOLO + ReID)
 * já leva ~70ms por frame, então a diferença é imperceptível.
 */
export class JpegPollingClient {
  private img: HTMLImageElement;
  private baseUrl: string;
  private timer: number | null = null;
  private disposed = false;
  private intervalMs: number;

  constructor(img: HTMLImageElement, baseUrl: string, intervalMs = 120) {
    this.img = img;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.intervalMs = intervalMs;
  }

  start(): void {
    this.tick();
  }

  stop(): void {
    this.disposed = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    this.img.removeAttribute("src");
  }

  private tick(): void {
    if (this.disposed) return;
    // Bypass total do cache, força o navegador a recarregar.
    this.img.src = `${this.baseUrl}/api/frame.jpg?t=${Date.now()}`;
    this.timer = window.setTimeout(() => this.tick(), this.intervalMs);
  }
}
