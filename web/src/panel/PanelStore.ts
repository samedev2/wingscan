/**
 * PanelStore: CRUD sobre /api/panel com debounce pra evitar spam.
 * Mantém cache local + aplica mudanças otimisticamente.
 */
export type PanelSettings = {
  line_position?: number;
  line_orientation?: "horizontal" | "vertical";
  heatmap_enabled?: boolean;
  paths_enabled?: boolean;
  heatmap_decay?: number;
  heatmap_radius?: number;
  reid_threshold?: number;
};

export class PanelStore {
  private settings: PanelSettings = {};
  private saveTimer: number | null = null;
  private baseUrl: string;
  private onUpdate?: (s: PanelSettings) => void;

  constructor(baseUrl: string, onUpdate?: (s: PanelSettings) => void) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.onUpdate = onUpdate;
  }

  async load(): Promise<void> {
    try {
      const r = await fetch(`${this.baseUrl}/api/panel`);
      if (!r.ok) return;
      this.settings = (await r.json()) as PanelSettings;
      this.onUpdate?.(this.settings);
    } catch {
      /* offline ok */
    }
  }

  get(): PanelSettings {
    return { ...this.settings };
  }

  /**
   * Aplica mudança localmente e agenda save no backend (debounce 400ms).
   */
  patch(key: keyof PanelSettings, value: unknown): void {
    this.settings = { ...this.settings, [key]: value };
    this.onUpdate?.(this.settings);
    if (this.saveTimer !== null) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => this.save(), 400);
  }

  private async save(): Promise<void> {
    const payload = { ...this.settings };
    try {
      await fetch(`${this.baseUrl}/api/panel`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      console.warn("[panel] save falhou:", e);
    }
  }
}
