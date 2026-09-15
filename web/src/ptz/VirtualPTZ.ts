/**
 * VirtualPTZ: usa spatial-controls como "cérebro" de input/damping/keybindings
 * para acumular pan/tilt/zoom num Vector3 (x=pan, y=tilt, z=zoom).
 *
 * O construtor do spatial-controls v6 aceita `{ spatial, domElement }` e o
 * `spatial` precisa ter `position` (Vector3), `quaternion` (Quaternion) e
 * `scale` (Vector3). O controls.update() move `position` com damping aplicado.
 *
 * Mapeamento:
 *   X = pan (esquerda/direita)
 *   Y = tilt (cima/baixo)
 *   Z = zoom (forward = zoom in, backward = zoom out)
 *
 * O rotation da câmera 3D não é usado (rotation.enabled = false).
 */
import { Quaternion, Vector3 } from "three";
import { SpatialControls } from "spatial-controls";

export interface PTZState {
  pan: number;
  tilt: number;
  zoom: number;
}

export class VirtualPTZ {
  /** Estado PTZ acumulado com damping aplicado. */
  readonly ptz: Vector3;

  /** Instância subjacente do spatial-controls. */
  readonly controls: SpatialControls;

  private readonly listeners = new Set<(s: PTZState) => void>();
  private raf: number | null = null;

  /** Limites do zoom (fração; 1 = sem zoom, 4 = 4x). */
  readonly zoomMin = 1.0;
  readonly zoomMax = 4.0;

  /** Limites opcionais de pan/tilt em coordenadas normalizadas. */
  readonly panLimit = 1.5;
  readonly tiltLimit = 1.5;

  constructor(domElement: HTMLElement) {
    this.ptz = new Vector3(0, 0, 1);

    const quaternion = new Quaternion();
    const scale = new Vector3(1, 1, 1);
    const spatial = { position: this.ptz, quaternion, scale };

    this.controls = new SpatialControls({ spatial, domElement });
    this.configure();
  }

  private configure(): void {
    const s = this.controls.settings;

    // Movement direto em X/Y/Z (sem orientação 3D).
    s.general.mode = "first-person";

    s.translation.enabled = true;
    s.translation.sensitivity = 0.6;
    s.translation.damping = 0.12;
    s.translation.boostMultiplier = 2.0;
    s.translation.setAxisWeights(1, 1, 1);

    s.rotation.enabled = false; // não usamos rotação 3D
    s.dolly.enabled = false;    // zoom tratado via translation Z (first-person não tem dolly)

    // Limpa keybindings default e configura PTZ.
    const kb = s.keyBindings;
    for (const k of [...kb.keys()]) {
      kb.delete(k);
    }
    kb.set("KeyA", "move-left");      // X-
    kb.set("KeyD", "move-right");     // X+
    kb.set("KeyW", "move-forward");   // Z- = zoom in
    kb.set("KeyS", "move-backward");  // Z+ = zoom out
    kb.set("ArrowLeft", "move-left");
    kb.set("ArrowRight", "move-right");
    kb.set("ArrowUp", "move-up");     // Y+ = tilt up
    kb.set("ArrowDown", "move-down"); // Y- = tilt down
    kb.set("KeyR", "boost");          // Shift Left removido; "R" como boost
    kb.set("KeyZ", "move-down");      // alias

    // Limpa pointer bindings (vamos tratar mouse manualmente).
    const pb = s.pointerBindings;
    for (const k of [...pb.keys()]) {
      pb.delete(k);
    }
  }

  /** Inicia o loop de update() com rAF. */
  start(): void {
    if (this.raf !== null) return;
    const tick = (t: number) => {
      this.raf = requestAnimationFrame(tick);
      this.controls.update(t);
      this.clamp();
      this.emit();
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    if (this.raf !== null) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
  }

  onChange(fn: (s: PTZState) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  getState(): PTZState {
    return { pan: this.ptz.x, tilt: this.ptz.y, zoom: this.ptz.z };
  }

  reset(): void {
    this.ptz.set(0, 0, 1);
    this.emit();
  }

  private clamp(): void {
    if (this.ptz.x > this.panLimit) this.ptz.x = this.panLimit;
    else if (this.ptz.x < -this.panLimit) this.ptz.x = -this.panLimit;
    if (this.ptz.y > this.tiltLimit) this.ptz.y = this.tiltLimit;
    else if (this.ptz.y < -this.tiltLimit) this.ptz.y = -this.tiltLimit;
    if (this.ptz.z > this.zoomMax) this.ptz.z = this.zoomMax;
    else if (this.ptz.z < this.zoomMin) this.ptz.z = this.zoomMin;
  }

  private emit(): void {
    const s: PTZState = { pan: this.ptz.x, tilt: this.ptz.y, zoom: this.ptz.z };
    for (const l of this.listeners) l(s);
  }
}
