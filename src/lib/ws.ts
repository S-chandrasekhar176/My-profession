// ─────────────────────────────────────────────
// UltraBot WebSocket Manager
// ─────────────────────────────────────────────
// Auto-reconnect with exponential backoff.
// Event-based pub/sub API.
// ─────────────────────────────────────────────

type EventCallback = (...args: unknown[]) => void;

interface WsConfig {
  url?: string;
  reconnectBaseMs?: number;
  reconnectMaxMs?: number;
  maxRetries?: number;
}

function getWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost:8000/ws';
  // Explicit override wins everywhere (e.g. gateway deployments:
  // wss://host/ws?XTransformPort=8000).
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  // Personal-use topology: frontend (:3000) and backend (:8000) run on the
  // same host. Connect straight to the backend — the Next.js server has no
  // /ws proxy (rewrites do not support WebSocket), so a page-relative
  // wss://host/ws URL can never work.
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.hostname}:8000/ws`;
}

const DEFAULT_CONFIG: Required<WsConfig> = {
  url: getWsUrl(),
  reconnectBaseMs: 1000,
  reconnectMaxMs: 30_000,
  maxRetries: 30,
};

class WebSocketManager {
  private ws: WebSocket | null = null;
  private config: Required<WsConfig>;
  private listeners: Map<string, Set<EventCallback>> = new Map();
  private retryCount = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _connected = false;
  private _lastMessage: unknown = null;
  private intentionalClose = false;

  constructor(config?: WsConfig) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ── Public state ─────────────────────────

  get connected(): boolean {
    return this._connected;
  }

  get lastMessage(): unknown {
    return this._lastMessage;
  }

  // ── Connect ───────────────────────────────

  connect(token?: string): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return; // already connected or connecting
    }

    this.intentionalClose = false;

    const url = token
      ? `${this.config.url}?token=${encodeURIComponent(token)}`
      : this.config.url;

    try {
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        this._connected = true;
        this.retryCount = 0;
        this.emit('connected');
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const payload = JSON.parse(event.data as string);
          this._lastMessage = payload;

          // Emit on the specific channel if present
          if (payload.channel && typeof payload.channel === 'string') {
            this.emit(payload.channel, payload.data ?? payload);
          }

          // Also emit on generic message event
          this.emit('message', payload);
        } catch {
          // non-JSON message, ignore or emit raw
          this.emit('raw_message', event.data);
        }
      };

      this.ws.onclose = (event: CloseEvent) => {
        this._connected = false;
        this.emit('disconnected', event);

        // 1008 indicates policy violation / invalid or expired JWT token
        if (event.code === 1008) {
          this.emit('auth_error', event);
          const latestToken = typeof window !== 'undefined' ? localStorage.getItem('ultrabot_token') : null;
          if (latestToken && latestToken !== token && !this.intentionalClose) {
            this.scheduleReconnect(latestToken);
          }
          return;
        }

        if (!this.intentionalClose) {
          this.scheduleReconnect(token);
        }
      };

      this.ws.onerror = (event: Event) => {
        this.emit('error', event);
      };
    } catch (err) {
      this.emit('error', err);
      if (!this.intentionalClose) {
        this.scheduleReconnect(token);
      }
    }
  }

  // ── Disconnect ──────────────────────────

  disconnect(): void {
    this.intentionalClose = true;
    this.clearReconnectTimer();

    if (this.ws) {
      this.ws.onclose = null; // prevent auto-reconnect
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }

    this._connected = false;
    this.emit('disconnected', { code: 1000, reason: 'Client disconnect' });
  }

  // ── Send ──────────────────────────────────

  send(channel: string, data?: unknown): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[WS] Cannot send — not connected');
      return;
    }

    const payload = data !== undefined ? { channel, data } : { channel };
    this.ws.send(JSON.stringify(payload));
  }

  // ── Subscribe / Unsubscribe ──────────────

  on(event: string, callback: EventCallback): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);

    // Return unsubscribe function
    return () => {
      this.listeners.get(event)?.delete(callback);
      if (this.listeners.get(event)?.size === 0) {
        this.listeners.delete(event);
      }
    };
  }

  off(event: string, callback: EventCallback): void {
    this.listeners.get(event)?.delete(callback);
    if (this.listeners.get(event)?.size === 0) {
      this.listeners.delete(event);
    }
  }

  // ── Reconnect logic ───────────────────────

  private scheduleReconnect(token?: string): void {
    this.clearReconnectTimer();

    if (this.retryCount >= this.config.maxRetries) {
      this.emit('reconnect_failed');
      return;
    }

    const delay = Math.min(
      this.config.reconnectBaseMs * Math.pow(2, this.retryCount),
      this.config.reconnectMaxMs,
    );

    this.retryCount += 1;
    this.emit('reconnecting', { attempt: this.retryCount, delay });

    this.reconnectTimer = setTimeout(() => {
      this.connect(token);
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  // ── Emit ──────────────────────────────────

  private emit(event: string, ...args: unknown[]): void {
    this.listeners.get(event)?.forEach((cb) => {
      try {
        cb(...args);
      } catch (err) {
        console.error(`[WS] Error in listener for "${event}":`, err);
      }
    });
  }
}

// ── Singleton ─────────────────────────────────
export const wsManager = new WebSocketManager();
export default wsManager;
