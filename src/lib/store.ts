import { create } from 'zustand';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export type EngineStatus = 'running' | 'stopped' | 'paused' | 'error';
export type EngineMode = 'paper' | 'live';
export type MarketRegime = 'bull' | 'bear' | 'sideways' | 'volatile';

export interface Opportunity {
  id: string;
  symbol: string;
  type: string;
  direction: 'BUY' | 'SELL';
  entry: number;
  target: number;
  stopLoss: number;
  riskReward: number;
  confidence: number;
  strategy: string;
  timestamp: string;
  status?: string;
  conviction_score?: number;
  conviction_stars?: number;
  conviction_label?: string;
  risk_gates?: any[];
  riskGates?: any[];
  margin?: number;
  capital_required?: number;
  capitalRequired?: number;
  quantity?: number;
  expiryAt?: string;
  expiry_at?: string;
  ttlSeconds?: number;
  ttl_seconds?: number;
  invalidationReason?: string;
  rejectionReason?: string;
  raw?: any;
}

export function normalizeOpportunity(raw: any): Opportunity {
  if (!raw || typeof raw !== 'object') {
    return {
      id: '',
      symbol: '',
      type: 'EQ',
      direction: 'BUY',
      entry: 0,
      target: 0,
      stopLoss: 0,
      riskReward: 0,
      confidence: 0,
      strategy: '',
      timestamp: new Date().toISOString(),
    };
  }

  const rawDir = String(raw.direction || 'BUY').toUpperCase();
  const dir: 'BUY' | 'SELL' = rawDir === 'SELL' || rawDir === 'SHORT' ? 'SELL' : 'BUY';

  const entry = Number(raw.entry ?? raw.entry_price ?? 0);
  const target = Number(raw.target ?? raw.target_price ?? 0);
  const stopLoss = Number(raw.stopLoss ?? raw.stop_loss ?? raw.sl_price ?? 0);
  const riskReward = Number(raw.riskReward ?? raw.risk_reward ?? 0);
  const confidence = Number(raw.confidence ?? raw.kronosScore ?? 0);

  return {
    id: String(raw.id || ''),
    symbol: String(raw.symbol || ''),
    type: String(raw.type || raw.segment || 'EQ'),
    direction: dir,
    entry,
    target,
    stopLoss,
    riskReward,
    confidence,
    strategy: String(raw.strategy || raw.strategy_name || ''),
    timestamp: String(raw.timestamp || raw.created_at || new Date().toISOString()),
    status: raw.status,
    conviction_score: raw.conviction_score,
    conviction_stars: raw.conviction_stars,
    conviction_label: raw.conviction_label,
    risk_gates: raw.risk_gates || raw.riskGates || (raw.risk_result ? raw.risk_result.all_gates : undefined),
    riskGates: raw.riskGates || raw.risk_gates || (raw.risk_result ? raw.risk_result.all_gates : undefined),
    margin: Number(raw.margin ?? raw.capital_required ?? raw.capitalRequired ?? (raw.quantity && entry ? entry * raw.quantity * 0.2 : 0)),
    capitalRequired: Number(raw.capitalRequired ?? raw.capital_required ?? raw.margin ?? 0),
    quantity: Number(raw.quantity ?? raw.sizing?.quantity ?? 1),
    ttlSeconds: raw.ttl_seconds ?? raw.ttlSeconds,
    ttl_seconds: raw.ttl_seconds ?? raw.ttlSeconds,
    expiryAt: raw.expiryAt || raw.expiry_at || raw.expiry,
    expiry_at: raw.expiry_at || raw.expiryAt || raw.expiry,
    invalidationReason: raw.invalidationReason || raw.invalidation_reason,
    rejectionReason: raw.rejectionReason || raw.rejection_reason,
    raw,
  };
}

export interface LivePrice {
  symbol: string;
  ltp: number;
  change: number;
  changePercent: number;
  high: number;
  low: number;
  open: number;
  volume: number;
}

// ─────────────────────────────────────────────
// Broker Types
// ─────────────────────────────────────────────

