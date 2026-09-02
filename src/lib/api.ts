import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

// ─────────────────────────────────────────────
// Axios instance
// ─────────────────────────────────────────────

const API_BASE_URL =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL || '')
    : '';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ─────────────────────────────────────────────
// Request interceptor — attach JWT & dynamic timeout
// ─────────────────────────────────────────────

api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    if (typeof window !== 'undefined') {
      const token = localStorage.getItem('ultrabot_token');
      if (token && config.headers) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// ─────────────────────────────────────────────
// Response interceptor — handle 401
// ─────────────────────────────────────────────

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('ultrabot_token');
        localStorage.removeItem('ultrabot_username');
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  },
);

// ─────────────────────────────────────────────
// Typed response wrappers
// ─────────────────────────────────────────────

type ApiResponse<T = unknown> = Promise<T>;

// ─────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────

export async function login(username: string, password: string): ApiResponse<{ access_token: string; token_type: string; expires_in_hours: number }> {
  const { data } = await api.post(
    '/api/auth/login',
    new URLSearchParams({ username, password }).toString(),
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
  );
  return data;
}

// ─────────────────────────────────────────────
// Dashboard
// ─────────────────────────────────────────────

export interface MarketDataResponse {
  nifty: number;
  nifty_change: number;
  vix: number;
  source: string;
}

export interface NewsItemResponse {
  symbol: string;
  symbols?: string[];
  price?: number;
  changePct?: number;
  headline: string;
  summary?: string;
  source: string;
  category?: string;
  sentiment?: 'BUY' | 'SELL' | 'NEUTRAL';
  impactLevel?: 'HIGH' | 'MEDIUM' | 'LOW';
  tradeAction?: 'BUY' | 'SELL' | 'HOLD';
  confidence?: number;
  timeAgo?: string;
  providerCode?: string;
  publishedTimestamp?: number;
  publishedAt?: string;
  url?: string;
}


export interface DashboardData {
  regime?: string;
  regimeConfidence?: number;
  niftyPrice?: number;
  niftyChange?: number;
  vix?: number;
  dailyPnl?: number;
  dailyPnlPct?: number;
  winRate?: number;
  openPositionsCount?: number;
  closedTradesCount?: number;
  signalsGenerated?: number;
  signalsConfirmed?: number;
  signalsSkipped?: number;
  activeStrategies?: string[];
  recentTrades?: any[];
  positions?: any[];
  [key: string]: unknown;
}

export async function getDashboard(): ApiResponse<DashboardData> {
  const { data } = await api.get<DashboardData>('/api/dashboard');
  return data;
}

export async function getMarketData(): ApiResponse<MarketDataResponse> {
  const { data } = await api.get<MarketDataResponse>('/api/dashboard/market-data');
  return data;
}

// ─────────────────────────────────────────────
// Typed Data Interfaces
// ─────────────────────────────────────────────

export interface ApiOpportunityItem {
  id: string;
  symbol: string;
  direction: 'BUY' | 'SELL' | 'LONG' | 'SHORT';
  entry_price: number;
  target_price: number;
  stop_loss: number;
  risk_reward?: number;
  confidence: number;
  strategy: string;
  timestamp?: string;
  [key: string]: unknown;
}

export interface ApiTradeItem {
  id: string;
  symbol: string;
  direction: string;
  quantity: number;
  entry_price: number;
  exit_price?: number;
  pnl?: number;
  net_pnl?: number;
  status: string;
  timestamp?: string;
  [key: string]: unknown;
}

export interface ApiPositionItem {
  id: string;
  symbol: string;
  direction: 'BUY' | 'SELL' | 'LONG' | 'SHORT';
  quantity: number;
  entry_price: number;
  current_price?: number;
  pnl?: number;
  pnl_pct?: number;
  status: string;
  [key: string]: unknown;
}

export interface ApiStrategyItem {
  name: string;
  is_enabled: boolean;
  regimes?: string[];
  description?: string;
  [key: string]: unknown;
}

export interface ApiBrokerStatus {
  connected: boolean;
  broker_name?: string;
  mode?: string;
  [key: string]: unknown;
}

// ─────────────────────────────────────────────
// Opportunities
// ─────────────────────────────────────────────

export async function getOpportunities(): ApiResponse<ApiOpportunityItem[]> {
  const { data } = await api.get<ApiOpportunityItem[]>('/api/opportunities');
  return data;
}

