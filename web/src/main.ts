/**
 * Entry point v4: orquestra MJPEG, WS eventos, PTZ virtual, naming panel,
 * analytics panel, system panel, heatmap/paths overlay, inline namer.
 *
 * Mudanças v4: layout mudou para dois painéis (vídeo à esquerda, analytics à
 * direita). Painel de Sistema / Heatmap / Classes / Counter / Eventos ficam
 * atrás de <details> para não competir com o painel de analytics.
 */
import { JpegPollingClient } from "./stream/JpegPollingClient";
import { EventsClient, type WsEvent } from "./stream/EventsClient";
import { CounterPanel } from "./counter/CounterPanel";
import { NamingPanel, type LabelEntry } from "./naming/NamingPanel";
import { InlineNamer, type TrackInfo } from "./naming/InlineNamer";
import { PanelStore } from "./panel/PanelStore";
import { SystemPanel } from "./panel/SystemPanel";
import { HeatmapOverlay } from "./canvas/HeatmapOverlay";
import { PathOverlay } from "./canvas/PathOverlay";
import { PTZOverlay } from "./ptz/Overlay";
import { VirtualPTZ } from "./ptz/VirtualPTZ";
import { AnalyticsPanel } from "./analytics/AnalyticsPanel";

const CV_BASE = (import.meta.env?.VITE_CV_BASE as string | undefined) ?? "/cv";

