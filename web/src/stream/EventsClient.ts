/**
 * EventsClient: WebSocket wrapper para /ws/events do cv-service.
 *
 * Tipos de mensagem:
 *   - init:        estado inicial ao conectar
 *   - evento:      cruzamento de linha (IN/OUT)
 *   - novo_item:   detecção de item nunca visto — auto-nomeado ItemN
 *   - tracks:      snapshot das tracks atuais (pra hit-test no canvas)
 *   - ping:        keep-alive
 */
export type EventKind = "init" | "evento" | "novo_item" | "tracks" | "ping";

export interface CrossEvent {
  type: "evento";
  ts: number;
  track_id: number;
  classe: string;
  direcao: "in" | "out";
  conf: number;
}

export interface InitEvent {
  type: "init";
  contagens: Record<string, { in: number; out: number; current: number }>;
  resolution: [number, number];
  line_orientation: "horizontal" | "vertical";
  line_position: number;
  labels: Array<{ name: string; samples: number; created_at?: string }>;
  panel: Record<string, unknown>;
}

export interface NewItemEvent {
  type: "novo_item";
  track_id: number;
  name: string;
  sim: number;
  crop: string; // base64 JPEG
}

export interface TrackSnapEntry {
  track_id: number;
  cls_name: string;
  bbox: [number, number, number, number];
  conf: number;
}

export interface TracksEvent {
  type: "tracks";
  tracks: TrackSnapEntry[];
}

export type WsEvent = InitEvent | CrossEvent | NewItemEvent | TracksEvent | { type: "ping" };

export class EventsClient {
  private url: string;
  private ws: WebSocket | null = null;
  private retryDelay = 1000;
  private disposed = false;
  private listeners: ((e: WsEvent) => void)[] = [];
  private onStatus?: (connected: boolean) => void;

  constructor(url: string) {
    this.url = url;
  }

  start(onStatus?: (connected: boolean) => void): void {
    this.onStatus = onStatus;
    this.connect();
  }

  stop(): void {
    this.disposed = true;
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      this.ws.close();
      this.ws = null;
    }
  }

  onEvent(fn: (e: WsEvent) => void): () => void {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== fn);
    };
  }

  private connect(): void {
    if (this.disposed) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.retryDelay = 1000;
      this.onStatus?.(true);
    };
    this.ws.onclose = () => {
      this.onStatus?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      this.onStatus?.(false);
    };
    this.ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data) as WsEvent;
        for (const l of this.listeners) l(data);
      } catch {
        // ignora payloads não-JSON
      }
    };
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    const delay = Math.min(this.retryDelay, 10000);
    this.retryDelay = Math.min(this.retryDelay * 2, 10000);
    setTimeout(() => this.connect(), delay);
  }
}