export async function getInvalidatedOpportunities(): ApiResponse<ApiOpportunityItem[]> {
  const { data } = await api.get<ApiOpportunityItem[]>('/api/opportunities/invalidated');
  return data;
}

export async function confirmOpportunity(id: string, segment: string = "EQ"): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post(`/api/opportunities/${id}/confirm`, { segment });
  return data;
}

export async function skipOpportunity(id: string): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post(`/api/opportunities/${id}/skip`);
  return data;
}

// ─────────────────────────────────────────────
// Trades
// ─────────────────────────────────────────────

export async function getTrades(params?: { page?: number; limit?: number; status?: string }): ApiResponse<ApiTradeItem[] | { trades: ApiTradeItem[]; total?: number }> {
  const { data } = await api.get('/api/trades', { params });
  return data;
}

// ─────────────────────────────────────────────
// Positions
// ─────────────────────────────────────────────

export async function getPositions(): ApiResponse<ApiPositionItem[]> {
  const { data } = await api.get<ApiPositionItem[]>('/api/positions');
  return data;
}

export async function closePosition(id: string, payload?: { exit_price?: number; exit_reason?: string; notes?: string }): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post(`/api/positions/${id}/close`, payload || {});
  return data;
}

export async function modifyPositionStopLoss(id: string, newSl: number): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post(`/api/positions/${id}/modify-sl`, { new_sl: newSl });
  return data;
}

export async function modifyPositionTarget(id: string, newTarget: number): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post(`/api/positions/${id}/modify-target`, { new_target: newTarget });
  return data;
}

// ─────────────────────────────────────────────
// Strategies
// ─────────────────────────────────────────────

export async function getStrategies(): ApiResponse<ApiStrategyItem[]> {
  const { data } = await api.get<ApiStrategyItem[]>('/api/strategies');
  return data;
}

export async function toggleStrategy(name: string, isEnabled: boolean): ApiResponse<Record<string, unknown>> {
  const { data } = await api.put(`/api/strategies/${name}/toggle`, { is_enabled: isEnabled });
  return data;
}

// ─────────────────────────────────────────────
// Watchlist
// ─────────────────────────────────────────────

export async function getWatchlist(): ApiResponse<string[] | { watchlist: string[] }> {
  const { data } = await api.get('/api/watchlist');
  return data;
}

// ─────────────────────────────────────────────
// Brokers
// ─────────────────────────────────────────────

export async function getBrokerStatus(): ApiResponse<Record<string, ApiBrokerStatus>> {
  const { data } = await api.get('/api/brokers');
  return data;
}

export interface BrokerConnectionResponse {
  broker?: string;
  connected: boolean;
  message: string;
  [key: string]: unknown;
}

export interface BrokerSaveCredentialsResponse {
  success: boolean;
  message: string;
  broker?: string;
  [key: string]: unknown;
}

export async function saveAngelOneCredentials(creds: {
  client_id?: string;
  client_code?: string;
  client_secret?: string;
  api_key?: string;
  pin?: string;
  totp_secret?: string;
  account_type?: string;
}): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.post('/api/brokers/angel-one/credentials', creds);
  return data;
}

export async function saveShoonyaCredentials(creds: {
  client_id?: string;
  client_secret?: string;
  user_id?: string;
  password?: string;
  vendor_code?: string;
  app_key?: string;
  totp_secret?: string;
  account_type?: string;
}): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.post('/api/brokers/shoonya/credentials', creds);
  return data;
}

export async function testAngelOneConnection(creds?: Record<string, string>): ApiResponse<BrokerConnectionResponse> {
  try {
    const { data } = await api.post('/api/brokers/angel-one/test', creds || {});
    return data;
  } catch (err: any) {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Angel One connection test failed';
    return { broker: 'angel_one', connected: false, message: msg };
  }
}

export async function testShoonyaConnection(creds?: Record<string, string>): ApiResponse<BrokerConnectionResponse> {
  try {
    const { data } = await api.post('/api/brokers/shoonya/test', creds || {});
    return data;
  } catch (err: any) {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Shoonya connection test failed';
    return { broker: 'shoonya', connected: false, message: msg };
  }
}

