'use client';

import { useState, useCallback, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { toast } from 'sonner';
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Save,
  TestTube,
  Trash2,
  Shield,
  ShieldCheck,
  Zap,
  Info,
  LogIn,
  Clock,
  KeyRound,
} from 'lucide-react';
import { useStore, BROKER_LIST, BROKER_FIELDS, type BrokerCredentialFields } from '@/lib/store';
import { theme } from '@/styles/theme';
import {
  saveAngelOneCredentials,
  saveShoonyaCredentials,
  saveDhanCredentials,
  saveFyersCredentials,
  saveZerodhaCredentials,
  testAngelOneConnection,
  testShoonyaConnection,
  testDhanConnection,
  testFyersConnection,
  testZerodhaConnection,
  deleteBrokerCredentials,
  getBrokerStatus,
  reloginBroker,
  getBrokerTokenStatus,
  type BrokerTokenStatus,
} from '@/lib/api';

/* ─────────────────────────────────────────────
   Brokers whose API sessions expire daily and
   therefore show the Re-login / session panel.
   ───────────────────────────────────────────── */
const DAILY_SESSION_BROKERS = new Set(['angel_one', 'shoonya', 'dhan', 'fyers']);

/* ─────────────────────────────────────────────
   Stable constants (avoid new refs on every render)
   ───────────────────────────────────────────── */

const EMPTY_CREDENTIALS: BrokerCredentialFields = {};

/* ─────────────────────────────────────────────
   Pure helper — checks if a broker's credentials
   are filled directly from the state snapshot.
   ───────────────────────────────────────────── */

function isBrokerCredsComplete(creds: BrokerCredentialFields | undefined, brokerId: string): boolean {
  if (!creds) return false;
  const fields = BROKER_FIELDS[brokerId];
  if (!fields || fields.length === 0) return true;
  return fields.every((f) => creds[f.key]?.trim() !== '');
}

/* ─────────────────────────────────────────────
   Backend status hydration — GET /api/brokers is
   the source of truth for "Configured" badges.
   ───────────────────────────────────────────── */

// Shared with StartEngineDialog (which also needs the backend source of
// truth for "Configured" badges — credentials live encrypted in the
// backend DB and are never sent back to the browser, so the in-memory
// form draft is empty after any full page reload, e.g. the Fyers OAuth
// callback redirect).
export function useRefreshBrokerStatus() {
  const setBackendBrokerStatus = useStore((s) => s.brokers.setBackendBrokerStatus);
  return useCallback(async () => {
    try {
      const res = await getBrokerStatus();
      const raw = (res as any)?.brokers;
      if (Array.isArray(raw)) {
        setBackendBrokerStatus(
          raw.map((b: any) => ({
            broker: String(b.broker ?? b.broker_name ?? ''),
            isActive: Boolean(b.is_active),
            hasCredentials: b.has_credentials !== undefined ? Boolean(b.has_credentials) : true,
            authStatus: b.auth_status ?? null,
            lastAuth: b.last_auth ?? null,
            accountType: b.account_type,
          })),
        );
      }
    } catch {
      // Backend unreachable — the form still works; badges fall back to the
      // in-memory draft until the next successful poll.
    }
  }, [setBackendBrokerStatus]);
}

/* ─────────────────────────────────────────────
   Shared formatting helpers for session status
   ───────────────────────────────────────────── */

