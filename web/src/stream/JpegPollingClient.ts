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
  private onError?: (e: unknown) => void;
  private currentBlobUrl: string | null = null;
  private intervalMs: number;

  constructor(img: HTMLImageElement, baseUrl: string, intervalMs = 80) {
    this.img = img;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.intervalMs = intervalMs;
  }

  start(onError?: (e: unknown) => void): void {
    this.onError = onError;
    this.tick();
  }

  stop(): void {
    this.disposed = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.currentBlobUrl) {
      URL.revokeObjectURL(this.currentBlobUrl);
      this.currentBlobUrl = null;
    }
    this.img.removeAttribute("src");
  }

  private async tick(): Promise<void> {
    if (this.disposed) return;
    try {
      const r = await fetch(`${this.baseUrl}/api/frame.jpg?t=${Date.now()}`, {
        cache: "no-store",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      if (this.disposed) return;
      const newUrl = URL.createObjectURL(blob);
      this.img.onload = () => {
        if (this.currentBlobUrl) URL.revokeObjectURL(this.currentBlobUrl);
        this.currentBlobUrl = newUrl;
      };
      this.img.onerror = () => {
        URL.revokeObjectURL(newUrl);
        this.onError?.(new Error("img decode failed"));
      };
      this.img.src = newUrl;
    } catch (e) {
      this.onError?.(e);
    }
    if (!this.disposed) {
      this.timer = window.setTimeout(() => this.tick(), this.intervalMs);
    }
  }
}
