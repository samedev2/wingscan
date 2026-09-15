/**
 * MjpegClient: injeta um stream MJPEG num <img>. Suporta reconexão simples
 * com backoff se o stream cair.
 *
 * Espera-se que o cv-service exponha o endpoint em `${baseUrl}/video_feed`
 * (multipart/x-mixed-replace). O proxy do Vite em /cv/forward faz o mesmo
 * caminho em dev.
 */
export class MjpegClient {
  private img: HTMLImageElement;
  private baseUrl: string;
  private retryDelay = 1000;
  private disposed = false;
  private onError?: (e: unknown) => void;

  constructor(img: HTMLImageElement, baseUrl: string) {
    this.img = img;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  start(onError?: (e: unknown) => void): void {
    this.onError = onError;
    this.attach();
  }

  stop(): void {
    this.disposed = true;
    this.img.removeAttribute("src");
    this.img.src = "";
  }

  private attach(): void {
    if (this.disposed) return;
    // Cache-buster para forçar reload no reconectar
    const sep = this.baseUrl.includes("?") ? "&" : "?";
    this.img.src = `${this.baseUrl}/video_feed${sep}t=${Date.now()}`;
    this.img.onerror = (e) => {
      if (this.disposed) return;
      this.onError?.(e);
      this.img.removeAttribute("src");
      this.img.src = "";
      setTimeout(() => this.attach(), this.retryDelay);
    };
    this.img.onload = () => {
      // Conectado — reset do backoff
      this.retryDelay = 1000;
    };
  }
}