export const BROKER_LIST = [
  { id: 'paper', name: 'Paper Broker', needsCredentials: false, category: 'paper' as const },
  { id: 'yahoofinance', name: 'Yahoo Finance', needsCredentials: false, category: 'paper' as const },
  { id: 'zerodha', name: 'Zerodha', needsCredentials: true, category: 'live' as const },
  { id: 'angel_one', name: 'Angel One', needsCredentials: true, category: 'live' as const },
  { id: 'dhan', name: 'Dhan', needsCredentials: true, category: 'live' as const },
  { id: 'fyers', name: 'Fyers', needsCredentials: true, category: 'live' as const },
  { id: 'shoonya', name: 'Shoonya', needsCredentials: true, category: 'live' as const },
] as const;

export type BrokerId = (typeof BROKER_LIST)[number]['id'];

export interface BrokerCredentialFields {
  [key: string]: string;
}

// Credential field definitions per broker
export const BROKER_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: 'password' }[]> = {
  zerodha: [
    { key: 'apiKey', label: 'API Key', placeholder: 'Your Kite Connect API key' },
    { key: 'apiSecret', label: 'API Secret', placeholder: 'Your Kite Connect API secret', type: 'password' },
    { key: 'userId', label: 'User ID', placeholder: 'e.g. AB1234' },
    { key: 'accessToken', label: 'Access Token (daily)', placeholder: 'Kite access token — expires daily, re-enter each morning', type: 'password' },
  ],
  angel_one: [
    { key: 'apiKey', label: 'SmartAPI Key', placeholder: 'Your Angel One API key' },
    { key: 'clientCode', label: 'Client Code', placeholder: 'Your client code' },
    { key: 'pin', label: 'PIN', placeholder: 'Your PIN', type: 'password' },
    { key: 'totpSecret', label: 'TOTP Secret', placeholder: 'For auto-login (optional)', type: 'password' },
  ],
  dhan: [
    { key: 'clientId', label: 'Client ID', placeholder: 'Your Dhan Client ID (e.g. 1000000123)' },
    { key: 'accessToken', label: 'Access Token (JWT)', placeholder: 'Optional with TOTP — auto-generated by Re-login', type: 'password' },
    { key: 'pin', label: 'PIN', placeholder: '6-digit Dhan PIN (enables auto re-login)', type: 'password' },
    { key: 'totpSecret', label: 'TOTP Secret', placeholder: 'TOTP secret from web.dhan.co (enables auto re-login)', type: 'password' },
  ],
  fyers: [
    { key: 'appId', label: 'App ID / Client ID', placeholder: 'Your Fyers App ID (e.g. XC12345-100)' },
    { key: 'secretKey', label: 'Secret Key', placeholder: 'Your Fyers App Secret Key', type: 'password' },
    { key: 'redirectUri', label: 'Redirect URI', placeholder: 'Must match the URI registered in your Fyers app (e.g. http://127.0.0.1:8000/api/brokers/fyers/callback)' },
    { key: 'accessToken', label: 'Access Token (auto-filled after Connect)', placeholder: 'Click "Connect / Re-authenticate" below instead of pasting this manually', type: 'password' },
    { key: 'pin', label: 'User PIN', placeholder: 'Your Fyers PIN (optional)', type: 'password' },
  ],
  shoonya: [
    { key: 'userId', label: 'User ID', placeholder: 'Your Shoonya User ID' },
    { key: 'password', label: 'Password', placeholder: 'Your Shoonya Password', type: 'password' },
    { key: 'vendorCode', label: 'Vendor Code', placeholder: 'Your Shoonya Vendor Code (e.g. FA12345_U)' },
    { key: 'appKey', label: 'API / App Key', placeholder: 'Your Shoonya API App Key', type: 'password' },
    { key: 'totpSecret', label: 'TOTP Secret', placeholder: 'Your TOTP Secret Key', type: 'password' },
  ],
  yahoofinance: [
    { key: 'symbols', label: 'Symbol List', placeholder: 'e.g. ^NSEI, ^NSEBANK, RELIANCE.NS' },
  ],
  paper: [],
};

// ─────────────────────────────────────────────
// Auth Slice
// ─────────────────────────────────────────────

export interface AuthSlice {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  login: (token: string, username: string) => void;
  logout: () => void;
  hydrate: () => void;
}

// ─────────────────────────────────────────────
// Engine Slice
// ─────────────────────────────────────────────

