/**
 * InlineNamer: recebe lista de tracks (via WS) e permite clicar na bbox
 * no canvas pra abrir popover de nome.
 */
export interface TrackInfo {
  track_id: number;
  cls_name: string;
  bbox: [number, number, number, number];
  conf: number;
}

export class InlineNamer {
  private host: HTMLElement;
  private tracks: TrackInfo[] = [];
  private onRename: ((oldName: string, newName: string) => void) | null = null;
  private popover: HTMLDivElement | null = null;
  private canvas: HTMLCanvasElement;
  private baseUrl: string;

  constructor(host: HTMLElement, canvas: HTMLCanvasElement, baseUrl: string) {
    this.host = host;
    this.canvas = canvas;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  setTracks(tracks: TrackInfo[]): void {
    this.tracks = tracks;
  }

  setOnRename(fn: (oldName: string, newName: string) => void): void {
    this.onRename = fn;
  }

  /**
   * Handler de click no canvas: hit-test nas bboxes (do frame original 1280x720)
   * e abre popover se clicou dentro de alguma.
   */
  onCanvasClick(viewportX: number, viewportY: number): void {
    const rect = this.canvas.getBoundingClientRect();
    // canvas está sobreposto ao vídeo com object-fit: contain, então pode ter
    // letterbox. Mapeamos do viewport (canvas) para coords do frame (1280x720).
    const frameW = 1280;
    const frameH = 720;
    const sx = frameW / rect.width;
    const sy = frameH / rect.height;
    const fx = viewportX * sx;
    const fy = viewportY * sy;

    let hit: TrackInfo | null = null;
    // pega o bbox com maior área que contenha o ponto (menor = mais específico)
    let bestArea = Infinity;
    for (const t of this.tracks) {
      const [x1, y1, x2, y2] = t.bbox;
      if (fx >= x1 && fx <= x2 && fy >= y1 && fy <= y2) {
        const area = (x2 - x1) * (y2 - y1);
        if (area < bestArea) {
          bestArea = area;
          hit = t;
        }
      }
    }
    if (hit) this.openPopover(hit, viewportX, viewportY);
  }

  private openPopover(t: TrackInfo, vx: number, vy: number): void {
    this.closePopover();
    const pop = document.createElement("div");
    pop.className = "inline-namer";
    pop.style.left = `${vx + 12}px`;
    pop.style.top = `${vy + 12}px`;
    pop.innerHTML = `
      <div class="muted small">track #${t.track_id} · ${t.cls_name}</div>
      <input type="text" placeholder="novo nome (ex: galinha)" />
      <div class="inline-namer-actions">
        <button class="btn-mini primary">renomear</button>
        <button class="btn-mini">cancelar</button>
      </div>
    `;
    const input = pop.querySelector("input") as HTMLInputElement;
    input.value = t.cls_name.startsWith("Item") ? "" : t.cls_name;
    input.focus();
    input.select();

    const submit = async () => {
      const newName = input.value.trim();
      if (!newName || newName === t.cls_name) {
        this.closePopover();
        return;
      }
      try {
        const r = await fetch(
          `${this.baseUrl}/api/labels/${encodeURIComponent(t.cls_name)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: newName }),
          }
        );
        if (r.ok) {
          this.onRename?.(t.cls_name, newName);
          this.closePopover();
        } else {
          const err = await r.json().catch(() => ({}));
          alert(`Falha: ${err.error || r.status}`);
        }
      } catch (e) {
        alert(`Erro: ${e}`);
      }
    };
    pop.querySelector(".primary")!.addEventListener("click", submit);
    pop.querySelector(".btn-mini:not(.primary)")!.addEventListener("click", () =>
      this.closePopover()
    );
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      else if (e.key === "Escape") this.closePopover();
    });
    this.host.appendChild(pop);
    this.popover = pop;
  }

  private closePopover(): void {
    if (this.popover) {
      this.popover.remove();
      this.popover = null;
    }
  }
}
