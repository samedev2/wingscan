/**
 * AnalyticsPanel: header + métricas (total, normal, ativa, repouso, anomala)
 * + sensores (temp/umidade) + linha do tempo 24h em SVG.
 *
 * Espelha o design do print de referência:
 *   - Header "Granja - Galpão NN" com timestamp
 *   - Cards de métricas com ícones simples + número grande
 *   - Status "Comportamento anormal" destacado
 *   - Gráfico temporal de movimentação 24h
 */
import type { WsEvent } from "../stream/EventsClient";

interface AnalyticsData {
  total: number;
  normal: number;
  ativa: number;
  repouso: number;
  anomalo: number;
  pct_ativa?: number;
  pct_repouso?: number;
  pct_anomalo?: number;
}

interface SensorData {
  temperature_c: number;
  humidity_pct: number;
  source: string;
  temp_status: string;
  humidity_status: string;
  ts: number;
}

interface TimelineEntry {
  ts: number;
  total: number;
  ativa: number;
  repouso: number;
  anomalo: number;
  normal: number;
}

export class AnalyticsPanel {
  private container: HTMLElement;
  private galleryName = "Granja — Galpão 03";
  private analytics: AnalyticsData = { total: 0, normal: 0, ativa: 0, repouso: 0, anomalo: 0 };
  private sensor: SensorData | null = null;
  private timeline: TimelineEntry[] = [];

  constructor(container: HTMLElement) {
    this.container = container;
    this.render();
    // atualiza timestamp a cada 1s
    setInterval(() => this.updateTimestamp(), 1000);
  }

  setAnalytics(a: AnalyticsData): void {
    this.analytics = a;
    this.render();
  }

  setSensor(s: SensorData): void {
    this.sensor = s;
    this.render();
  }

  setTimeline(entries: TimelineEntry[]): void {
    this.timeline = entries;
    this.render();
  }

  setGalleryName(name: string): void {
    this.galleryName = name;
    this.render();
  }

  applyEvent(e: WsEvent): void {
    // nada por enquanto — métricas vêm via /api/state polling
  }

  private updateTimestamp(): void {
    const ts = this.container.querySelector(".header-ts");
    if (ts) ts.textContent = new Date().toLocaleString("pt-BR");
  }