export interface EngineSlice {
  status: EngineStatus;
  mode: EngineMode;
  regime: MarketRegime;
  vix: number;
  niftyValue: number;
  niftyChange: number;
  marketCloseSeconds: number;
  activeBroker: string | null;
  startedAt: number | null;
  errorMessage: string | null;
  lastHeartbeat: number | null;
  scanTelemetry: any | null;
  setEngineStatus: (status: EngineStatus) => void;
  setMode: (mode: EngineMode) => void;
  setRegime: (regime: MarketRegime) => void;
  setVix: (vix: number) => void;
  setNifty: (value: number, change: number) => void;
  setMarketCloseSeconds: (seconds: number) => void;
  setActiveBroker: (broker: string | null) => void;
  setErrorMessage: (msg: string | null) => void;
  setScanTelemetry: (telemetry: any) => void;
  addTelemetryEvent: (event: any) => void;
  addTelemetryEvents: (events: any[]) => void;
  start: (mode: EngineMode, brokerId: string) => void;
  stop: () => void;
  heartbeat: () => void;
  hydrateEngine: () => void;
}

// ─────────────────────────────────────────────
// Realtime Slice
// ─────────────────────────────────────────────

export interface RealtimeSlice {
  livePrices: Record<string, LivePrice>;
  opportunities: Opportunity[];
  updatePrice: (price: LivePrice) => void;
  updatePrices: (prices: LivePrice[]) => void;
  addOpportunity: (opportunity: Opportunity | Record<string, any>) => void;
  removeOpportunity: (id: string) => void;
  setOpportunities: (opportunities: (Opportunity | Record<string, any>)[]) => void;
  clearOpportunities: () => void;
}

// ─────────────────────────────────────────────
// Sidebar Slice
// ─────────────────────────────────────────────

export interface SidebarSlice {
  collapsed: boolean;
  mobileOpen: boolean;
  toggle: () => void;
  setCollapsed: (collapsed: boolean) => void;
  setMobileOpen: (open: boolean) => void;
}

// ─────────────────────────────────────────────
// Brokers Slice
// ─────────────────────────────────────────────

/** Per-broker configured status hydrated from the backend
 *  (GET /api/brokers — has_credentials / auth_status in the DB). */
export interface BackendBrokerStatus {
  broker: string;
  isActive: boolean;
  hasCredentials: boolean;
  authStatus?: string | null;
  lastAuth?: string | null;
  accountType?: string;
}

export interface BrokersSlice {
  /** In-memory draft of the credential form (NEVER persisted — secrets
   *  are stored encrypted in the backend DB only). */
  credentials: Record<string, BrokerCredentialFields>;
  /** Configured-on-backend status from GET /api/brokers. */
  backendStatus: Record<string, BackendBrokerStatus>;
  saveBrokerCredentials: (brokerId: string, fields: BrokerCredentialFields) => void;
  clearBrokerCredentials: (brokerId: string) => void;
  setBackendBrokerStatus: (statuses: BackendBrokerStatus[]) => void;
  isBrokerConfigured: (brokerId: string) => boolean;
}

// ─────────────────────────────────────────────
// Combined Store
// ─────────────────────────────────────────────

interface StoreState {
  auth: AuthSlice;
  engine: EngineSlice;
  realtime: RealtimeSlice;
  sidebar: SidebarSlice;
  brokers: BrokersSlice;
}

// ─────────────────────────────────────────────
// Store — actions inside each slice
// ─────────────────────────────────────────────

// NOTE: broker credentials are intentionally NOT persisted to localStorage —
// the backend DB (encrypted at rest) is the single source of truth.

