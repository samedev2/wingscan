/**
 * NamingPanel: lista de classes identificadas com botão de renomear inline.
 * Quando recebe `novo_item` via WS, faz flash visual na linha correspondente.
 */
export interface LabelEntry {
  name: string;
  samples: number;
  created_at?: string;
}

export class NamingPanel {
  private container: HTMLElement;
  private labels: Map<string, LabelEntry> = new Map();
  private flashTimers = new Map<string, number>();

  constructor(container: HTMLElement) {
    this.container = container;
    this.render();
  }

  setLabels(labels: LabelEntry[]): void {
    this.labels.clear();
    for (const l of labels) {
      this.labels.set(l.name, l);
    }
    this.render();
  }

  upsertLabel(label: LabelEntry): void {
    this.labels.set(label.name, label);
    this.render();
    this.flash(label.name);
  }

  private flash(name: string): void {
    const prev = this.flashTimers.get(name);
    if (prev) {
      window.clearTimeout(prev);
      const row = this.container.querySelector(`[data-name="${cssEscape(name)}"]`);
      row?.classList.remove("flash");
    }
    window.setTimeout(() => {
      const row = this.container.querySelector(`[data-name="${cssEscape(name)}"]`);
      row?.classList.add("flash");
      const t = window.setTimeout(() => row?.classList.remove("flash"), 1200);
      this.flashTimers.set(name, t);
    }, 10);
  }

  private render(): void {
    const labels = Array.from(this.labels.values()).sort((a, b) => {
      // ItemN primeiro, depois alfabético
      const aItem = a.name.startsWith("Item");
      const bItem = b.name.startsWith("Item");
      if (aItem && !bItem) return -1;
      if (!aItem && bItem) return 1;
      return a.name.localeCompare(b.name, "pt-BR");
    });

    if (labels.length === 0) {
      this.container.innerHTML = '<p class="muted">nenhuma classe identificada ainda</p>';
      return;
    }

    this.container.innerHTML = labels
      .map((l) => {
        const isAuto = l.name.startsWith("Item");
        return `
          <div class="label-row ${isAuto ? "auto" : "named"}" data-name="${escapeHtml(l.name)}">
            <div class="label-name">
              <span class="name">${escapeHtml(l.name)}</span>
              ${isAuto ? '<span class="badge">auto</span>' : '<span class="badge ok">ok</span>'}
            </div>
            <div class="label-meta">${l.samples} amostras</div>
            <div class="label-actions">
              <button class="btn-rename" data-name="${escapeHtml(l.name)}">renomear</button>
              <button class="btn-delete danger" data-name="${escapeHtml(l.name)}" title="apagar classe e embeddings">×</button>
            </div>
          </div>
        `;
      })
      .join("");

    // Liga botões
    this.container.querySelectorAll<HTMLButtonElement>(".btn-rename").forEach((btn) => {
      btn.addEventListener("click", () => {
        const oldName = btn.getAttribute("data-name") || "";
        const newName = window.prompt(`Renomear "${oldName}" para:`, oldName);
        if (newName === null) return;
        const trimmed = newName.trim();
        if (!trimmed || trimmed === oldName) return;
        this.onRename?.(oldName, trimmed);
      });
    });
    this.container.querySelectorAll<HTMLButtonElement>(".btn-delete").forEach((btn) => {
      btn.addEventListener("click", () => {
        const name = btn.getAttribute("data-name") || "";
        if (window.confirm(`Apagar a classe "${name}" e todos os embeddings dela?`)) {
          this.onDelete?.(name);
        }
      });
    });
  }

  onRename: ((oldName: string, newName: string) => void) | null = null;
  onDelete: ((name: string) => void) | null = null;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function cssEscape(s: string): string {
  return s.replace(/["\\]/g, "\\$&");
}