export async function saveDhanCredentials(creds: {
  client_id: string;
  access_token?: string;
  pin?: string;
  totp_secret?: string;
  account_type?: string;
}): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.post('/api/brokers/dhan/credentials', creds);
  return data;
}

export async function testDhanConnection(creds?: Record<string, string>): ApiResponse<BrokerConnectionResponse> {
  try {
    const { data } = await api.post('/api/brokers/dhan/test', creds || {});
    return data;
  } catch (err: any) {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Dhan connection test failed';
    return { broker: 'dhan', connected: false, message: msg };
  }
}

export async function saveFyersCredentials(creds: {
  app_id: string;
  access_token?: string;
  secret_key?: string;
  redirect_uri?: string;
  pin?: string;
  account_type?: string;
}): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.post('/api/brokers/fyers/credentials', creds);
  return data;
}

export async function testFyersConnection(creds?: Record<string, string>): ApiResponse<BrokerConnectionResponse> {
  try {
    const { data } = await api.post('/api/brokers/fyers/test', creds || {});
    return data;
  } catch (err: any) {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Fyers connection test failed';
    return { broker: 'fyers', connected: false, message: msg };
  }
}

/** Fetch the Fyers login URL. Caller should open it (new tab) so the user
 * can complete login + 2FA — required daily, cannot be automated. */
export async function getFyersAuthUrl(): ApiResponse<{ auth_url?: string }> {
  const { data } = await api.get('/api/brokers/fyers/authorize');
  return data;
}

/** Token expiry/re-auth status for the Settings "Connected — expires in Xh" display. */
export async function getFyersTokenStatus(): ApiResponse<{
  connected: boolean;
  needs_reauth: boolean;
  seconds_until_expiry: number;
}> {
  try {
    const { data } = await api.get('/api/brokers/fyers/token-status');
    return data;
  } catch (err: any) {
    return { connected: false, needs_reauth: true, seconds_until_expiry: 0 };
  }
}

export async function saveZerodhaCredentials(creds: {
  api_key?: string;
  api_secret?: string;
  access_token?: string;
  user_id?: string;
  account_type?: string;
}): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.post('/api/brokers/zerodha/credentials', creds);
  return data;
}

export async function testZerodhaConnection(): ApiResponse<BrokerConnectionResponse> {
  try {
    const { data } = await api.post('/api/brokers/zerodha/test', {});
    return data;
  } catch (err: any) {
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Zerodha connection test failed';
    return { broker: 'zerodha', connected: false, message: msg };
  }
}

/** Remove a broker's stored credentials from the backend (DB row delete). */
export async function deleteBrokerCredentials(brokerId: string): ApiResponse<BrokerSaveCredentialsResponse> {
  const { data } = await api.delete(`/api/brokers/${brokerId}/credentials`);
  return data;
}

// ─────────────────────────────────────────────
// Daily re-login / session tokens
// ─────────────────────────────────────────────

export interface BrokerReloginResponse {
  success: boolean;
  broker?: string;
  message: string;
  relogin_method?: 'totp' | 'browser' | 'none';
  requires_browser?: boolean;
  auth_url?: string;
  expires_at?: number;
  seconds_until_expiry?: number;
  applied_to_running_engine?: boolean;
  [key: string]: unknown;
}

/** One-click daily re-login (TOTP brokers). For Fyers the response carries
 * the browser login URL instead (SEBI 2FA — no silent path by design). */
export async function reloginBroker(brokerId: string): ApiResponse<BrokerReloginResponse> {
  const { data } = await api.post(`/api/brokers/${brokerId}/relogin`);
  return data;
}

export interface BrokerTokenStatus {
  broker: string;
  has_credentials: boolean;
  auth_status: string;
  token_state: 'valid' | 'expired' | 'unknown';
  token_expires_at: number | null;
  seconds_until_expiry: number | null;
  last_relogin_at: number | null;
  last_auth: string | null;
  last_error: string | null;
  can_auto_relogin: boolean;
  relogin_method: 'totp' | 'browser' | 'none';
}

/** Session-token status for every configured broker (valid/expired +
 * countdown + re-login capability) — powers the Settings session panel. */
export async function getBrokerTokenStatus(): ApiResponse<{ brokers: BrokerTokenStatus[] }> {
  const { data } = await api.get('/api/brokers/token-status');
  return data;
}