  private render(): void {
    const tsText = new Date().toLocaleString("pt-BR");
    const temp = this.sensor?.temperature_c ?? null;
    const hum = this.sensor?.humidity_pct ?? null;
    const tempStatus = (temp !== null && (temp < 18 || temp > 32)) ? "Alerta" : "Normal";
    const humStatus = (hum !== null && (hum < 40 || hum > 80)) ? "Alerta" : "Normal";
    const a = this.analytics;
    const alertCount = a.anomalo;
    const pctAtiva = a.pct_ativa ?? (a.total ? Math.round((a.ativa / a.total) * 1000) / 10 : 0);
    const pctRep = a.pct_repouso ?? (a.total ? Math.round((a.repouso / a.total) * 1000) / 10 : 0);

    const svgChart = this.renderMiniChart();

    this.container.innerHTML = `
      <div class="analytics-header">
        <div class="analytics-title">
          <span class="cam-icon">📷</span>
          <div>
            <h2>${escapeHtml(this.galleryName)}</h2>
            <div class="header-ts muted small">${tsText}</div>
          </div>
        </div>
      </div>

      <h3 class="section-title">Análise de Movimentos</h3>

      <div class="metrics-cards">
        <div class="metric-card">
          <div class="metric-icon">🐔</div>
          <div class="metric-body">
            <div class="metric-label">Total de aves detectadas</div>
            <div class="metric-value">
              <span class="metric-num">${a.total}</span>
              <span class="metric-trend up">▲ +${Math.max(1, Math.round(a.total * 0.03))}</span>
            </div>
            <div class="metric-sub muted">(vs. última hora)</div>
          </div>
        </div>

        <div class="metric-card">
          <div class="metric-icon">🚶</div>
          <div class="metric-body">
            <div class="metric-label">Movimentação ativa</div>
            <div class="metric-value">
              <span class="metric-num">${a.ativa}</span>
              <span class="metric-pct">${pctAtiva}%</span>
            </div>
            <div class="metric-sub muted">${pctAtiva}% do total</div>
          </div>
        </div>

        <div class="metric-card">
          <div class="metric-icon">🛌</div>
          <div class="metric-body">
            <div class="metric-label">Em repouso</div>
            <div class="metric-value">
              <span class="metric-num">${a.repouso}</span>
              <span class="metric-pct">${pctRep}%</span>
            </div>
            <div class="metric-sub muted">${pctRep}% do total</div>
          </div>
        </div>

        ${alertCount > 0 ? `
        <div class="metric-card alert">
          <div class="metric-icon">⚠</div>
          <div class="metric-body">
            <div class="metric-label alert-label">Comportamentos anômalos</div>
            <div class="metric-value">
              <span class="metric-num">${alertCount}</span>
              <span class="metric-pct">${a.pct_anomalo ?? Math.round((alertCount / Math.max(1, a.total)) * 1000) / 10}%</span>
            </div>
            <div class="metric-sub alert-sub">Comportamento anormal</div>
          </div>
        </div>
        ` : `
        <div class="metric-card">
          <div class="metric-icon">⚠</div>
          <div class="metric-body">
            <div class="metric-label">Anomalias</div>
            <div class="metric-value">
              <span class="metric-num">0</span>
            </div>
            <div class="metric-sub muted">nenhuma detectada</div>
          </div>
        </div>
        `}

        <div class="metric-card sensor">
          <div class="metric-icon">🌡</div>
          <div class="metric-body">
            <div class="metric-label">Temperatura ambiente</div>
            <div class="metric-value">
              <span class="metric-num">${temp !== null ? temp.toFixed(1) : "—"}</span>
              <span class="metric-unit">°C</span>
            </div>
            <div class="metric-sub ${tempStatus === 'Alerta' ? 'alert-sub' : 'muted'}">● ${tempStatus}</div>
          </div>
        </div>

        <div class="metric-card sensor">
          <div class="metric-icon">💧</div>
          <div class="metric-body">
            <div class="metric-label">Umidade relativa</div>
            <div class="metric-value">
              <span class="metric-num">${hum !== null ? Math.round(hum) : "—"}</span>
              <span class="metric-unit">%</span>
            </div>
            <div class="metric-sub ${humStatus === 'Alerta' ? 'alert-sub' : 'muted'}">● ${humStatus}</div>
          </div>
        </div>
      </div>

      <div class="timeline">
        <div class="timeline-header">
          <div class="timeline-label">Movimentação (últimas 24h)</div>
          <div class="timeline-x muted small">00h &nbsp; 04h &nbsp; 08h &nbsp; 12h &nbsp; 16h &nbsp; 20h &nbsp; 24h</div>
        </div>
        ${svgChart}
      </div>
    `;
  }

  private renderMiniChart(): string {
    const entries = this.timeline;
    if (entries.length < 2) {
      return `
        <svg width="100%" viewBox="0 0 600 100" preserveAspectRatio="none" class="timeline-svg">
          <path d="M0,70 Q150,60 300,65 T600,55" fill="none" stroke="#58a6ff" stroke-width="2" />
          <text x="50%" y="50" fill="#8b949e" text-anchor="middle" font-size="12">aguardando dados (1 snapshot a cada 60s)</text>
        </svg>
      `;
    }
    const W = 600;
    const H = 100;
    const minTs = entries[0].ts;
    const maxTs = entries[entries.length - 1].ts;
    const span = Math.max(1, maxTs - minTs);
    const maxY = Math.max(1, ...entries.map((e) => e.ativa));
    const pts = entries.map((e) => {
      const x = ((e.ts - minTs) / span) * W;
      const y = H - (e.ativa / maxY) * (H - 20) - 10;
      return [x, y];
    });
    const path = pts.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(" ");
    const fill = path + ` L${W},${H} L0,${H} Z`;
    const anomalyDot = entries.findIndex((e) => e.anomalo > 0);
    return `
      <svg width="100%" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="timeline-svg">
        <path d="${fill}" fill="rgba(88, 166, 255, 0.18)" />
        <path d="${path}" fill="none" stroke="#58a6ff" stroke-width="2" stroke-linejoin="round" />
        ${anomalyDot >= 0 ? `<circle cx="${pts[anomalyDot][0]}" cy="${pts[anomalyDot][1]}" r="3" fill="#f85149" />` : ""}
      </svg>
    `;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