export const useStore = create<StoreState>((set, get) => ({
  auth: {
    token: null,
    username: null,
    isAuthenticated: false,

    login(token: string, username: string) {
      if (typeof window !== 'undefined') {
        localStorage.setItem('ultrabot_token', token);
        localStorage.setItem('ultrabot_username', username);
      }
      set({ auth: { ...get().auth, token, username, isAuthenticated: true } });
    },

    logout() {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('ultrabot_token');
        localStorage.removeItem('ultrabot_username');
      }
      set({ auth: { ...get().auth, token: null, username: null, isAuthenticated: false } });
    },

    hydrate() {
      if (typeof window === 'undefined') return;
      const token = localStorage.getItem('ultrabot_token');
      const username = localStorage.getItem('ultrabot_username');
      // Purge legacy artifacts from removed demo mode / plaintext-credential
      // storage so old browser sessions can't masquerade as authenticated.
      if (token === 'demo-token' || username === 'demo') {
        localStorage.removeItem('ultrabot_token');
        localStorage.removeItem('ultrabot_username');
        set({ auth: { ...get().auth, token: null, username: null, isAuthenticated: false } });
        return;
      }
      localStorage.removeItem('ultrabot_broker_creds'); // legacy plaintext secrets — wipe
      set({ auth: { ...get().auth, token, username, isAuthenticated: !!token } });
    },
  },

  engine: {
    status: 'stopped',
    mode: 'paper',
    regime: 'sideways',
    vix: 0,
    niftyValue: 0,
    niftyChange: 0,
    marketCloseSeconds: 0,
    activeBroker: null,
    startedAt: null,
    errorMessage: null,
    lastHeartbeat: null,

    hydrateEngine() {
      if (typeof window === 'undefined') return;
      const saved = localStorage.getItem('ultrabot_engine_state');
      const savedMode = (localStorage.getItem('ultrabot_engine_mode') as EngineMode) || 'paper';
      const savedBroker = localStorage.getItem('ultrabot_active_broker');
      const savedStartedAt = localStorage.getItem('ultrabot_started_at');

      if (saved && (saved === 'running' || saved === 'stopped' || saved === 'paused')) {
        set({
          engine: {
            ...get().engine,
            status: saved as EngineStatus,
            mode: savedMode,
            activeBroker: saved === 'running' ? (savedBroker || 'paper') : null,
            startedAt: saved === 'running' && savedStartedAt ? Number(savedStartedAt) : null,
          },
        });
      }
    },

    scanTelemetry: null,

    setEngineStatus(status) {
      if (typeof window !== 'undefined') {
        localStorage.setItem('ultrabot_engine_state', status);
      }
      set({ engine: { ...get().engine, status } });
    },
    setMode(mode) { set({ engine: { ...get().engine, mode } }); },
    setRegime(regime) { set({ engine: { ...get().engine, regime } }); },
    setVix(vix) { set({ engine: { ...get().engine, vix } }); },
    setNifty(value, change) { set({ engine: { ...get().engine, niftyValue: value, niftyChange: change } }); },
    setMarketCloseSeconds(seconds) { set({ engine: { ...get().engine, marketCloseSeconds: seconds } }); },
    setActiveBroker(broker) {
      if (typeof window !== 'undefined' && broker) {
        localStorage.setItem('ultrabot_active_broker', broker);
      }
      set({ engine: { ...get().engine, activeBroker: broker } });
    },
    setErrorMessage(msg) { set({ engine: { ...get().engine, errorMessage: msg } }); },
    setScanTelemetry(telemetry) { set({ engine: { ...get().engine, scanTelemetry: telemetry } }); },
    addTelemetryEvents(events) {
      if (!events || events.length === 0) return;
      const current = get().engine.scanTelemetry || { recent_events: [], rejections_by_gate: {}, signals_generated: 0, signals_passed: 0, signals_rejected: 0 };
      const recent = [...events.slice().reverse(), ...(current.recent_events || [])].slice(0, 50);
      let passedDelta = 0;
      let rejectedDelta = 0;
      const gateMap = { ...(current.rejections_by_gate || {}) };
      for (const ev of events) {
        const isPassed = ev.status === 'PASSED';
        const isRejected = ev.status === 'REJECTED';
        if (isPassed) passedDelta++;
        if (isRejected) {
          rejectedDelta++;
          if (ev.gate && ev.gate !== '—') {
            gateMap[ev.gate] = (gateMap[ev.gate] || 0) + 1;
          }
        }
      }
      set({
        engine: {
          ...get().engine,
          scanTelemetry: {
            ...current,
            signals_generated: (current.signals_generated || 0) + passedDelta + rejectedDelta,
            signals_passed: (current.signals_passed || 0) + passedDelta,
            signals_rejected: (current.signals_rejected || 0) + rejectedDelta,
            rejections_by_gate: gateMap,
            recent_events: recent,
          }
        }
      });
    },
    addTelemetryEvent(event) {
      get().engine.addTelemetryEvents([event]);
    },

    start(mode, brokerId) {
      const now = Date.now();
      if (typeof window !== 'undefined') {
        localStorage.setItem('ultrabot_engine_state', 'running');
        localStorage.setItem('ultrabot_engine_mode', mode);
        localStorage.setItem('ultrabot_active_broker', brokerId);
        localStorage.setItem('ultrabot_started_at', String(now));
      }
      set({
        engine: {
          ...get().engine,
          status: 'running',
          mode,
          activeBroker: brokerId,
          startedAt: now,
          errorMessage: null,
          lastHeartbeat: now,
        },
      });
    },

    stop() {
      if (typeof window !== 'undefined') {
        localStorage.setItem('ultrabot_engine_state', 'stopped');
        localStorage.removeItem('ultrabot_active_broker');
        localStorage.removeItem('ultrabot_started_at');
      }
      set({
        engine: {
          ...get().engine,
          status: 'stopped',
          activeBroker: null,
          startedAt: null,
          errorMessage: null,
          lastHeartbeat: null,
        },
      });
    },

    heartbeat() {
      set({ engine: { ...get().engine, lastHeartbeat: Date.now() } });
    },
  },

  realtime: {
    livePrices: {},
    opportunities: [],

    updatePrice(price) {
      set({
        realtime: {
          ...get().realtime,
          livePrices: { ...get().realtime.livePrices, [price.symbol]: price },
        },
      });
    },
    updatePrices(prices) {
      const updated = { ...get().realtime.livePrices };
      for (const p of prices) { updated[p.symbol] = p; }
      set({ realtime: { ...get().realtime, livePrices: updated } });
    },
    addOpportunity(opportunity) {
      const normalized = normalizeOpportunity(opportunity);
      if (!normalized.id) return;
      const current = get().realtime.opportunities;
      const filtered = current.filter((o) => o.id !== normalized.id);
      set({
        realtime: {
          ...get().realtime,
          opportunities: [normalized, ...filtered],
        },
      });
    },
    removeOpportunity(id) {
      if (!id) return;
      set({
        realtime: {
          ...get().realtime,
          opportunities: get().realtime.opportunities.filter((o) => o.id !== id),
        },
      });
    },
    setOpportunities(opportunities) {
      const normalized = (opportunities || []).map(normalizeOpportunity).filter((o) => !!o.id);
      set({ realtime: { ...get().realtime, opportunities: normalized } });
    },
    clearOpportunities() {
      set({ realtime: { ...get().realtime, opportunities: [] } });
    },
  },

  sidebar: {
    collapsed: false,
    mobileOpen: false,

    toggle() { set({ sidebar: { ...get().sidebar, collapsed: !get().sidebar.collapsed } }); },
    setCollapsed(collapsed) { set({ sidebar: { ...get().sidebar, collapsed } }); },
    setMobileOpen(mobileOpen) { set({ sidebar: { ...get().sidebar, mobileOpen } }); },
  },

  brokers: {
    credentials: {},
    backendStatus: {},

    saveBrokerCredentials(brokerId, fields) {
      // In-memory form draft only — the authoritative (encrypted) copy lives
      // in the backend DB. Never persist secrets to localStorage.
      const updated = { ...get().brokers.credentials, [brokerId]: fields };
      set({ brokers: { ...get().brokers, credentials: updated } });
    },

    clearBrokerCredentials(brokerId) {
      const updated = { ...get().brokers.credentials };
      delete updated[brokerId];
      set({ brokers: { ...get().brokers, credentials: updated } });
    },

    setBackendBrokerStatus(statuses) {
      const map: Record<string, BackendBrokerStatus> = {};
      for (const s of statuses || []) {
        map[s.broker] = s;
      }
      set({ brokers: { ...get().brokers, backendStatus: map } });
    },

    isBrokerConfigured(brokerId) {
      // Backend is the source of truth: the broker is configured when the
      // engine DB holds (encrypted) credentials for it.
      const backend = get().brokers.backendStatus[brokerId];
      if (backend) return backend.hasCredentials;
      // Fall back to the in-memory draft (current editing session only).
      const creds = get().brokers.credentials[brokerId];
      if (!creds) return false;
      return Object.values(creds).some((v) => v.trim() !== '');
    },
  },
}));

// ─────────────────────────────────────────────
// Selectors — simple, stable references
// ─────────────────────────────────────────────

export const useAuth = () => useStore((s) => s.auth);
export const useEngine = () => useStore((s) => s.engine);
export const useRealtime = () => useStore((s) => s.realtime);
export const useSidebar = () => useStore((s) => s.sidebar);
export const useBrokers = () => useStore((s) => s.brokers);