/**
 * CounterPanel: atualiza a tabela de contagem (classe x IN/OUT/ATIVOS).
 */
export interface ClassState {
  in: number;
  out: number;
  current: number;
}

export class CounterPanel {
  private tbody: HTMLTableSectionElement;
  private state: Record<string, ClassState> = {};

  constructor(tbody: HTMLTableSectionElement) {
    this.tbody = tbody;
  }

  setState(state: Record<string, ClassState>): void {
    this.state = state;
    this.render();
  }

  private render(): void {
    const keys = Object.keys(this.state);
    if (keys.length === 0) {
      this.tbody.innerHTML = '<tr><td colspan="4" class="muted">sem dados ainda</td></tr>';
      return;
    }
    const rows = keys
      .sort()
      .map((cls) => {
        const s = this.state[cls];
        return `<tr>
          <td>${escape(cls)}</td>
          <td>${s.in}</td>
          <td>${s.out}</td>
          <td>${s.current}</td>
        </tr>`;
      })
      .join("");
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
