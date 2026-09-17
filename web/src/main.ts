/**
 * Entry point v5: orquestra MJPEG, WS eventos, PTZ virtual, naming panel,
 * analytics panel, system panel, heatmap/paths overlay, inline namer,
 * ReID gallery, source switcher, process start/stop.
 *
 * v5 (UI Claude-style): layout mudou para topbar + controls + stats + main-grid
 * + advanced. Botões do topbar (Iniciar/Parar/Enviar/Fonte) agora respondem
 * de verdade via fetch. Log tem filtros por categoria + busca textual + pause
 * + clear + download. Identidades persistentes (ReID SQLite) rendenizam em
 * cards com thumbnail.
 */
import { JpegPollingClient } from "./stream/JpegPollingClient";
import { EventsClient, type WsEvent } from "./stream/EventsClient";
import { CounterPanel } from "./counter/CounterPanel";
import { LifetimeCounterPanel } from "./counter/LifetimeCounterPanel";
import { NamingPanel, type LabelEntry } from "./naming/NamingPanel";
import { InlineNamer, type TrackInfo } from "./naming/InlineNamer";
import { PanelStore } from "./panel/PanelStore";
import { SystemPanel } from "./panel/SystemPanel";
import { HeatmapOverlay } from "./canvas/HeatmapOverlay";
import { PathOverlay } from "./canvas/PathOverlay";
import { PTZOverlay } from "./ptz/Overlay";
import { VirtualPTZ } from "./ptz/VirtualPTZ";
import { AnalyticsPanel } from "./analytics/AnalyticsPanel";
import { SourceSwitcher } from "./source/SourceSwitcher";

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

  // ===== DOM refs - legacy (preservados) =====
  const mjpegImg = $("mjpeg") as HTMLImageElement;
  const overlayCanvas = $("overlay") as HTMLCanvasElement;
  const heatmapCanvas = $("heatmap-canvas") as HTMLCanvasElement;
  const pathsCanvas = $("paths-canvas") as HTMLCanvasElement;
  const counterBody = $("counter-body") as HTMLTableSectionElement;
  const lifetimeBody = $("lifetime-body") as HTMLTableSectionElement;
  const lifetimeSummary = $("lifetime-summary") as HTMLElement;
  const labelsList = $("labels-list") as HTMLDivElement;
  const eventsList = $("events") as HTMLUListElement;
  const newItemBanner = $("new-item-banner") as HTMLDivElement;
  const connDot = $("conn-dot") as HTMLSpanElement;
  const connText = $("conn-text") as HTMLSpanElement;
  const systemPanel = $("system-panel") as HTMLDivElement;
  const heatmapTabs = $("heatmap-tabs") as HTMLDivElement;
  const analyticsEl = $("analytics") as HTMLDivElement;
  const btnSource = $("btn-source") as HTMLButtonElement;

  // ===== DOM refs - novos (UI Claude-style v5) =====
  const btnStart = $("btn-start") as HTMLButtonElement;
  const btnStop = $("btn-stop") as HTMLButtonElement;
  const sourceSelect = $("source-select") as HTMLSelectElement;
  const videoSelect = $("video-select") as HTMLSelectElement;
  const fileInput = $("file-input") as HTMLInputElement;
  const uploadStatus = $("upload-status") as HTMLDivElement;
  const uploadStatusText = $("upload-status-text") as HTMLSpanElement;
  const pillsBox = $("status-pills") as HTMLDivElement;
  const frameMeta = $("frame-meta") as HTMLSpanElement;
  const statTotal = $("stat-total") as HTMLDivElement;
  const statTotalSub = $("stat-total-sub") as HTMLDivElement;
  const statAndando = $("stat-andando") as HTMLDivElement;
  const statAndandoSub = $("stat-andando-sub") as HTMLDivElement;
  const statDescansando = $("stat-descansando") as HTMLDivElement;
  const statDescansandoSub = $("stat-descansando-sub") as HTMLDivElement;
  const statAgitados = $("stat-agitados") as HTMLDivElement;
  const statAgitadosSub = $("stat-agitados-sub") as HTMLDivElement;
  const statAlertas = $("stat-alertas") as HTMLDivElement;
  const statAlertasSub = $("stat-alertas-sub") as HTMLDivElement;
  const statComendo = $("stat-comendo") as HTMLDivElement;
  const statBebendo = $("stat-bebendo") as HTMLDivElement;
  const statBicando = $("stat-bicando") as HTMLDivElement;
  const logFilters = $("log-filters") as HTMLDivElement;
  const logSearch = $("log-search") as HTMLInputElement;
  const btnLogToggle = $("btn-log-toggle") as HTMLButtonElement;
  const btnLogClear = $("btn-log-clear") as HTMLButtonElement;
  const btnLogDownload = $("btn-log-download") as HTMLButtonElement;
  const btnRefreshIdentities = $("btn-refresh-identities") as HTMLButtonElement;
  const identityFilter = $("identity-filter") as HTMLSelectElement;
  const identitiesCount = $("identities-count") as HTMLSpanElement;
  const identitiesGallery = $("identities-gallery") as HTMLDivElement;

  // ===== Front-end state =====
  let logPaused = false;
  const activeFilters = new Set<string>(["sistema", "entrada"]);
  let searchText = "";
  let lastStateTs = 0;
  let lastReidInfo: Record<string, unknown> | null = null;

  // ===== PTZ + Overlay (preservado) =====
  const overlay = new PTZOverlay(overlayCanvas);
  const ptz = new VirtualPTZ(document.body);
  ptz.start();
  ptz.onChange((s) => overlay.update(s));

  const heatmapOv = new HeatmapOverlay(heatmapCanvas, CV_BASE);
  const pathOv = new PathOverlay(pathsCanvas, CV_BASE);
  // trilhas (paths) começam desligadas — geram borrão quando há várias aves paradas
  pathOv.setEnabled(false);
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

  // ===== Video drag/zoom/teclado (preservado) =====
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
  const lifetimeCounter = new LifetimeCounterPanel(lifetimeBody, lifetimeSummary);
  const naming = new NamingPanel(labelsList);

  const sourceSwitcher = new SourceSwitcher(document.body, CV_BASE);
  sourceSwitcher.setOnChange(() => {
    // reset local pra forçar refresh rápido do stream/heatmap/paths
    refreshLabels();
    refreshHeatmapClasses();
    refreshTimeline();
    refreshVideoList();
  });
  btnSource.addEventListener("click", () => sourceSwitcher.open());

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

  // ===== Log: helpers (appendLog/applyLogFilters/updateLogCount) =====
  function appendLog(kind: string, text: string, payload?: Record<string, unknown>): void {
    if (logPaused) return;
    const li = document.createElement("li");
    li.className = "kind-" + kind;
    li.dataset.kind = kind;
    const ts = new Date().toLocaleTimeString();
    li.dataset.search = text.toLowerCase();
    li.textContent = `${ts}  ${text}`;
    eventsList.prepend(li);
    while (eventsList.children.length > 200) eventsList.removeChild(eventsList.lastChild!);
    applyLogFilters();
    updateLogCount();
    void payload; // placeholder para futuro "log estruturado"
  }

  function applyLogFilters(): void {
    const lis = eventsList.querySelectorAll<HTMLElement>("li");
    const q = searchText.trim().toLowerCase();
    lis.forEach((li) => {
      const kind = li.dataset.kind || "";
      const search = li.dataset.search || "";
      const kindMatch = activeFilters.has(kind);
      const searchMatch = !q || search.includes(q);
      li.style.display = kindMatch && searchMatch ? "" : "none";
    });
  }

  function updateLogCount(): void {
    const total = eventsList.children.length;
    let visible = 0;
    eventsList.querySelectorAll<HTMLElement>("li").forEach((li) => {
      if (li.style.display !== "none") visible++;
    });
    const logCount = $("log-count");
    logCount.textContent = `${visible}/${total} eventos`;
  }

  // ===== Wire: log filters + search + buttons =====
  logFilters.addEventListener("click", (e) => {
    const chip = (e.target as HTMLElement).closest(".chip") as HTMLButtonElement | null;
    if (!chip) return;
    const filter = chip.dataset.filter || "";
    if (chip.classList.contains("active")) {
      chip.classList.remove("active");
      activeFilters.delete(filter);
    } else {
      chip.classList.add("active");
      activeFilters.add(filter);
    }
    applyLogFilters();
    updateLogCount();
  });

  logSearch.addEventListener("input", () => {
    searchText = logSearch.value;
    applyLogFilters();
    updateLogCount();
  });

  btnLogToggle.addEventListener("click", () => {
    logPaused = !logPaused;
    btnLogToggle.textContent = logPaused ? "▶ Retomar" : "⏸ Pausar";
    btnLogToggle.classList.toggle("active", logPaused);
    appendLog("sistema", logPaused ? "⏸ log pausado" : "▶ log retomado");
  });

  btnLogClear.addEventListener("click", () => {
    eventsList.innerHTML = "";
    updateLogCount();
    appendLog("sistema", "🧹 log limpo");
  });

  btnLogDownload.addEventListener("click", () => {
    const lines = Array.from(eventsList.querySelectorAll<HTMLElement>("li"))
      .filter((li) => li.style.display !== "none")
      .map((li) => li.textContent)
      .join("\n");
    const blob = new Blob([lines + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `wingscam-log-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    appendLog("sistema", `💾 log exportado (${lines.split("\n").filter(Boolean).length} linhas)`);
  });

  // ===== Wire: process start/stop =====
  btnStart.addEventListener("click", async () => {
    btnStart.disabled = true;
    try {
      const r = await fetch(`${CV_BASE}/api/process/start`, { method: "POST" });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status} ${t}`);
      }
      appendLog("sistema", "▶ processamento iniciado");
    } catch (err) {
      appendLog("debug", `falha iniciar: ${err}`);
    } finally {
      btnStart.disabled = false;
    }
  });

  btnStop.addEventListener("click", async () => {
    btnStop.disabled = true;
    try {
      const r = await fetch(`${CV_BASE}/api/process/stop`, { method: "POST" });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status} ${t}`);
      }
      appendLog("sistema", "⏸ processamento pausado");
    } catch (err) {
      appendLog("debug", `falha parar: ${err}`);
    } finally {
      btnStop.disabled = false;
    }
  });

  // ===== Wire: source controls + upload + video-select =====
  sourceSelect.addEventListener("change", async () => {
    const val = sourceSelect.value;
    if (val === "webcam") {
      try {
        const r = await fetch(`${CV_BASE}/api/source/webcam`, { method: "POST" });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        appendLog("sistema", "📷 fonte trocada: webcam");
      } catch (err) {
        appendLog("debug", `falha webcam: ${err}`);
        sourceSelect.value = "upload";
      }
    } else if (val === "upload") {
      fileInput.click();
    } else if (val === "url") {
      const url = prompt("URL RTSP/HTTP do stream:");
      if (!url) {
        sourceSelect.value = "upload";
        return;
      }
      try {
        const r = await fetch(`${CV_BASE}/api/source/url`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        appendLog("sistema", `🌐 fonte URL: ${url}`);
      } catch (err) {
        appendLog("debug", `falha URL: ${err}`);
        sourceSelect.value = "upload";
      }
    }
  });

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
    uploadStatus.hidden = false;
    uploadStatusText.textContent = `⏳ enviando ${file.name} (${sizeMb} MB)…`;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch(`${CV_BASE}/api/source/upload`, { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      const w = data.source?.width || "?";
      const h = data.source?.height || "?";
      uploadStatusText.textContent = `✓ ${file.name} (${w}x${h}) carregado`;
      appendLog("sistema", `📤 vídeo enviado: ${file.name} (${w}x${h})`);
      sourceSelect.value = "upload";
      await refreshVideoList();
    } catch (err) {
      uploadStatusText.textContent = `✗ falhou: ${err}`;
      appendLog("debug", `upload falhou: ${err}`);
      setTimeout(() => { uploadStatus.hidden = true; }, 7000);
      fileInput.value = "";
      return;
    }
    setTimeout(() => { uploadStatus.hidden = true; }, 5000);
    fileInput.value = "";
  });

  videoSelect.addEventListener("change", async () => {
    const path = videoSelect.value;
    if (!path) return;
    const name = path.split(/[/\\]/).pop() || path;
    try {
      const r = await fetch(`${CV_BASE}/api/source/path`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      appendLog("sistema", `🎬 vídeo trocado: ${name}`);
      sourceSelect.value = "upload";
    } catch (err) {
      appendLog("debug", `troca falhou: ${err}`);
    }
  });

  async function refreshVideoList(): Promise<void> {
    try {
      const r = await fetch(`${CV_BASE}/api/source/list`);
      if (!r.ok) return;
      const data = (await r.json()) as { files?: { path: string; name: string }[] };
      const list = data.files || [];
      // preserva seleção atual
      const prev = videoSelect.value;
      videoSelect.innerHTML = "";
      if (list.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "— nenhum vídeo enviado —";
        videoSelect.appendChild(opt);
        return;
      }
      for (const v of list) {
        const opt = document.createElement("option");
        opt.value = v.path;
        opt.textContent = v.name;
        videoSelect.appendChild(opt);
      }
      // tenta manter a seleção anterior
      if (prev && list.find((f) => f.path === prev)) {
        videoSelect.value = prev;
      }
    } catch { /* ok */ }
  }

  // ===== Wire: identities gallery =====
  btnRefreshIdentities.addEventListener("click", () => {
    refreshIdentities();
    appendLog("sistema", "↻ identidades recarregadas");
  });

  identityFilter.addEventListener("change", () => {
    refreshIdentities();
  });

  async function refreshIdentities(): Promise<void> {
    try {
      const typeParam = identityFilter.value ? `?type=${encodeURIComponent(identityFilter.value)}` : "";
      const r = await fetch(`${CV_BASE}/api/identities${typeParam}`);
      if (!r.ok) return;
      const data = await r.json() as {
        enabled?: boolean;
        message?: string;
        count?: number;
        identities?: Array<{
          id: number;
          name?: string;
          type?: string;
          seen_count?: number;
          last_seen?: number;
        }>;
      };
      const count = data.count || 0;
      identitiesCount.textContent = `${count} identidade${count === 1 ? "" : "s"}`;
      identitiesGallery.innerHTML = "";
      if (data.enabled === false) {
        identitiesGallery.innerHTML =
          `<div class="muted small" style="grid-column:1/-1;padding:24px;text-align:center">` +
          `ReID SQLite desativado — ligue com <code>CV_REID_SQLITE=1</code> no cv-service.` +
          `</div>`;
        return;
      }
      const ids = data.identities || [];
      if (ids.length === 0) {
        identitiesGallery.innerHTML =
          `<div class="muted small" style="grid-column:1/-1;padding:24px;text-align:center">` +
          `Nenhuma identidade persistente ainda. Rode o vídeo alguns minutos — cada galinha nova ` +
          `vira um card com thumbnail.` +
          `</div>`;
        return;
      }
      for (const ident of ids) {
        const card = document.createElement("div");
        card.className = "identity-card";
        const id = ident.id;
        const name = ident.name || `id-${id}`;
        const tipo = ident.type || "?";
        const seen = ident.seen_count || 0;
        const lastTs = ident.last_seen
          ? new Date(ident.last_seen * 1000).toLocaleString()
          : "—";
        const img = document.createElement("img");
        img.src = `${CV_BASE}/api/identity/${id}/thumbnail`;
        img.alt = name;
        img.loading = "lazy";
        img.addEventListener("error", () => {
          img.style.opacity = "0.25";
          img.alt = "(sem thumbnail)";
        });
        const nameEl = document.createElement("div");
        nameEl.className = "name";
        nameEl.textContent = name;
        const meta1 = document.createElement("div");
        meta1.className = "meta";
        meta1.textContent = `${tipo} · visto ${seen}×`;
        const meta2 = document.createElement("div");
        meta2.className = "meta";
        meta2.textContent = lastTs;
        card.appendChild(img);
        card.appendChild(nameEl);
        card.appendChild(meta1);
        card.appendChild(meta2);
        identitiesGallery.appendChild(card);
      }
    } catch (err) {
      appendLog("debug", `identidades falhou: ${err}`);
    }
  }

  // ===== Status pills + stat cards + frame meta =====
  function updateStatusPills(info: Record<string, unknown> | null): void {
    const pills = pillsBox.querySelectorAll<HTMLElement>(".pill");
    pills.forEach((p) => {
      p.classList.remove("ok", "warn", "bad");
      const id = p.dataset.id || "";
      // OpenCV/YOLO/model/behavior: dependem do cv-service estar vivo (assume ok
      // enquanto WS conectado). ReID: depende da flag sqlite_enabled.
      if (id === "reid") {
        const enabled = Boolean(info?.sqlite_enabled);
        const cached = Number(info?.cached_identities ?? 0);
        p.classList.add(enabled ? "ok" : "warn");
        p.textContent = enabled
          ? `ReID SQLite (${cached})`
          : "ReID SQLite OFF";
      } else if (id === "opencv") {
        p.classList.add("ok");
        p.textContent = "OpenCV";
      } else if (id === "yolo") {
        p.classList.add("ok");
        p.textContent = "Ultralytics YOLO";
      } else if (id === "model") {
        p.classList.add("ok");
        p.textContent = "pinteiro.pt";
      } else if (id === "behavior") {
        p.classList.add("ok");
        p.textContent = "Comportamento (zonas)";
      }
    });
  }

  function flashCard(card: HTMLElement | null): void {
    if (!card) return;
    card.classList.remove("flash");
    void card.offsetWidth; // força reflow para reiniciar a animação
    card.classList.add("flash");
  }

  function setStat(
    numEl: HTMLElement,
    subEl: HTMLElement,
    value: number | string,
    sub: string,
  ): void {
    const newVal = String(value);
    if (numEl.textContent !== newVal) {
      numEl.textContent = newVal;
      flashCard(numEl.closest(".stat"));
    }
    subEl.textContent = sub;
  }

  function updateStatCards(analytics: Record<string, unknown> | undefined): void {
    const a = analytics || {};
    const total = Number(a.total ?? 0);
    const ativos = Number(a.ativa ?? 0);
    const repouso = Number(a.repouso ?? 0);
    const anomalo = Number(a.anomalo ?? 0);
    const uniqueTotal = Number(a.unique_total ?? 0);
    const pctAtiva = Number(a.pct_ativa ?? 0);
    const pctRepouso = Number(a.pct_repouso ?? 0);
    const pctAnomalo = Number(a.pct_anomalo ?? 0);

    setStat(statTotal, statTotalSub, total, `únicas: ${uniqueTotal}`);
    setStat(statAndando, statAndandoSub, ativos, `${pctAtiva}%`);
    setStat(statDescansando, statDescansandoSub, repouso, `${pctRepouso}%`);
    setStat(statAgitados, statAgitadosSub, anomalo, `${pctAnomalo}%`);
    setStat(
      statAlertas,
      statAlertasSub,
      anomalo > 0 ? anomalo : 0,
      anomalo > 0 ? `anomalia ativa` : "ok",
    );
    // Comportamentos "comer/beber/bicar" ainda não modelados no backend v4 —
    // exibimos "--" mas mantemos o card pra roadmap futuro.
    setStat(statComendo, $("stat-comendo-sub"), "--", "em breve");
    setStat(statBebendo, $("stat-bebendo-sub"), "--", "em breve");
    setStat(statBicando, $("stat-bicando-sub"), "--", "em breve");
  }

  function updateFrameMeta(s: Record<string, unknown>): void {
    const loop = Number(s.loop_count ?? 0);
    const isWebcam = Boolean(s.is_webcam);
    const res = String(s.resolution || "?");
    if (isWebcam) {
      frameMeta.textContent = `webcam · ${res}`;
      return;
    }
    if (loop > 0) {
      frameMeta.textContent = `loop #${loop} · ${res}`;
    } else {
      frameMeta.textContent = `rodando · ${res}`;
    }
  }

  function updateProcessButtons(paused: boolean | undefined): void {
    if (paused) {
      btnStart.classList.add("primary-active");
      btnStop.classList.remove("primary-active");
      btnStart.textContent = "▶ Iniciar";
      btnStop.textContent = "⏹ Parado";
    } else {
      btnStart.classList.remove("primary-active");
      btnStop.classList.add("primary-active");
      btnStart.textContent = "▶ Rodando";
      btnStop.textContent = "⏹ Parar";
    }
  }

  // ===== Stream + WS (preservado, com appendLog enriquecido) =====
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
      // pathOv só inicia se o painel pedir (ver systemPanelUi.onPathsToggle)
      // começa desligado pra não borrar a tela com várias galinhas paradas
      refreshHeatmapClasses();
      refreshTimeline();
      if (e.panel?.paths_enabled) pathOv.setEnabled(true);
      appendLog("sistema", `cv-service inicializou · ${(e.labels || []).length} classes conhecidas`);
      refreshIdentities();
    } else if (e.type === "evento") {
      const ts = new Date(e.ts * 1000).toLocaleTimeString();
      const text = `${ts}  #${e.track_id}  ${e.classe}  → ${e.direcao.toUpperCase()}  (${(e.conf * 100).toFixed(0)}%)`;
      appendLog("entrada", text);
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
      appendLog(
        "entrada",
        `🆕 #${e.track_id} identificado como "${e.name}" (sim ${(e.sim * 100).toFixed(0)}%)`,
      );
    } else if (e.type === "tracks") {
      inlineNamer.setTracks(e.tracks as TrackInfo[]);
    } else if (e.type === "track_entered") {
      // cada galinha nova no frame → banner rápido
      newItemBanner.innerHTML = `
        <div class="new-item-info">
          <div class="muted">nova ave detectada</div>
          <div class="new-item-name">#${e.track_id} ${escapeHtml(e.classe)}</div>
          <div class="muted small">confiança ${(e.conf * 100).toFixed(0)}%</div>
        </div>
      `;
      newItemBanner.classList.add("show");
      if (newItemHideTimer !== null) window.clearTimeout(newItemHideTimer);
      newItemHideTimer = window.setTimeout(() => {
        newItemBanner.classList.remove("show");
      }, 2000);
      appendLog(
        "entrada",
        `🐔 #${e.track_id} ${e.classe} entrou no frame (conf ${(e.conf * 100).toFixed(0)}%)`,
      );
    } else if (e.type === "identified") {
      // evento detalhado de identificação por tipo (pinto/galinha/galo)
      appendLog(
        "entrada",
        `✓ #${e.track_id} → ${e.kind} (conf ${(e.conf * 100).toFixed(0)}%)`,
      );
    } else if (e.type === "ping") {
      /* keep-alive */
    }
  });

  // ===== Polling: state + process status =====
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/state`);
      if (!r.ok) return;
      const s = await r.json();
      updateStatCards(s.analytics || {});
      updateFrameMeta(s);
      if (s.analytics) analyticsPanel.setAnalytics(s.analytics);
      if (s.sensor) analyticsPanel.setSensor(s.sensor);
      if (s.palette) analyticsPanel.setPalette(s.palette);
      if (s.contagens) counter.setState(s.contagens);
      if (s.lifetime_contagens) {
        lifetimeCounter.setState(s.lifetime_contagens, s.loop_count ?? 0);
      }
      lastStateTs = performance.now();
    } catch { /* ok */ }
  }, 1000);

  // poll separado: status do loop (start/stop) — baixo custo
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/process/status`);
      if (!r.ok) return;
      const ps = await r.json() as { running?: boolean; paused?: boolean };
      updateProcessButtons(ps.paused);
    } catch { /* ok */ }
  }, 1500);

  // poll separado: ReID info (cada 5s, atualiza pill)
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/reid/info`);
      if (!r.ok) return;
      const info = await r.json();
      lastReidInfo = info;
      updateStatusPills(info);
    } catch { /* ok */ }
  }, 5000);

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
      connDot.parentElement?.classList.add("online");
      connDot.parentElement?.classList.remove("offline");
      connText.textContent = "conectado ao cv-service";
    } else {
      connDot.className = "dot bad";
      connDot.parentElement?.classList.remove("online");
      connDot.parentElement?.classList.add("offline");
      connText.textContent = "desconectado";
    }
  }

  // ===== Boot inicial =====
  refreshVideoList();
  refreshIdentities();
  // re-render após pequeno delay (WS init chega logo depois)
  setTimeout(refreshIdentities, 2500);
  updateStatusPills(null);
  updateProcessButtons(false);
  updateLogCount();
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
