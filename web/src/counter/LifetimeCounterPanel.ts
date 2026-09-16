/**
 * LifetimeCounterPanel: mostra a contagem ACUMULADA entre loops do vídeo.
 * Diferente do CounterPanel (que mostra o turno atual, zera a cada loop),
 * este persiste enquanto o cv-service estiver rodando. Cada vez que o
 * vídeo reinicia, os IN/OUT do loop atual são somados aqui.
 */
export interface LifetimeClassState {
  in: number;
  out: number;
  current: number;
}

export class LifetimeCounterPanel {
  private tbody: HTMLTableSectionElement;
  private headerEl: HTMLElement | null;
  private state: Record<string, LifetimeClassState> = {};
  private loopCount = 0;

  constructor(tbody: HTMLTableSectionElement, headerEl?: HTMLElement | null) {
    this.tbody = tbody;
    this.headerEl = headerEl ?? null;
    this.render();
  }

  setState(state: Record<string, LifetimeClassState>, loopCount: number): void {
    this.state = state;
    this.loopCount = loopCount;
    this.render();
  }

  private render(): void {
    if (this.headerEl) {
      this.headerEl.textContent = this.loopCount > 0
        ? `Total acumulado entre ${this.loopCount} loop${this.loopCount > 1 ? 's' : ''}`
        : `Total acumulado (após o 1º loop)`;
    }
    const keys = Object.keys(this.state);
    if (keys.length === 0) {
      this.tbody.innerHTML = '<tr><td colspan="4" class="muted">aguardando o 1º loop do vídeo terminar…</td></tr>';
      return;
    }
    const totalIn = keys.reduce((acc, k) => acc + (this.state[k]?.in ?? 0), 0);
    const totalOut = keys.reduce((acc, k) => acc + (this.state[k]?.out ?? 0), 0);
    const totalCurrent = keys.reduce((acc, k) => acc + (this.state[k]?.current ?? 0), 0);
    const rows = [
      `<tr class="lifetime-total">
        <td><strong>TOTAL</strong></td>
        <td><strong>${totalIn}</strong></td>
        <td><strong>${totalOut}</strong></td>
        <td><strong>${totalCurrent}</strong></td>
      </tr>`,
      ...keys
        .sort()
        .map((cls) => {
          const s = this.state[cls];
          return `<tr>
            <td>${escape(cls)}</td>
            <td>${s.in}</td>
            <td>${s.out}</td>
            <td>${s.current}</td>
          </tr>`;
        }),
    ].join("");
    this.tbody.innerHTML = rows;
  }
}

function escape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