function wsUrlFor(base: string): string {
  const clean = base.replace(/\/$/, "");
  if (clean.startsWith("http://")) return clean.replace(/^http/, "ws") + "/ws/events";
  if (clean.startsWith("https://")) return clean.replace(/^https/, "wss") + "/ws/events";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${clean}/ws/events`;
}

function init(): void {
  const $ = <T extends HTMLElement>(id: string): T => {
    const el = document.getElementById(id);
    if (!el) throw new Error(`#${id} não encontrado`);
    return el as T;
  };

  const mjpegImg = $("mjpeg") as HTMLImageElement;
  const overlayCanvas = $("overlay") as HTMLCanvasElement;
  const heatmapCanvas = $("heatmap-canvas") as HTMLCanvasElement;
  const pathsCanvas = $("paths-canvas") as HTMLCanvasElement;
  const counterBody = $("counter-body") as HTMLTableSectionElement;
  const labelsList = $("labels-list") as HTMLDivElement;
  const eventsList = $("events") as HTMLUListElement;
  const newItemBanner = $("new-item-banner") as HTMLDivElement;
  const connDot = $("conn-dot") as HTMLSpanElement;
  const connText = $("conn-text") as HTMLSpanElement;
  const systemPanel = $("system-panel") as HTMLDivElement;
  const heatmapTabs = $("heatmap-tabs") as HTMLDivElement;
  const analyticsEl = $("analytics") as HTMLDivElement;

  // PTZ + Overlay
  const overlay = new PTZOverlay(overlayCanvas);
  const ptz = new VirtualPTZ(document.body);
  ptz.start();
  ptz.onChange((s) => overlay.update(s));

  const heatmapOv = new HeatmapOverlay(heatmapCanvas, CV_BASE);
  const pathOv = new PathOverlay(pathsCanvas, CV_BASE);
  const inlineNamer = new InlineNamer(document.body, heatmapCanvas, CV_BASE);
  const analyticsPanel = new AnalyticsPanel(analyticsEl);

  const panelStore = new PanelStore(CV_BASE, (s) => {
    systemPanelUi.setSettings(s);
  });
  const systemPanelUi = new SystemPanel(systemPanel, panelStore);
  systemPanelUi.onHeatmapToggle = (on) => {
    heatmapOv.setEnabled(on);
    if (on) heatmapOv.start();
  };
  systemPanelUi.onHeatmapClear = () => {
    fetch(`${CV_BASE}/api/heatmap`, { method: "DELETE" });
  };
  systemPanelUi.onPathsToggle = (on) => {
    pathOv.setEnabled(on);
  };

  const videoWrap = $("video-wrap") as HTMLDivElement;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  videoWrap.addEventListener("pointerdown", (e) => {
    const target = e.target as HTMLElement;
    if (target.tagName === "CANVAS") return;
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    videoWrap.setPointerCapture(e.pointerId);
  });
  videoWrap.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = videoWrap.getBoundingClientRect();
    const dx = (e.clientX - lastX) / rect.width;
    const dy = (e.clientY - lastY) / rect.height;
    ptz.ptz.x -= dx * 1.2;
    ptz.ptz.y += dy * 1.2;
    lastX = e.clientX;
    lastY = e.clientY;
  });
  videoWrap.addEventListener("pointerup", (e) => {
    dragging = false;
    try { videoWrap.releasePointerCapture(e.pointerId); } catch { /* */ }
  });
  videoWrap.addEventListener("pointercancel", () => { dragging = false; });

  videoWrap.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.92 : 1.08;
      ptz.ptz.z = Math.min(ptz.zoomMax, Math.max(ptz.zoomMin, ptz.ptz.z * delta));
    },
    { passive: false },
  );

  const onCanvasClick = (canvas: HTMLCanvasElement) => (e: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    const vx = e.clientX - rect.left;
    const vy = e.clientY - rect.top;
    inlineNamer.onCanvasClick(vx, vy);
  };
  heatmapCanvas.addEventListener("click", onCanvasClick(heatmapCanvas));
  pathsCanvas.addEventListener("click", onCanvasClick(pathsCanvas));
  overlayCanvas.addEventListener("click", onCanvasClick(overlayCanvas));

  window.addEventListener("keydown", (e) => {
    if (e.code === "Digit0") ptz.reset();
  });

  const counter = new CounterPanel(counterBody);
  const naming = new NamingPanel(labelsList);

  async function refreshLabels(): Promise<void> {
    try {
      const r = await fetch(`${CV_BASE}/api/labels`);
      if (!r.ok) return;
      const data = (await r.json()) as { labels: LabelEntry[] };
      naming.setLabels(data.labels || []);
    } catch { /* ok */ }
  }

  naming.onRename = async (oldName, newName) => {
    try {
      const r = await fetch(`${CV_BASE}/api/labels/${encodeURIComponent(oldName)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: newName }),
      });
      if (r.ok) {
        await refreshLabels();
        await refreshHeatmapClasses();
      }
    } catch (e) {
      alert(`Erro: ${e}`);
    }
  };
  naming.onDelete = async (name) => {
    try {
      const r = await fetch(`${CV_BASE}/api/labels/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (r.ok) {
        await refreshLabels();
        await refreshHeatmapClasses();
      }
    } catch (e) { alert(`Erro: ${e}`); }
  };
  inlineNamer.setOnRename(() => {
    refreshLabels();
    refreshHeatmapClasses();
  });

  heatmapTabs.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest("button.tab") as HTMLButtonElement | null;
    if (!btn) return;
    heatmapTabs.querySelectorAll("button.tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const cls = btn.dataset.cls || null;
    heatmapOv.setFilter(cls && cls.length > 0 ? cls : null);
  });

  async function refreshHeatmapClasses(): Promise<void> {
    try {
      const r = await fetch(`${CV_BASE}/api/heatmap/info`);
      if (!r.ok) return;
      const info = (await r.json()) as { classes: string[] };
      rebuildHeatmapTabs(info.classes || []);
      heatmapOv.setClasses(info.classes || []);
    } catch { /* ok */ }
  }

  function rebuildHeatmapTabs(classes: string[]): void {
    const existing = heatmapTabs.querySelector("button.tab.active");
    const activeCls = existing ? (existing as HTMLElement).dataset.cls || "" : "";
    heatmapTabs.innerHTML = "";
    const all = document.createElement("button");
    all.className = "tab" + (activeCls === "" ? " active" : "");
    all.dataset.cls = "";
    all.textContent = "todas";
    heatmapTabs.appendChild(all);
    for (const cls of classes) {
      const b = document.createElement("button");
      b.className = "tab" + (activeCls === cls ? " active" : "");
      b.dataset.cls = cls;
      b.textContent = cls;
      heatmapTabs.appendChild(b);
    }
  }

  const stream = new JpegPollingClient(mjpegImg, CV_BASE, 120);
  stream.start();

  const events = new EventsClient(wsUrlFor(CV_BASE));
  events.start((connected) => setStatus(connected));

  let newItemHideTimer: number | null = null;

  events.onEvent((e: WsEvent) => {
    if (e.type === "init") {
      counter.setState(e.contagens);
      naming.setLabels(e.labels || []);
      if (e.analytics) analyticsPanel.setAnalytics(e.analytics);
      if (e.sensor) analyticsPanel.setSensor(e.sensor);
      if (e.panel && Object.keys(e.panel).length > 0) {
        systemPanelUi.setSettings(e.panel as never);
      }
      panelStore.load();
      heatmapOv.start();
      pathOv.start();
      refreshHeatmapClasses();
      refreshTimeline();
    } else if (e.type === "evento") {
      const li = document.createElement("li");
      li.className = e.direcao;
      const ts = new Date(e.ts * 1000).toLocaleTimeString();
      li.textContent = `${ts}  #${e.track_id}  ${e.classe}  → ${e.direcao.toUpperCase()}  (${(e.conf * 100).toFixed(0)}%)`;
      eventsList.prepend(li);
      while (eventsList.children.length > 50) eventsList.removeChild(eventsList.lastChild!);
    } else if (e.type === "novo_item") {
      newItemBanner.innerHTML = `
        <img alt="crop" src="data:image/jpeg;base64,${e.crop}" />
        <div class="new-item-info">
          <div class="muted">novo item detectado</div>
          <div class="new-item-name">${escapeHtml(e.name)}</div>
          <div class="muted small">track #${e.track_id} · similaridade máx ${(e.sim * 100).toFixed(0)}%</div>
        </div>
      `;
      newItemBanner.classList.add("show");
      if (newItemHideTimer !== null) window.clearTimeout(newItemHideTimer);
      newItemHideTimer = window.setTimeout(() => {
        newItemBanner.classList.remove("show");
      }, 4000);
      naming.upsertLabel({ name: e.name, samples: 1 });
      refreshLabels();
      refreshHeatmapClasses();
    } else if (e.type === "tracks") {
      inlineNamer.setTracks(e.tracks as TrackInfo[]);
    } else if (e.type === "ping") { /* keep-alive */ }
  });

  // Polling: estado + analytics + sensor + timeline
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/state`);
      if (!r.ok) return;
      const s = await r.json();
      if (s.analytics) analyticsPanel.setAnalytics(s.analytics);
      if (s.sensor) analyticsPanel.setSensor(s.sensor);
      if (s.contagens) counter.setState(s.contagens);
    } catch { /* ok */ }
  }, 1000);

  async function refreshTimeline(): Promise<void> {
    try {
      const r = await fetch(`${CV_BASE}/api/timeline?hours=24`);
      if (!r.ok) return;
      const data = (await r.json()) as { entries: { ts: number; total: number; ativa: number; repouso: number; anomalo: number; normal: number; }[] };
      analyticsPanel.setTimeline(data.entries || []);
    } catch { /* ok */ }
  }
  // Atualiza timeline a cada 60s
  setInterval(refreshTimeline, 60000);

  function setStatus(ok: boolean): void {
    if (ok) {
      connDot.className = "dot ok";
      connText.textContent = "conectado ao cv-service";
    } else {
      connDot.className = "dot bad";
      connText.textContent = "desconectado";
    }
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function runInit(): void {
  try {
    init();
  } catch (e) {
    const msg = e instanceof Error ? `${e.message}\n\n${e.stack}` : String(e);
    const banner = document.createElement("pre");
    banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:#f85149;color:#fff;padding:16px;z-index:99999;font-size:13px;white-space:pre-wrap;max-height:60vh;overflow:auto;";
    banner.textContent = "❌ ERRO JS:\n\n" + msg;
    document.body.appendChild(banner);
    throw e;
  }
}

window.addEventListener("error", (ev) => {
  const banner = document.createElement("pre");
  banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:#d29922;color:#000;padding:16px;z-index:99998;font-size:13px;white-space:pre-wrap;max-height:60vh;overflow:auto;";
  banner.textContent = "⚠ ERRO NÃO TRATADO:\n\n" + (ev.error?.stack || ev.message);
  document.body.appendChild(banner);
});

window.addEventListener("unhandledrejection", (ev) => {
  const banner = document.createElement("pre");
  banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:#d29922;color:#000;padding:16px;z-index:99998;font-size:13px;white-space:pre-wrap;max-height:60vh;overflow:auto;";
  banner.textContent = "⚠ PROMISE REJEITADA:\n\n" + (ev.reason?.stack || String(ev.reason));
  document.body.appendChild(banner);
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", runInit);
} else {
  runInit();
}
