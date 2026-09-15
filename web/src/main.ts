/**
 * Entry point: orquestra MJPEG, WS eventos, PTZ virtual, naming panel e contagem.
 *
 * v2: NamingPanel para identificar e renomear classes. Endpoint PATCH/DELETE /api/labels.
 */
import { MjpegClient } from "./stream/MjpegClient";
import { JpegPollingClient } from "./stream/JpegPollingClient";
import { EventsClient, type WsEvent } from "./stream/EventsClient";
import { CounterPanel } from "./counter/CounterPanel";
import { NamingPanel, type LabelEntry } from "./naming/NamingPanel";
import { PTZOverlay } from "./ptz/Overlay";
import { VirtualPTZ } from "./ptz/VirtualPTZ";

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
  const counterBody = $("counter-body") as HTMLTableSectionElement;
  const labelsList = $("labels-list") as HTMLDivElement;
  const eventsList = $("events") as HTMLUListElement;
  const newItemBanner = $("new-item-banner") as HTMLDivElement;
  const connDot = $("conn-dot") as HTMLSpanElement;
  const connText = $("conn-text") as HTMLSpanElement;

  const cvUrl = $("cv-url") as HTMLElement;
  const cvCamera = $("cv-camera") as HTMLElement;
  const cvModel = $("cv-model") as HTMLElement;
  const cvRes = $("cv-resolution") as HTMLElement;
  const cvLine = $("cv-line") as HTMLElement;
  cvUrl.textContent = CV_BASE === "/cv" ? "(proxy Vite → 127.0.0.1:8000)" : CV_BASE;

  // ---- PTZ + Overlay ----
  const overlay = new PTZOverlay(overlayCanvas);
  const ptz = new VirtualPTZ(document.body);
  ptz.start();
  ptz.onChange((s) => overlay.update(s));

  // Drag no stage = pan/tilt
  const videoWrap = $("video-wrap") as HTMLDivElement;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  videoWrap.addEventListener("pointerdown", (e) => {
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

  window.addEventListener("keydown", (e) => {
    if (e.code === "Digit0") ptz.reset();
  });

  // ---- Painéis ----
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
      } else {
        const err = await r.json().catch(() => ({}));
        alert(`Falha ao renomear: ${err.error || r.status}`);
      }
    } catch (e) {
      alert(`Erro: ${e}`);
    }
  };

  naming.onDelete = async (name) => {
    try {
      const r = await fetch(`${CV_BASE}/api/labels/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (r.ok) {
        await refreshLabels();
      } else {
        const err = await r.json().catch(() => ({}));
        alert(`Falha ao apagar: ${err.error || r.status}`);
      }
    } catch (e) {
      alert(`Erro: ${e}`);
    }
  };

  // ---- Stream (JPEG polling — mais robusto que MJPEG em <img>) ----
  const stream = new JpegPollingClient(mjpegImg, CV_BASE, 80);
  stream.start(() => {
    console.warn("[stream] falha ao buscar frame, retentando...");
  });

  const events = new EventsClient(wsUrlFor(CV_BASE));
  events.start((connected) => setStatus(connected));

  let newItemHideTimer: number | null = null;

  events.onEvent((e: WsEvent) => {
    if (e.type === "init") {
      cvCamera.textContent = "—";
      cvModel.textContent = "—";
      cvRes.textContent = `${e.resolution[0]}×${e.resolution[1]}`;
      cvLine.textContent = `${e.line_orientation} @ ${e.line_position.toFixed(2)}`;
      counter.setState(e.contagens);
      naming.setLabels(e.labels || []);
      fetch(`${CV_BASE}/api/state`)
        .then((r) => r.json())
        .then((s) => {
          if (s.camera_id) cvCamera.textContent = s.camera_id;
          if (s.model) cvModel.textContent = s.model;
          if (s.resolution) cvRes.textContent = s.resolution;
        })
        .catch(() => {});
    } else if (e.type === "evento") {
      const li = document.createElement("li");
      li.className = e.direcao;
      const ts = new Date(e.ts * 1000).toLocaleTimeString();
      li.textContent = `${ts}  #${e.track_id}  ${e.classe}  → ${e.direcao.toUpperCase()}  (${(e.conf * 100).toFixed(0)}%)`;
      eventsList.prepend(li);
      while (eventsList.children.length > 50) eventsList.removeChild(eventsList.lastChild!);
    } else if (e.type === "novo_item") {
      // Banner com crop + nome
      newItemBanner.innerHTML = `
        <img alt="crop" src="data:image/jpeg;base64,${e.crop}" />
        <div class="new-item-info">
          <div class="muted">novo item detectado</div>
          <div class="new-item-name">${escapeHtml(e.name)}</div>
          <div class="muted small">track #${e.track_id} · similaridade máx ${(e.sim * 100).toFixed(0)}%</div>
          <div class="muted small">renomeie na lista à direita se quiser identificar</div>
        </div>
      `;
      newItemBanner.classList.add("show");
      if (newItemHideTimer !== null) window.clearTimeout(newItemHideTimer);
      newItemHideTimer = window.setTimeout(() => {
        newItemBanner.classList.remove("show");
      }, 4000);

      naming.upsertLabel({ name: e.name, samples: 1 });
      refreshLabels();
    } else if (e.type === "ping") {
      // keep-alive
    }
  });

  // Polling leve do /api/state para o counter atualizar mesmo sem cruzamento novo
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/state`);
      if (!r.ok) return;
      const s = await r.json();
      if (s.contagens) counter.setState(s.contagens);
    } catch { /* ok */ }
  }, 1000);

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
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