function formatExpiry(seconds: number) {
  if (seconds <= 0) return 'Expired';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m remaining`;
}

function formatTimeAgo(isoOrNull: string | number | null): string {
  if (isoOrNull == null) return '—';
  const ts = typeof isoOrNull === 'number' ? isoOrNull : Date.parse(isoOrNull);
  if (Number.isNaN(ts)) return '—';
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

/* ─────────────────────────────────────────────
   Broker Card Component
   ───────────────────────────────────────────── */

function BrokerCredentialCard({
  brokerId,
  isActiveEngineBroker = false,
}: {
  brokerId: string;
  isActiveEngineBroker?: boolean;
}) {
  const credentials = useStore((s) => s.brokers.credentials[brokerId] ?? EMPTY_CREDENTIALS);
  const isConfigured = useStore((s) => isBrokerCredsComplete(s.brokers.credentials[brokerId], brokerId) || s.brokers.isBrokerConfigured(brokerId));
  const saveCreds = useStore((s) => s.brokers.saveBrokerCredentials);
  const clearCreds = useStore((s) => s.brokers.clearBrokerCredentials);
  const refreshBackendStatus = useRefreshBrokerStatus();

  const brokerMeta = BROKER_LIST.find((b) => b.id === brokerId);
  const fields = BROKER_FIELDS[brokerId] || [];
  const needsCreds = brokerMeta?.needsCredentials ?? true;

  const [localCreds, setLocalCreds] = useState<BrokerCredentialFields>(credentials);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const hasChanges = JSON.stringify(localCreds) !== JSON.stringify(credentials);
  // True when the local form draft has ANY non-empty value. When the backend
  // reports the broker as configured but this is false, we're in the
  // post-page-reload state (secrets never return to the browser) — the
  // fields look blank even though a valid encrypted copy exists server-side.
  const hasDraftValues = Object.values(localCreds).some((v) => (v ?? '').trim() !== '');

  // Sync local credentials when the store completes hydration
  useEffect(() => {
    if (credentials && Object.keys(credentials).length > 0) {
      setLocalCreds(credentials);
    }
  }, [credentials]);

  const [tokenStatus, setTokenStatus] = useState<BrokerTokenStatus | null>(null);
  const [reloggingIn, setReloggingIn] = useState(false);
  const searchParams = useSearchParams();

  const showSessionPanel = DAILY_SESSION_BROKERS.has(brokerId);

  const refreshTokenStatus = useCallback(async () => {
    if (!DAILY_SESSION_BROKERS.has(brokerId)) return;
    try {
      const res = await getBrokerTokenStatus();
      const mine = (res as any)?.brokers?.find((b: BrokerTokenStatus) => b.broker === brokerId) || null;
      setTokenStatus(mine);
    } catch {
      // Backend unreachable — keep whatever we had; the panel degrades to
      // "session unknown" rather than blocking the form.
    }
  }, [brokerId]);

  useEffect(() => {
    refreshTokenStatus();
  }, [refreshTokenStatus]);

  const handleSave = useCallback(async () => {
    // Credentials live ONLY in the backend DB (encrypted at rest).
    // The local draft is kept in memory so the form retains values this session.
    setSaving(true);
    try {
      if (brokerId === 'angel_one') {
        await saveAngelOneCredentials({
          client_id: localCreds.clientCode || '',
          client_code: localCreds.clientCode || '',
          api_key: localCreds.apiKey || '',
          pin: localCreds.pin || '',
          totp_secret: localCreds.totpSecret || '',
          account_type: 'live',
        });
      } else if (brokerId === 'shoonya') {
        await saveShoonyaCredentials({
          client_id: localCreds.userId || '',
          user_id: localCreds.userId || '',
          client_secret: localCreds.password || '',
          password: localCreds.password || '',
          vendor_code: localCreds.vendorCode || '',
          app_key: localCreds.appKey || '',
          totp_secret: localCreds.totpSecret || '',
          account_type: 'live',
        });
      } else if (brokerId === 'dhan') {
        await saveDhanCredentials({
          client_id: localCreds.clientId || '',
          access_token: localCreds.accessToken || '',
          pin: localCreds.pin || '',
          totp_secret: localCreds.totpSecret || '',
          account_type: 'live',
        });
      } else if (brokerId === 'fyers') {
        await saveFyersCredentials({
          app_id: localCreds.appId || '',
          access_token: localCreds.accessToken || '',
          secret_key: localCreds.secretKey || '',
          redirect_uri: localCreds.redirectUri || '',
          pin: localCreds.pin || '',
          account_type: 'live',
        });
      } else if (brokerId === 'zerodha') {
        await saveZerodhaCredentials({
          api_key: localCreds.apiKey || '',
          api_secret: localCreds.apiSecret || '',
          access_token: localCreds.accessToken || '',
          user_id: localCreds.userId || '',
          account_type: 'live',
        });
      }
      // Success: keep the draft in memory and refresh backend-truth badge.
      saveCreds(brokerId, localCreds);
      await refreshBackendStatus();
      // Refresh the daily-session panel too — otherwise the Login button
      // keeps showing the pre-save "Not configured" state until page reload.
      await refreshTokenStatus();
      toast.success(`${brokerMeta?.name || brokerId} credentials saved to backend (encrypted)`);
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || err;
      toast.error(`Failed to save to backend: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
    } finally {
      setSaving(false);
    }
  }, [brokerId, localCreds, saveCreds, brokerMeta?.name, refreshBackendStatus, refreshTokenStatus]);

  const handleClear = useCallback(async () => {
    // Removes the stored credentials from the backend DB (not just the form).
    try {
      await deleteBrokerCredentials(brokerId);
      await refreshBackendStatus();
      // Sync the session panel: the Login button must disable immediately
      // after credentials are deleted (no stale "has_credentials" state).
      await refreshTokenStatus();
      toast.info(`${brokerMeta?.name || brokerId} credentials deleted from backend`);
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || err;
      toast.error(`Failed to delete credentials: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
      return; // keep the form as-is when the backend call fails
    }
    clearCreds(brokerId);
    setLocalCreds({});
  }, [brokerId, clearCreds, brokerMeta?.name, refreshBackendStatus, refreshTokenStatus]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      if (Object.keys(localCreds).length > 0 && hasChanges) {
        await handleSave();
      }
      let res: any;
      if (brokerId === 'angel_one') res = await testAngelOneConnection();
      else if (brokerId === 'shoonya') res = await testShoonyaConnection();
      else if (brokerId === 'dhan') res = await testDhanConnection();
      else if (brokerId === 'fyers') res = await testFyersConnection();
      else if (brokerId === 'zerodha') res = await testZerodhaConnection();

      if (res?.connected) {
        toast.success(`${brokerMeta?.name || brokerId}: Connection verified successfully!`);
      } else {
        toast.error(`${brokerMeta?.name || brokerId}: ${res?.message || 'Connection test failed'}`);
      }
    } catch (err: any) {
      toast.error(`${brokerMeta?.name || brokerId} test failed: ${err.message || err}`);
    } finally {
      setTesting(false);
    }
  }, [brokerId, brokerMeta?.name, localCreds, hasChanges, handleSave]);

  const handleRelogin = useCallback(async () => {
    if (reloggingIn) return;
    // Persist any unsaved edits first — re-login uses the STORED credentials.
    if (Object.keys(localCreds).length > 0 && hasChanges) {
      await handleSave();
    }
    setReloggingIn(true);
    try {
      const res = await reloginBroker(brokerId);
      if (res?.success) {
        const expiryNote = res.seconds_until_expiry
          ? ` Session valid ~${formatExpiry(res.seconds_until_expiry)}.`
          : '';
        const engineNote = res.applied_to_running_engine
          ? ' Running engine updated live.'
          : '';
        toast.success(`${brokerMeta?.name || brokerId}: ${res.message || 'Re-login successful.'}${expiryNote}${engineNote}`);
      } else if (res?.requires_browser && res?.auth_url) {
        // Fyers: SEBI-mandated browser 2FA — open the login page.
        window.open(res.auth_url, '_blank', 'noopener');
        toast.info(`${brokerMeta?.name || brokerId}: complete login + 2FA in the new tab — the token is saved automatically.`);
      } else {
        toast.error(`${brokerMeta?.name || brokerId}: ${res?.message || 'Re-login failed'}`);
      }
      await refreshTokenStatus();
      await refreshBackendStatus();
    } catch (err: any) {
      toast.error(`${brokerMeta?.name || brokerId} re-login failed: ${err.message || err}`);
    } finally {
      setReloggingIn(false);
    }
  }, [brokerId, brokerMeta?.name, reloggingIn, localCreds, hasChanges, handleSave, refreshTokenStatus, refreshBackendStatus]);

  // Fyers OAuth redirect lands on /settings?broker=fyers&auth=… — surface
  // the outcome and refresh the session panel after the token exchange.
  useEffect(() => {
    if (brokerId === 'fyers') {
      const authStatus = searchParams.get('auth') || searchParams.get('fyers_auth');
      const authMsg = searchParams.get('message') || searchParams.get('msg');
      if (authStatus === 'success') {
        toast.success('Fyers login successful! Access token is valid.');
        refreshTokenStatus();
        refreshBackendStatus();
      } else if (authStatus === 'error') {
        toast.error(`Fyers login failed: ${authMsg || 'Unknown error'}`);
      }
    }
  }, [brokerId, searchParams, refreshTokenStatus, refreshBackendStatus]);

  const updateField = (key: string, val: string) => {
    setLocalCreds((prev) => ({ ...prev, [key]: val }));
  };

  // No credentials needed (Paper Broker / Yahoo Finance)
  if (!needsCreds) {
    return (
      <Card
        className={`bg-ub-surface transition-all ${
          isActiveEngineBroker
            ? 'border-ub-profit/60 shadow-[0_0_15px_rgba(0,208,156,0.12)] bg-ub-profit/[0.02]'
            : 'border-ub-border'
        }`}
      >
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div
                className="h-9 w-9 rounded-lg flex items-center justify-center font-bold text-sm"
                style={{ backgroundColor: theme.colors.profit + '15', color: theme.colors.profit, border: '1px solid ' + theme.colors.profit + '30' }}
              >
                {brokerMeta?.name?.[0] || '?'}
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <CardTitle className="text-base font-semibold text-ub-text-primary">
                    {brokerMeta?.name || brokerId}
                  </CardTitle>
                  {isActiveEngineBroker && (
                    <Badge className="bg-ub-profit text-ub-background font-bold text-[9px] px-1.5 py-0 border-none flex items-center gap-1">
                      <Zap size={9} className="fill-current" /> ACTIVE IN ENGINE
                    </Badge>
                  )}
                </div>
                <p className="text-[11px] text-ub-text-disabled mt-0.5">
                  {brokerId === 'paper'
                    ? 'Built-in simulator — no configuration needed. Uses virtual money with simulated or real data.'
                    : 'Free market data from Yahoo Finance. No API key required.'}
                </p>
              </div>
            </div>
            <Badge
              variant="outline"
              className="border-ub-profit/40 text-ub-profit bg-ub-profit/10 text-[10px] font-semibold"
            >
              <CheckCircle2 size={11} className="mr-1" />
              Always Ready
            </Badge>
          </div>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card
      className={`bg-ub-surface transition-all ${
        isActiveEngineBroker
          ? 'border-ub-profit/60 shadow-[0_0_15px_rgba(0,208,156,0.12)] bg-ub-profit/[0.02]'
          : 'border-ub-border'
      }`}
    >
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div
              className="h-9 w-9 rounded-lg flex items-center justify-center font-bold text-sm"
              style={{
                backgroundColor: isConfigured ? theme.colors.profit + '15' : theme.colors.surfaceActive,
                color: isConfigured ? theme.colors.profit : theme.colors.textMuted,
                border: '1px solid ' + (isConfigured ? theme.colors.profit + '30' : theme.colors.border),
              }}
            >
              {brokerMeta?.name?.[0] || '?'}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <CardTitle className="text-base font-semibold text-ub-text-primary">
                  {brokerMeta?.name || brokerId}
                </CardTitle>
                {isActiveEngineBroker && (
                  <Badge className="bg-ub-profit text-ub-background font-bold text-[9px] px-1.5 py-0 border-none flex items-center gap-1">
                    <Zap size={9} className="fill-current" /> ACTIVE IN ENGINE
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-ub-text-disabled mt-0.5">
                Fill in your API credentials below. They are stored encrypted in the backend database — never in your browser.
              </p>
            </div>
          </div>
          <Badge
            variant="outline"
            className={`text-[10px] font-semibold ${
              isConfigured
                ? 'border-ub-profit/40 text-ub-profit bg-ub-profit/10'
                : 'border-ub-text-disabled/40 text-ub-text-disabled bg-ub-text-disabled/10'
            }`}
          >
            {isConfigured ? (
              <><CheckCircle2 size={11} className="mr-1" /> Configured</>
            ) : (
              <><XCircle size={11} className="mr-1" /> Not Configured</>
            )}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {fields.map((field) => (
            <div key={field.key} className="space-y-2">
              <Label className="text-ub-text-muted text-sm">{field.label}</Label>
              <Input
                type={field.type || 'text'}
                value={localCreds[field.key] || ''}
                onChange={(e) => updateField(field.key, e.target.value)}
                placeholder={field.placeholder}
                className="bg-ub-background border-ub-border text-ub-text-primary"
              />
            </div>
          ))}
        </div>
        {isConfigured && !hasDraftValues && (
          <div
            className="flex items-start gap-2 px-3 py-2.5 rounded-lg"
            style={{
              backgroundColor: theme.colors.profit + '08',
              border: '1px solid ' + theme.colors.profit + '20',
            }}
          >
            <CheckCircle2 size={13} className="mt-0.5 shrink-0" style={{ color: theme.colors.profit }} />
            <p className="text-[11px] leading-relaxed" style={{ color: theme.colors.textMuted }}>
              Credentials are <span className="font-semibold" style={{ color: theme.colors.textPrimary }}>saved in the backend</span> (encrypted). The
              fields above show blank after a page reload by design — secrets are never sent back to the
              browser. Re-enter them only to update; <span className="font-semibold">Test</span> and{' '}
              <span className="font-semibold">Re-login / Connect</span> always use the saved copy.
            </p>
          </div>
        )}
        {showSessionPanel && tokenStatus && (
          <div
            className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-3 py-2.5 rounded-lg"
            style={{
              backgroundColor:
                tokenStatus.token_state === 'valid'
                  ? theme.colors.profit + '08'
                  : tokenStatus.token_state === 'expired'
                  ? theme.colors.loss + '08'
                  : 'rgba(255,255,255,0.02)',
              border: `1px solid ${
                tokenStatus.token_state === 'valid'
                  ? theme.colors.profit + '25'
                  : tokenStatus.token_state === 'expired'
                  ? theme.colors.loss + '25'
                  : theme.colors.border
              }`,
            }}
          >
            <div className="flex items-start gap-2 min-w-0">
              <Clock
                size={13}
                className="mt-0.5 shrink-0"
                style={{
                  color:
                    tokenStatus.token_state === 'valid'
                      ? theme.colors.profit
                      : tokenStatus.token_state === 'expired'
                      ? theme.colors.loss
                      : theme.colors.textMuted,
                }}
              />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="text-[11px] font-semibold text-ub-text-primary">Daily session:</span>
                  {tokenStatus.token_state === 'valid' ? (
                    <span className="text-[11px] font-semibold" style={{ color: theme.colors.profit }}>
                      ● Valid — {formatExpiry(tokenStatus.seconds_until_expiry ?? 0)}
                    </span>
                  ) : tokenStatus.token_state === 'expired' ? (
                    <span className="text-[11px] font-semibold" style={{ color: theme.colors.loss }}>
                      ● Expired — re-login required
                    </span>
                  ) : !tokenStatus.has_credentials ? (
                    <span className="text-[11px] text-ub-text-muted">
                      ○ Not configured — save credentials to enable re-login
                    </span>
                  ) : (
                    <span className="text-[11px] text-ub-text-muted">○ No session yet — log in once</span>
                  )}
                </div>
                <p className="text-[10px] text-ub-text-disabled mt-0.5">
                  {tokenStatus.has_credentials
                    ? `Last login: ${formatTimeAgo(tokenStatus.last_relogin_at ?? tokenStatus.last_auth)}`
                    : 'Last login: —'}
                  {tokenStatus.relogin_method === 'totp' ?
                    ' · one-click re-login (TOTP)' :
                    tokenStatus.relogin_method === 'browser' ?
                    ' · browser 2FA (SEBI)' : ''}
                </p>
              </div>
            </div>
            <Button
              onClick={handleRelogin}
              disabled={reloggingIn || !tokenStatus.has_credentials}
              size="sm"
              className="bg-ub-accent hover:bg-ub-accent-hover text-ub-background font-semibold text-xs shrink-0"
              title={
                tokenStatus.relogin_method === 'totp'
                  ? 'Log in again now using your stored PIN + TOTP secret'
                  : 'Open the broker login page (daily 2FA)'
              }
            >
              {reloggingIn ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
              ) : (
                <KeyRound className="h-3.5 w-3.5 mr-1.5" />
              )}
              {tokenStatus.token_state === 'valid' ? 'Re-login' : 'Login'}
            </Button>
          </div>
        )}
        {showSessionPanel && !tokenStatus && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-ub-border">
            <Loader2 className="h-3 w-3 animate-spin" style={{ color: theme.colors.textMuted }} />
            <span className="text-[11px] text-ub-text-muted">Checking session status…</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <Button
            onClick={handleClear}
            variant="ghost"
            className="text-ub-text-disabled hover:text-ub-loss hover:bg-ub-loss/10 text-xs"
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            Clear
          </Button>
          <div className="flex gap-2">
            <Button
              onClick={handleTest}
              disabled={testing || saving || !isConfigured}
              variant="outline"
              className="border-ub-accent/40 text-ub-accent hover:bg-ub-accent/10 hover:text-ub-accent text-xs"
            >
              {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <TestTube className="h-3.5 w-3.5 mr-1.5" />}
              Test
            </Button>
            <Button
              onClick={handleSave}
              disabled={hasChanges ? saving || testing : saving}
              className="bg-ub-accent hover:bg-ub-accent-hover text-ub-background font-semibold text-xs"
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <Save className="h-3.5 w-3.5 mr-1.5" />}
              Save
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/* ─────────────────────────────────────────────
   Main Section
   ───────────────────────────────────────────── */

export default function BrokerSettingsSection() {
  const engineState = useStore((s) => s.engine.status);
  const activeBrokerId = useStore((s) => s.engine.activeBroker);
  const engineMode = useStore((s) => s.engine.mode);
  const isEngineRunning = engineState === 'running' || engineState === 'paused';

  // Hydrate the "Configured" badges from the backend DB on mount.
  const refreshBackendStatus = useRefreshBrokerStatus();
  useEffect(() => {
    refreshBackendStatus();
  }, [refreshBackendStatus]);

  const currentBrokerId = activeBrokerId || (engineMode === 'live' ? 'fyers' : 'paper');
  const activeBrokerMeta = BROKER_LIST.find((b) => b.id === currentBrokerId);

  const paperBrokers = BROKER_LIST.filter((b) => b.category === 'paper');
  const liveBrokers = BROKER_LIST.filter((b) => b.category === 'live');

  return (
    <div className="space-y-6">
      {/* Active Engine Broker Status Banner */}
      <div
        className="p-4 rounded-xl border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 transition-all"
        style={{
          backgroundColor: isEngineRunning ? 'rgba(0, 208, 156, 0.06)' : 'rgba(255, 255, 255, 0.02)',
          borderColor: isEngineRunning ? 'rgba(0, 208, 156, 0.3)' : 'rgba(255, 255, 255, 0.1)',
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className={`h-11 w-11 rounded-xl flex items-center justify-center shrink-0 ${
              isEngineRunning ? 'bg-ub-profit/15 text-ub-profit' : 'bg-ub-accent/10 text-ub-accent'
            }`}
          >
            <ShieldCheck size={22} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-ub-text-muted">
                Active Execution Broker in Engine
              </span>
              <Badge
                className={`text-[10px] font-bold ${
                  isEngineRunning
                    ? 'bg-ub-profit text-ub-background animate-pulse'
                    : 'bg-ub-text-disabled/20 text-ub-text-muted'
                }`}
              >
                {isEngineRunning ? '● RUNNING IN ENGINE' : '○ ENGINE IDLE'}
              </Badge>
            </div>
            <p className="text-base font-bold text-ub-text-primary mt-0.5">
              {activeBrokerMeta?.name || currentBrokerId}
              <span className="text-xs font-normal text-ub-text-muted ml-2">
                ({engineMode.toUpperCase()} Mode)
              </span>
            </p>
            <p className="text-[11px] text-ub-text-disabled mt-0.5">
              {isEngineRunning
                ? 'All incoming strategy opportunities and risk-managed orders are active through this broker.'
                : 'Configure credentials below. When you start the engine, select which broker to trade with.'}
            </p>
          </div>
        </div>
      </div>

      {/* Info banner */}
      <div
        className="flex items-start gap-3 px-4 py-3 rounded-lg"
        style={{ backgroundColor: theme.colors.info + '08', border: '1px solid ' + theme.colors.info + '20' }}
      >
        <Info size={16} className="shrink-0 mt-0.5" style={{ color: theme.colors.info }} />
        <div>
          <p className="text-xs font-semibold" style={{ color: theme.colors.textPrimary }}>
            Broker Credentials &amp; API Integration
          </p>
          <p className="text-[11px] mt-0.5 leading-relaxed" style={{ color: theme.colors.textMuted }}>
            Your API credentials are stored encrypted in the backend database (never in browser localStorage). Configure at least one broker to use with the trading engine. Paper Broker and Yahoo Finance work without any credentials.
          </p>
        </div>
      </div>

      {/* Daily re-login explainer */}
      <div
        className="flex items-start gap-3 px-4 py-3 rounded-lg"
        style={{ backgroundColor: 'rgba(255, 176, 32, 0.05)', border: '1px solid rgba(255, 176, 32, 0.2)' }}
      >
        <KeyRound size={16} className="shrink-0 mt-0.5" style={{ color: '#ffb020' }} />
        <div>
          <p className="text-xs font-semibold" style={{ color: theme.colors.textPrimary }}>
            Daily Broker Login — one click each morning
          </p>
          <p className="text-[11px] mt-0.5 leading-relaxed" style={{ color: theme.colors.textMuted }}>
            Broker API sessions expire every trading day (Angel One at midnight, Dhan after 24h, Shoonya early
            morning, Fyers by ~05:30 IST). Save your PIN + TOTP secret once for Angel One / Shoonya / Dhan —
            then just press <span className="font-semibold">Re-login</span> on the broker card below (or before
            starting the engine). Fyers requires the browser 2FA page by SEBI design; the button opens it for
            you and the token saves automatically.
          </p>
        </div>
      </div>

      {/* Paper Mode Data Sources */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Shield size={15} style={{ color: theme.colors.profit }} />
          <h3 className="text-sm font-semibold text-ub-text-primary">Paper Trade Data Sources</h3>
          <Badge className="text-[9px] px-1.5 py-0 bg-ub-profit/10 text-ub-profit border-ub-profit/20 font-medium">
            No credentials needed
          </Badge>
        </div>
        <div className="space-y-3">
          {paperBrokers.map((b) => (
            <BrokerCredentialCard
              key={b.id}
              brokerId={b.id}
              isActiveEngineBroker={isEngineRunning && currentBrokerId === b.id}
            />
          ))}
        </div>
      </div>

      <Separator className="bg-ub-border" />

      {/* Live Brokers */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Zap size={15} style={{ color: theme.colors.loss }} />
          <h3 className="text-sm font-semibold text-ub-text-primary">Live Trade Brokers</h3>
          <Badge className="text-[9px] px-1.5 py-0 bg-ub-loss/10 text-ub-loss border-ub-loss/20 font-medium">
            Credentials required
          </Badge>
        </div>
        <div className="space-y-3">
          {liveBrokers.map((b) => (
            <BrokerCredentialCard
              key={b.id}
              brokerId={b.id}
              isActiveEngineBroker={isEngineRunning && currentBrokerId === b.id}
            />
          ))}
        </div>
      </div>
    </div>
  );
}