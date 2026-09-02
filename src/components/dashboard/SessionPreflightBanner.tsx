'use client';

import { AlertTriangle, KeyRound, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useEngineStatus, useSessionPreflight } from '@/hooks/useApi';
import { useStore } from '@/lib/store';

const BROKER_LABELS: Record<string, string> = {
  angel_one: 'Angel One',
  shoonya: 'Shoonya',
  dhan: 'Dhan',
  fyers: 'Fyers',
};

/**
 * Re-login pre-flight banner (dashboard).
 *
 * Surfaces an expired / missing daily broker session BEFORE it costs money:
 * the backend /api/brokers/preflight endpoint (same logic the 08:45 IST
 * scheduler job uses) is polled for the RUNNING engine's active broker.
 * Hidden when the engine is stopped or the broker needs no daily session
 * (paper / yahoo) so it never nags in pure-paper mode.
 */
export default function SessionPreflightBanner() {
  const engineStatus = useStore((s) => s.engine.status);
  const { data: engineData } = useEngineStatus();

  // The engine's broker is authoritative while it runs; the store mirrors it.
  const activeBroker = (engineData as any)?.broker || (engineData as any)?.broker_name;
  const isRunning = (engineStatus || 'stopped') === 'running';

  // Only poll while the engine is running — the banner is actionable then.
  const { data: preflight } = useSessionPreflight(
    typeof activeBroker === 'string' ? activeBroker : undefined,
    isRunning,
  );

  if (!isRunning || !preflight) return null;
  if (preflight.level !== 'warning' && preflight.level !== 'critical') return null;

  const isCritical = preflight.level === 'critical';
  const brokerLabel = BROKER_LABELS[preflight.broker] || preflight.broker;

  return (
    <div
      role="alert"
      aria-live="polite"
      className={`flex flex-col sm:flex-row sm:items-center gap-3 rounded-lg border px-4 py-3 ${
        isCritical
          ? 'border-ub-loss/40 bg-ub-loss/10'
          : 'border-ub-warning/40 bg-ub-warning/10'
      }`}
    >
      <div className="flex items-start gap-3 min-w-0 flex-1">
        <AlertTriangle
          className={`h-5 w-5 shrink-0 mt-0.5 ${isCritical ? 'text-ub-loss' : 'text-ub-warning'}`}
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p
            className={`text-sm font-semibold ${
              isCritical ? 'text-ub-loss' : 'text-ub-warning'
            }`}
          >
            {isCritical ? 'Broker session expired' : 'Broker session check failed'}
            <span className="text-ub-text-muted font-normal"> · {brokerLabel}</span>
          </p>
          <p className="text-xs text-ub-text-muted mt-0.5">{preflight.message}</p>
        </div>
      </div>
      <Button
        asChild
        size="sm"
        variant={isCritical ? 'default' : 'outline'}
        className={`shrink-0 text-xs font-semibold ${
          isCritical
            ? 'bg-ub-loss hover:bg-ub-loss/90 text-white'
            : 'border-ub-warning/50 text-ub-warning hover:bg-ub-warning/10'
        }`}
      >
        <a href={`/settings?broker=${preflight.broker}`}>
          {preflight.relogin_method === 'totp' ? (
            <KeyRound className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
          ) : (
            <Settings className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
          )}
          {preflight.relogin_method === 'totp' ? 'One-click re-login' : 'Open Settings'}
        </a>
      </Button>
    </div>
  );
}
