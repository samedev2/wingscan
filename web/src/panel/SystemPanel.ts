/**
 * SystemPanel: sliders + switches da análise.
 * Mantém DOM leve (sem libs) e usa PanelStore pra persistir.
 */
import type { PanelSettings, PanelStore } from "./PanelStore";

export class SystemPanel {
  private container: HTMLElement;
  private store: PanelStore;

  constructor(container: HTMLElement, store: PanelStore) {
    this.container = container;
    this.store = store;
    this.render();
  }

  setSettings(s: PanelSettings): void {
    this.render(s);
  }

  private render(s: PanelSettings = this.store.get()): void {
    const linePos = s.line_position ?? 0.5;
    const lineOrient = s.line_orientation ?? "horizontal";
    const heatmapOn = s.heatmap_enabled ?? true;
    const pathsOn = s.paths_enabled ?? true;
    const decay = s.heatmap_decay ?? 0.985;
    const radius = s.heatmap_radius ?? 14;
    const reidT = s.reid_threshold ?? 0.65;

    this.container.innerHTML = `
      <div class="panel-row">
        <label for="sp-line-position">Linha de contagem</label>
        <input type="range" id="sp-line-position" min="0.05" max="0.95" step="0.01" value="${linePos}">
        <span class="panel-value" id="sp-line-position-val">${(linePos * 100).toFixed(0)}%</span>
      </div>
      <div class="panel-row">
        <label for="sp-line-orient">Orientação</label>
        <select id="sp-line-orient">
          <option value="horizontal" ${lineOrient === "horizontal" ? "selected" : ""}>Horizontal (y)</option>
          <option value="vertical" ${lineOrient === "vertical" ? "selected" : ""}>Vertical (x)</option>
        </select>
      </div>
      <div class="panel-row">
        <label><input type="checkbox" id="sp-heatmap-on" ${heatmapOn ? "checked" : ""}> Heatmap (por classe)</label>
        <button class="btn-mini" id="sp-heatmap-clear">limpar</button>
      </div>
      <div class="panel-row">
        <label for="sp-heatmap-decay">Decay do heatmap</label>
        <input type="range" id="sp-heatmap-decay" min="0.90" max="1.0" step="0.001" value="${decay}">
        <span class="panel-value" id="sp-heatmap-decay-val">${decay.toFixed(3)}</span>
      </div>
      <div class="panel-row">
        <label for="sp-heatmap-radius">Raio (px)</label>
        <input type="range" id="sp-heatmap-radius" min="4" max="40" step="1" value="${radius}">
        <span class="panel-value" id="sp-heatmap-radius-val">${radius}</span>
      </div>
      <div class="panel-row">
        <label><input type="checkbox" id="sp-paths-on" ${pathsOn ? "checked" : ""}> Trilhas (paths)</label>
      </div>
      <div class="panel-row">
        <label for="sp-reid-threshold">Threshold ReID</label>
        <input type="range" id="sp-reid-threshold" min="0.40" max="0.95" step="0.01" value="${reidT}">
        <span class="panel-value" id="sp-reid-threshold-val">${reidT.toFixed(2)}</span>
      </div>
    `;

    // Wire up
    const wire = <T extends HTMLElement>(id: string): T => {
      const el = this.container.querySelector(`#${id}`) as T | null;
      if (!el) throw new Error(`#${id} not found`);
      return el;
    };

    wire<HTMLInputElement>("sp-line-position").addEventListener("input", (e) => {
      const v = parseFloat((e.target as HTMLInputElement).value);
      this.container.querySelector("#sp-line-position-val")!.textContent = `${(v * 100).toFixed(0)}%`;
      this.store.patch("line_position", v);
    });
    wire<HTMLSelectElement>("sp-line-orient").addEventListener("change", (e) => {
      this.store.patch("line_orientation", (e.target as HTMLSelectElement).value);
    });
    wire<HTMLInputElement>("sp-heatmap-on").addEventListener("change", (e) => {
      this.store.patch("heatmap_enabled", (e.target as HTMLInputElement).checked);
      this.onHeatmapToggle?.((e.target as HTMLInputElement).checked);
    });
    wire<HTMLButtonElement>("sp-heatmap-clear").addEventListener("click", () => {
      this.onHeatmapClear?.();
    });
    wire<HTMLInputElement>("sp-heatmap-decay").addEventListener("input", (e) => {
      const v = parseFloat((e.target as HTMLInputElement).value);
      this.container.querySelector("#sp-heatmap-decay-val")!.textContent = v.toFixed(3);
      this.store.patch("heatmap_decay", v);
    });
    wire<HTMLInputElement>("sp-heatmap-radius").addEventListener("input", (e) => {
      const v = parseInt((e.target as HTMLInputElement).value, 10);
      this.container.querySelector("#sp-heatmap-radius-val")!.textContent = String(v);
      this.store.patch("heatmap_radius", v);
    });
    wire<HTMLInputElement>("sp-paths-on").addEventListener("change", (e) => {
      this.store.patch("paths_enabled", (e.target as HTMLInputElement).checked);
      this.onPathsToggle?.((e.target as HTMLInputElement).checked);
    });
    wire<HTMLInputElement>("sp-reid-threshold").addEventListener("input", (e) => {
      const v = parseFloat((e.target as HTMLInputElement).value);
      this.container.querySelector("#sp-reid-threshold-val")!.textContent = v.toFixed(2);
      this.store.patch("reid_threshold", v);
    });
  }

  onHeatmapToggle: ((on: boolean) => void) | null = null;
  onHeatmapClear: (() => void) | null = null;
  onPathsToggle: ((on: boolean) => void) | null = null;
}