export interface BrokerSessionPreflight {
  ok: boolean;
  level: 'ok' | 'warning' | 'critical' | 'skipped';
  broker: string;
  message: string;
  token_state: 'valid' | 'expired' | 'unknown' | 'not_applicable';
  seconds_until_expiry: number | null;
  relogin_method: 'totp' | 'browser' | 'none';
}

/** Pre-flight daily-session check for a broker (defaults to the running
 * engine's active broker) — single source of truth for the dashboard
 * re-login warning banner. */
export async function getSessionPreflight(broker?: string): ApiResponse<BrokerSessionPreflight> {
  const { data } = await api.get('/api/brokers/preflight', {
    params: broker ? { broker } : undefined,
  });
  return data;
}

// ─────────────────────────────────────────────
// Risk
// ─────────────────────────────────────────────

export interface RiskStatusData {
  date: string;
  can_take_new_trades: boolean;
  block_reason?: string | null;
  net_pnl: number;
  daily_loss_pct?: number;
  max_drawdown_pct?: number;
  open_positions: number;
  consecutive_losses: number;
  total_trades: number;
  wins?: number;
  losses?: number;
  win_rate?: number;
  [key: string]: unknown;
}

export interface RiskGateItem {
  gate_name: string;
  status: 'passed' | 'failed' | 'warning' | 'bypassed';
  description?: string;
  value?: unknown;
  threshold?: unknown;
  [key: string]: unknown;
}

export interface RiskGatesData {
  gates: Record<string, any> | RiskGateItem[];
  all_passed: boolean;
  [key: string]: unknown;
}

export async function getRiskStatus(): ApiResponse<RiskStatusData> {
  const { data } = await api.get<RiskStatusData>('/api/risk/status');
  return data;
}

export async function getRiskGates(): ApiResponse<RiskGatesData> {
  const { data } = await api.get<RiskGatesData>('/api/risk/gates');
  return data;
}

export async function updateRiskLimits(limits: Record<string, number | string | boolean>): ApiResponse<Record<string, unknown>> {
  const { data } = await api.put('/api/risk/limits', limits);
  return data;
}

export async function updateSettingsFull(payload: Record<string, unknown>): ApiResponse<Record<string, unknown>> {
  const { data } = await api.put('/api/settings', payload);
  return data;
}

export async function getNotificationSettings(): ApiResponse<Record<string, any>> {
  const { data } = await api.get('/api/notifications/settings');
  return data;
}

export async function updateNotificationSettings(payload: Record<string, any>): ApiResponse<Record<string, unknown>> {
  const { data } = await api.put('/api/notifications/settings', payload);
  return data;
}

export async function testTelegramNotification(payload?: {
  telegram_bot_token?: string;
  telegram_chat_id?: string;
}): ApiResponse<{ message: string; telegram_message_id?: number }> {
  const { data } = await api.post('/api/notifications/test', payload || {});
  return data;
}

export async function testEventNotification(eventType: string): ApiResponse<{ message: string; event_type: string; sent_to_telegram: boolean }> {
  const { data } = await api.post('/api/notifications/test-event', { event_type: eventType });
  return data;
}

// ─────────────────────────────────────────────
// Errors
// ─────────────────────────────────────────────

