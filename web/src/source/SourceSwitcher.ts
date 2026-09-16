/**
 * SourceSwitcher: modal com 3 abas para trocar a fonte de vídeo em runtime.
 *
 *   [Upload]  - multipart POST /api/source/upload
 *   [URL]    - json POST /api/source/url
 *   [Webcam] - POST /api/source/webcam (volta para camera_index)
 *
 * Após qualquer troca, dispara `onSourceChanged` (opcional) para o front
 * recarregar/avisar.
 */

interface SourceInfo {
  video_path: string | null;
  is_webcam: boolean;
  video_loop: boolean;
  camera_index: number;
  loop_count: number;
  resolution: string;
}

export class SourceSwitcher {
  private container: HTMLElement;
  private modalEl: HTMLDivElement | null = null;
  private onChange: (() => void) | null = null;
  private cvBase: string;

  constructor(container: HTMLElement, cvBase: string) {
    this.container = container;
    this.cvBase = cvBase;
  }

  setOnChange(cb: () => void): void {
    this.onChange = cb;
  }

  open(): void {
    if (this.modalEl) return;
    this.renderModal();
  }

  close(): void {
    if (this.modalEl) {
      this.modalEl.remove();
      this.modalEl = null;
    }
  }

  private renderModal(): void {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card">
        <div class="modal-header">
          <h2>Trocar fonte de vídeo</h2>
          <button class="modal-close" aria-label="Fechar">×</button>
        </div>
        <div class="modal-source-status" id="modal-source-status">carregando…</div>
        <div class="modal-tabs">
          <button class="modal-tab active" data-tab="upload">📤 Upload de arquivo</button>
          <button class="modal-tab" data-tab="url">🔗 URL pública</button>
          <button class="modal-tab" data-tab="webcam">📷 Webcam ao vivo</button>
        </div>

        <div class="modal-pane active" data-pane="upload">
          <p class="muted small">Envia um arquivo (mp4, webm, mkv, avi, mov, m4v) — até 200 MB.</p>
          <input type="file" id="src-file" accept="video/*,.mp4,.webm,.mkv,.avi,.mov,.m4v,.ogv" />
          <button class="btn-primary" id="src-upload-btn">Enviar e usar este vídeo</button>
        </div>

        <div class="modal-pane" data-pane="url">
          <p class="muted small">Cole uma URL direta de arquivo de vídeo (CC0, Wikimedia, etc.).</p>
          <input type="url" id="src-url" placeholder="https://...video.mp4" />
          <button class="btn-primary" id="src-url-btn">Baixar e usar este vídeo</button>
        </div>

        <div class="modal-pane" data-pane="webcam">
          <p class="muted small">Volta a usar a webcam ao vivo (camera_index=0 por padrão).</p>
          <button class="btn-primary" id="src-webcam-btn">Usar webcam ao vivo</button>
        </div>

        <div class="modal-feedback" id="modal-feedback"></div>
      </div>
    `;
    this.container.appendChild(overlay);
    this.modalEl = overlay;

    overlay.querySelector(".modal-close")!.addEventListener("click", () => this.close());
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) this.close();
    });

    overlay.querySelectorAll<HTMLButtonElement>(".modal-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        overlay.querySelectorAll(".modal-tab").forEach((b) => b.classList.remove("active"));
        overlay.querySelectorAll(".modal-pane").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab!;
        overlay.querySelector(`.modal-pane[data-pane="${tab}"]`)!.classList.add("active");
      });
    });

    overlay.querySelector("#src-upload-btn")!.addEventListener("click", () => this.handleUpload());
    overlay.querySelector("#src-url-btn")!.addEventListener("click", () => this.handleUrl());
    overlay.querySelector("#src-webcam-btn")!.addEventListener("click", () => this.handleWebcam());

    void this.refreshSource();
  }

  private async refreshSource(): Promise<void> {
    const el = this.modalEl?.querySelector("#modal-source-status");
    if (!el) return;
    try {
      const r = await fetch(`${this.cvBase}/api/source`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const s = (await r.json()) as SourceInfo;
      const label = s.is_webcam
        ? `webcam (index=${s.camera_index}) · ${s.resolution}`
        : `${this.shorten(s.video_path)} · ${s.resolution} · loop #${s.loop_count}`;
      el.textContent = `Fonte atual: ${label}`;
    } catch (e) {
      el.textContent = `Fonte atual: (erro: ${e})`;
    }
  }

  private shorten(p: string | null): string {
    if (!p) return "?";
    const parts = p.replace(/\\/g, "/").split("/");
    return parts.length <= 3 ? p : `…/${parts.slice(-3).join("/")}`;
  }

  private feedback(msg: string, kind: "info" | "ok" | "bad"): void {
    const el = this.modalEl?.querySelector("#modal-feedback");
    if (!el) return;
    el.className = `modal-feedback ${kind}`;
    el.textContent = msg;
  }

  private async handleUpload(): Promise<void> {
    const fileInput = this.modalEl!.querySelector("#src-file") as HTMLInputElement;
    const file = fileInput.files?.[0];
    if (!file) {
      this.feedback("Selecione um arquivo primeiro.", "bad");
      return;
    }
    if (file.size > 200 * 1024 * 1024) {
      this.feedback("Arquivo > 200 MB.", "bad");
      return;
    }
    this.feedback(`Enviando ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)…`, "info");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${this.cvBase}/api/source/upload`, { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
      this.feedback(`✓ Fonte trocada: ${data.source.width}×${data.source.height} @ ${data.source.fps} fps`, "ok");
      await this.refreshSource();
      this.onChange?.();
    } catch (e) {
      this.feedback(`Falha: ${e}`, "bad");
    }
  }

  private async handleUrl(): Promise<void> {
    const urlInput = this.modalEl!.querySelector("#src-url") as HTMLInputElement;
    const url = urlInput.value.trim();
    if (!url) {
      this.feedback("Cole uma URL.", "bad");
      return;
    }
    this.feedback(`Baixando ${this.shorten(url)}… (pode demorar)`, "info");
    try {
      const r = await fetch(`${this.cvBase}/api/source/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
      this.feedback(`✓ Fonte trocada: ${data.source.width}×${data.source.height} @ ${data.source.fps} fps`, "ok");
      await this.refreshSource();
      this.onChange?.();
    } catch (e) {
      this.feedback(`Falha: ${e}`, "bad");
    }
  }

  private async handleWebcam(): Promise<void> {
    this.feedback("Trocando para webcam ao vivo…", "info");
    try {
      const r = await fetch(`${this.cvBase}/api/source/webcam`, { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
      this.feedback("✓ Webcam ativa.", "ok");
      await this.refreshSource();
      this.onChange?.();
    } catch (e) {
      this.feedback(`Falha: ${e}`, "bad");
    }
  }
}
