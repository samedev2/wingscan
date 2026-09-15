/**
 * Entry point: orquestra MJPEG, WS eventos, PTZ virtual e painéis.
 *
 * URL base do cv-service é resolvida assim:
 *  - Em dev (Vite), o proxy em /cv -> http://127.0.0.1:8000 já está configurado.
 *  - Se o front for hospedado junto com o cv-service, basta apontar a env.
 */
import { MjpegClient } from "./stream/MjpegClient";
import { EventsClient, type WsEvent } from "./stream/EventsClient";
import { CounterPanel } from "./counter/CounterPanel";
import { PTZOverlay } from "./ptz/Overlay";
import { VirtualPTZ } from "./ptz/VirtualPTZ";

// Em dev o front roda em :5173 e o Vite faz proxy de /cv/* -> :8000.
// Em produção servidos juntos, deixe CV_BASE vazio para usar a mesma origem.
const CV_BASE = (import.meta.env?.VITE_CV_BASE as string | undefined) ?? "/cv";

function wsUrlFor(base: string): string {
  const clean = base.replace(/\/$/, "");
  if (clean.startsWith("http://")) return clean.replace(/^http/, "ws") + "/ws/events";
  if (clean.startsWith("https://")) return clean.replace(/^https/, "wss") + "/ws/events";
  // relativo (proxy do Vite) — usa mesmo host:porta do front
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${clean}/ws/events`;
}

function init(): void {
  const $ = <T extends HTMLElement>(id: string): T => {
    const el = document.getElementById(id);
    if (!el) throw new Error(`#${id} não encontrado`);
    return el as T;
  };

  const stage = $("stage") as HTMLDivElement;
  const videoWrap = $("video-wrap") as HTMLDivElement;
  const mjpegImg = $("mjpeg") as HTMLImageElement;
  const overlayCanvas = $("overlay") as HTMLCanvasElement;
  const counterBody = $("counter-body") as HTMLTableSectionElement;
  const eventsList = $("events") as HTMLUListElement;
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

  // Mouse drag no stage = pan/tilt manual (independente do spatial-controls).
  // Scroll = zoom manual. Esses são "atalhos" — keyboard passa pela lib.
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
    // Aplica direto no Vector3 do PTZ; damping da lib suaviza depois.
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

  // Tecla "0" reseta
  window.addEventListener("keydown", (e) => {
    if (e.code === "Digit0") ptz.reset();
  });

  // ---- Counter ----
  const counter = new CounterPanel(counterBody);

  // ---- MJPEG + WS ----
  const mjpeg = new MjpegClient(mjpegImg, CV_BASE);
  mjpeg.start((err) => {
    console.warn("[mjpeg] erro, tentando reconectar...", err);
    setStatus(false);
  });

  const events = new EventsClient(wsUrlFor(CV_BASE));
  events.start((connected) => setStatus(connected));
  events.onEvent((e: WsEvent) => {
    if (e.type === "init") {
      cvCamera.textContent = "—";
      cvModel.textContent = "—";
      cvRes.textContent = `${e.resolution[0]}×${e.resolution[1]}`;
      cvLine.textContent = `${e.line_orientation} @ ${e.line_position.toFixed(2)}`;
      counter.setState(e.contagens);
      // fetch /api/state para dados completos
      fetch(`${CV_BASE}/api/state`)
        .then((r) => r.json())
        .then((s) => {
          if (s.camera_id) cvCamera.textContent = s.camera_id;
          if (s.model) cvModel.textContent = s.model;
          if (s.resolution) cvRes.textContent = s.resolution;
          if (s.classes) cvLine.textContent = `${s.line_orientation} @ ${s.line_position} — classes: ${s.classes.join(",")}`;
        })
        .catch(() => {});
    } else if (e.type === "evento") {
      const li = document.createElement("li");
      li.className = e.direcao;
      const ts = new Date(e.ts * 1000).toLocaleTimeString();
      li.textContent = `${ts}  #${e.track_id}  ${e.classe}  → ${e.direcao.toUpperCase()}  (${(e.conf * 100).toFixed(0)}%)`;
      eventsList.prepend(li);
      while (eventsList.children.length > 50) eventsList.removeChild(eventsList.lastChild!);
    } else if (e.type === "ping") {
      // keep-alive, ignora
    }
  });

  // Polling leve do /api/state para o counter atualizar mesmo sem cruzamento novo
  setInterval(async () => {
    try {
      const r = await fetch(`${CV_BASE}/api/state`);
      if (!r.ok) return;
      const s = await r.json();
      if (s.contagens) counter.setState(s.contagens);
    } catch {
      // ok, segue tentando
    }
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

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