export interface ErrorLogItem {
  id: string;
  error_code: string;
  error_type: string;
  severity: string;
  message: string;
  is_resolved: boolean;
  resolution_note?: string;
  created_at: string;
  updated_at?: string;
  resolved_at?: string | null;
  what_happened?: string;
  why_happened?: string | null;
  how_to_fix?: string | null;
  auto_recovery_attempted?: boolean;
  auto_recovery_result?: string | null;
  context?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ErrorLogResponse {
  errors: ErrorLogItem[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

export async function getErrors(params?: { page?: number; limit?: number }): ApiResponse<ErrorLogItem[] | ErrorLogResponse> {
  const { data } = await api.get('/api/errors', { params });
  return data;
}

/** Mark an error as resolved in the engine's error_logs table. */
export async function resolveError(errorId: string, resolutionNote = ''): ApiResponse<{ message: string; id: string; resolved_at: string }> {
  const { data } = await api.put(`/api/errors/${errorId}/resolve`, { resolution_note: resolutionNote });
  return data;
}

// ─────────────────────────────────────────────
// Engine
// ─────────────────────────────────────────────

export interface ScanTelemetryEvent {
  time: string;
  symbol: string;
  strategy: string;
  status: 'PASSED' | 'REJECTED' | 'NO_SETUP';
  direction?: string;
  price?: number;
  confidence?: number;
  gate?: string | null;
  reason?: string;
}

export interface ScanTelemetryData {
  total_scans: number;
  symbols_scanned: number;
  signals_generated: number;
  signals_passed: number;
  signals_rejected: number;
  rejections_by_gate: Record<string, number>;
  rejections_by_strategy: Record<string, number>;
  active_strategies: string[];
  broker: string;
  mode: string;
  state: string;
  scanning_status?: string;
  idle_reason?: string;
  recent_events: ScanTelemetryEvent[];
}

export interface EngineStatusData {
  state: string;
  mode?: string;
  broker?: string;
  uptime?: number;
  active_strategies?: string[];
  scan_count?: number;
  signals_generated?: number;
  trades_executed?: number;
  errors_count?: number;
  [key: string]: unknown;
}

export async function getEngineStatus(): ApiResponse<EngineStatusData> {
  const { data } = await api.get<EngineStatusData>('/api/engine/status');
  return data;
}

export async function getEngineScanTelemetry(): ApiResponse<ScanTelemetryData> {
  const { data } = await api.get<ScanTelemetryData>('/api/engine/scan-telemetry');
  return data;
}

export interface EngineStartParams {
  mode: string;
  broker: string;
  strategies?: string[];
  initial_capital?: number;
}

export async function startEngine(params: EngineStartParams): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post('/api/engine/start', params);
  return data;
}

export async function pauseEngine(): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post('/api/engine/pause');
  return data;
}

export async function resumeEngine(): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post('/api/engine/resume');
  return data;
}

export async function stopEngine(): ApiResponse<Record<string, unknown>> {
  const { data } = await api.post('/api/engine/stop');
  return data;
}

// ─────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────

export interface SettingsData {
  app?: Record<string, unknown>;
  trading?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  capital?: Record<string, unknown>;
  fees?: Record<string, unknown>;
  notifications?: Record<string, unknown>;
  brokers?: Record<string, unknown>;
  [key: string]: unknown;
}

export async function getSettings(): ApiResponse<SettingsData> {
  const { data } = await api.get<SettingsData>('/api/settings');
  return data;
}

export async function updateSettings(settings: Record<string, unknown>): ApiResponse<Record<string, unknown>> {
  const { data } = await api.put('/api/settings', settings);
  return data;
}

// ─────────────────────────────────────────────
// Backtest
// ─────────────────────────────────────────────

export interface BacktestRunItem {
  id: string;
  strategy: string;
  symbol?: string;
  start_date: string;
  end_date: string;
  timeframe: string;
  initial_capital: number;
  status: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  parameters?: Record<string, unknown>;
  results?: Record<string, unknown>;
  equity_curve?: any[];
  created_at: string;
  [key: string]: unknown;
}

export async function getBacktestHistory(params?: { strategy?: string; limit?: number; offset?: number }): ApiResponse<BacktestRunItem[] | { items: BacktestRunItem[]; runs?: BacktestRunItem[]; total?: number }> {
  const { data } = await api.get('/api/backtest/history', { params });
  return data;
}

export async function getBacktestStatus(runId: string): ApiResponse<Record<string, unknown>> {
  const { data } = await api.get(`/api/backtest/status/${runId}`);
  return data;
}

export async function getBacktestResult(runId: string): ApiResponse<Record<string, unknown>> {
  const { data } = await api.get(`/api/backtest/results/${runId}`);
  return data;
}

export async function runBacktest(params: Record<string, unknown>): ApiResponse<Record<string, unknown>> {
  // Real backend call only — errors propagate so the UI can show an honest
  // failure instead of a fabricated "completed" result.
  const { data } = await api.post('/api/backtest', params);
  return data;
}

// ─────────────────────────────────────────────
// Scanner
// ─────────────────────────────────────────────

export interface KronosHotStockItem {
  rank: number;
  symbol: string;
  price: number;
  changePct: number;
  volume: string;
  hotness: number;
  reason: string;
  [key: string]: unknown;
}

export async function getKronosHotlist(): ApiResponse<KronosHotStockItem[] | { hotlist: KronosHotStockItem[] }> {
  const { data } = await api.get('/api/scanner/kronos');
  return data;
}

export default api;
