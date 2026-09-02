'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useStrategies, useEngineStatus } from '@/hooks/useApi';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Checkbox } from '@/components/ui/checkbox';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  ChevronDown,
  FlaskConical,
  Activity,
  TrendingUp,
  TrendingDown,
  Minus,
  Zap,
  BarChart3,
} from 'lucide-react';
import { useStore, type MarketRegime } from '@/lib/store';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

interface StrategyParams {
  [key: string]: string | number | boolean;
}

interface Strategy {
  id: string;
  name: string;
  description: string;
  category: 'core' | 'advanced';
  active: boolean;
  winRate: number | null;
  signals: number | null;
  trades: number | null;
  pauseReason?: 'regime_mismatch' | 'manual_pause';
  sparkline: number[] | null;
  params: StrategyParams;
  shadow?: boolean;
  shadowStats?: {
    total_signals: number;
    resolved: number;
    wins: number;
    losses: number;
    expired: number;
    pending: number;
    signal_win_rate: number;
  } | null;
}

// ─────────────────────────────────────────────
// Regime Config
// ─────────────────────────────────────────────

const REGIME_CONFIG: Record<MarketRegime, { label: string; color: string; bgColor: string; icon: React.ElementType }> = {
  bull: { label: 'Bull', color: 'text-ub-profit', bgColor: 'bg-ub-profit/15 border-ub-profit/30', icon: TrendingUp },
  bear: { label: 'Bear', color: 'text-ub-loss', bgColor: 'bg-ub-loss/15 border-ub-loss/30', icon: TrendingDown },
  sideways: { label: 'Sideways', color: 'text-ub-warning', bgColor: 'bg-ub-warning/15 border-ub-warning/30', icon: Minus },
  volatile: { label: 'Volatile', color: 'text-ub-volatile', bgColor: 'bg-ub-volatile/15 border-ub-volatile/30', icon: Zap },
};

// ─────────────────────────────────────────────
// Sparkline Component
// ─────────────────────────────────────────────

function Sparkline({ data, color = '#00d09c' }: { data: number[]; color?: string }) {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const width = 80;
  const height = 28;
  const padding = 2;

  const points = data
    .map((v, i) => {
      const x = padding + (i / (data.length - 1)) * (width - padding * 2);
      const y = height - padding - ((v - min) / range) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <svg width={width} height={height} className="shrink-0">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ─────────────────────────────────────────────
// Strategy Card
// ─────────────────────────────────────────────

function StrategyCard({ strategy, onToggle }: { strategy: Strategy; onToggle: (id: string, enabled: boolean) => void }) {
  const [expanded, setExpanded] = useState(false);

  const winRateColor =
    strategy.winRate === null
      ? 'text-ub-text-muted'
      : strategy.winRate >= 65
        ? 'text-ub-profit'
        : strategy.winRate >= 58
          ? 'text-ub-warning'
          : 'text-ub-loss';

  const sparkColor = strategy.active ? (strategy.winRate !== null && strategy.winRate >= 65 ? '#22c55e' : '#f59e0b') : '#475569';

  return (
    <Collapsible open={expanded} onOpenChange={setExpanded}>
      <Card
        className={cn(
          'bg-ub-surface border-ub-border transition-all duration-200',
          strategy.active
            ? 'hover:border-ub-accent/40'
            : 'opacity-70 hover:opacity-100',
          expanded && 'border-ub-accent/50',
        )}
      >
        <CollapsibleTrigger asChild>
          <CardHeader className="cursor-pointer select-none p-4 pb-2">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-ub-text-primary text-sm">{strategy.name}</span>
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-[10px] px-1.5 py-0',
                      strategy.active
                        ? 'border-ub-profit/40 text-ub-profit bg-ub-profit/10'
                        : 'border-ub-warning/40 text-ub-warning bg-ub-warning/10',
                    )}
                  >
                    {strategy.active ? 'Active' : 'Paused'}
                  </Badge>
                  {strategy.shadow && (
                    <Badge
                      variant="outline"
                      className="text-[10px] px-1.5 py-0 border-ub-accent/40 text-ub-accent bg-ub-accent/10"
                    >
                      Shadow — signals recorded, no orders
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-ub-text-muted mt-1 truncate">{strategy.description}</p>
              </div>
              <ChevronDown
                className={cn(
                  'h-4 w-4 text-ub-text-muted shrink-0 transition-transform duration-200',
                  expanded && 'rotate-180',
                )}
              />
            </div>
          </CardHeader>
        </CollapsibleTrigger>

        <CardContent className="p-4 pt-0">
          {strategy.pauseReason && !strategy.active && (
            <p className="text-[11px] text-ub-warning mb-2">
              ⚠ {strategy.pauseReason === 'regime_mismatch' ? 'Regime mismatch' : 'Manual pause'}
            </p>
          )}

          <div className="flex items-center gap-4 text-xs mb-3">
            <span className={cn('font-medium', winRateColor)}>
              Win Rate: {strategy.winRate === null ? '—' : `${strategy.winRate}%`}
            </span>
            {strategy.sparkline && strategy.sparkline.length > 1 && (
              <Sparkline data={strategy.sparkline} color={sparkColor} />
            )}
          </div>

          {strategy.trades === null || strategy.trades === 0 ? (
            <p className="text-[11px] text-ub-text-disabled mb-2">
              No real trades yet — statistics build only from executed trades (never simulated).
            </p>
          ) : null}

          {strategy.shadow && strategy.shadowStats && strategy.shadowStats.total_signals > 0 && (
            <p className="text-[11px] text-ub-text-muted mb-2">
              Shadow tracking: {strategy.shadowStats.total_signals} signals —{' '}
              {strategy.shadowStats.wins} hit target, {strategy.shadowStats.losses} stopped,{' '}
              {strategy.shadowStats.expired} expired, {strategy.shadowStats.pending} pending
              {strategy.shadowStats.resolved > 0 &&
                ` (signal win rate ${strategy.shadowStats.signal_win_rate}% — separate from trade stats)`}
            </p>
          )}

          <div className="flex items-center gap-4 text-xs text-ub-text-muted mb-3">
            <span>
              Signals: <span className="text-ub-text-primary font-medium">{strategy.signals ?? '—'}</span>{' '}
              | Trades: <span className="text-ub-text-primary font-medium">{strategy.trades ?? '—'}</span>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Switch
              checked={strategy.active}
              onCheckedChange={(checked) => onToggle(strategy.id, checked)}
              className="data-[state=checked]:bg-ub-accent"
            />
            <Link href={`/backtest?strategy=${encodeURIComponent(strategy.id)}`} className="ml-auto">
              <Button variant="outline" size="sm" className="h-7 text-xs gap-1 border-ub-border text-ub-text-muted hover:text-ub-accent hover:border-ub-accent/40 cursor-pointer">
                <FlaskConical className="h-3 w-3" />
                Backtest
              </Button>
            </Link>
          </div>

          <CollapsibleContent>
            <Separator className="my-3 bg-ub-border" />
            <div className="space-y-1.5">
              <p className="text-[11px] font-medium text-ub-text-muted uppercase tracking-wider mb-2">
                Strategy Parameters
              </p>
              {Object.keys(strategy.params).length === 0 ? (
                <p className="text-xs text-ub-text-disabled">No parameters exposed by the engine for this strategy.</p>
              ) : (
                Object.entries(strategy.params).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between text-xs">
                    <span className="text-ub-text-muted">{key}</span>
                    <span className="text-ub-text-primary font-mono bg-ub-bg/50 px-1.5 py-0.5 rounded">
                      {String(value)}
                    </span>
                  </div>
                ))
              )}
            </div>
          </CollapsibleContent>
        </CardContent>
      </Card>
    </Collapsible>
  );
}

// ─────────────────────────────────────────────
// Page Component
// ─────────────────────────────────────────────

const EMPTY_ARRAY: Strategy[] = [];

export default function StrategiesPage() {
  const regime = useStore((s) => s.engine.regime);
  const vix = useStore((s) => s.engine.vix);

  const { data: apiStrategies, toggle } = useStrategies();
  const { data: engineStatus } = useEngineStatus();

  // Real regime confidence from the engine (0 = detector not yet run).
  const regimeConfidencePct = useMemo(() => {
    const raw = (engineStatus as any)?.regime_confidence;
    const n = typeof raw === 'number' ? raw : Number(raw ?? 0);
    if (!Number.isFinite(n) || n <= 0) return null;
    return n <= 1 ? Math.round(n * 100) : Math.round(n);
  }, [engineStatus]);

  const strategies: Strategy[] = useMemo(() => {
    if (!apiStrategies || !Array.isArray(apiStrategies)) return EMPTY_ARRAY;
    return apiStrategies.map((item: any, idx: number) => {
      const id = item.name || item.id || `strat-${idx}`;
      const name = item.display_name || item.name || item.id || 'Strategy';
      const description = item.description || 'Automated algorithmic strategy with real-time risk guards.';
      const category = (Array.isArray(item.tags) && item.tags.includes('advanced')) || item.category === 'advanced' ? 'advanced' : 'core';
      const active = Boolean(item.is_enabled ?? item.is_active ?? item.active ?? true);

      // Honest performance stats — null (shown as "—") when the engine DB
      // has no history for the strategy. Never fabricate numbers.
      let winRate: number | null = null;
      if (typeof item.performance?.win_rate === 'number' && item.performance.total_trades > 0) {
        winRate = item.performance.win_rate > 1
          ? Math.round(item.performance.win_rate)
          : Math.round(item.performance.win_rate * 100);
      } else if (typeof item.winRate === 'number') {
        winRate = item.winRate > 1 ? Math.round(item.winRate) : Math.round(item.winRate * 100);
      }

      const signals = typeof item.signals === 'number'
        ? item.signals
        : (typeof item.performance?.total_trades === 'number' ? item.performance.total_trades : null);
      const trades = typeof item.trades === 'number'
        ? item.trades
        : (typeof item.performance?.total_trades === 'number' ? item.performance.total_trades : null);
      const sparkline = Array.isArray(item.sparkline) && item.sparkline.length > 1 ? item.sparkline : null;

      // Only real parameters reported by the backend — no invented defaults.
      const rawParams = item.parameters || item.params;
      const params: StrategyParams = rawParams && typeof rawParams === 'object' && !Array.isArray(rawParams)
        ? (rawParams as StrategyParams)
        : {};

      return {
        id,
        name,
        description,
        category,
        active,
        winRate,
        signals,
        trades,
        pauseReason: item.pauseReason,
        sparkline,
        params,
        shadow: Boolean(item.is_shadow),
        shadowStats: item.shadow_performance ?? null,
      };
    });
  }, [apiStrategies]);

  const regimeConfig = REGIME_CONFIG[regime];
  const RegimeIcon = regimeConfig.icon;

  const coreStrategies = useMemo(() => strategies.filter((s) => s.category === 'core'), [strategies]);
  const advancedStrategies = useMemo(() => strategies.filter((s) => s.category === 'advanced'), [strategies]);

  const handleToggle = (id: string, enabled: boolean) => {
    toggle({ name: id, isEnabled: enabled });
  };

  return (
    <div className="space-y-6">
      {/* ── Market Regime Panel ── */}
      <Card className="bg-ub-surface border-ub-border">
        <CardHeader className="p-4 pb-3">
          <CardTitle className="text-base font-semibold text-ub-text-primary flex items-center gap-2">
            <Activity className="h-4 w-4 text-ub-accent" />
            Market Regime
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 space-y-4">
          <div className="flex flex-wrap items-center gap-4">
            <Badge
              variant="outline"
              className={cn('text-xs px-3 py-1 border gap-1.5', regimeConfig.bgColor, regimeConfig.color)}
            >
              <RegimeIcon className="h-3.5 w-3.5" />
              {regimeConfig.label}
            </Badge>

            <div className="flex items-center gap-2 flex-1 min-w-[200px] max-w-xs">
              <span className="text-xs text-ub-text-muted">Confidence</span>
              {regimeConfidencePct === null ? (
                <span className="text-xs text-ub-text-disabled">— (detector idle)</span>
              ) : (
                <>
                  <Progress value={regimeConfidencePct} className="h-2 flex-1" />
                  <span className="text-xs font-medium text-ub-text-primary">{regimeConfidencePct}%</span>
                </>
              )}
            </div>
          </div>

          {/* VIX display */}
          <div className="flex items-center gap-2 text-xs text-ub-text-muted">
            <BarChart3 className="h-3 w-3" />
            <span>India VIX:</span>
            <span className={cn('font-medium', vix > 20 ? 'text-ub-volatile' : vix > 15 ? 'text-ub-warning' : 'text-ub-profit')}>
              {vix > 0 ? vix.toFixed(2) : '—'}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* ── Core Strategies ── */}
      <section>
        <h3 className="text-sm font-semibold text-ub-text-primary mb-3 flex items-center gap-2">
          <span className="w-1.5 h-4 rounded-full bg-ub-accent" />
          Core Strategies ({coreStrategies.length})
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {coreStrategies.map((s) => (
            <StrategyCard key={s.id} strategy={s} onToggle={handleToggle} />
          ))}
        </div>
      </section>

      {/* ── Advanced Strategies ── */}
      <section>
        <h3 className="text-sm font-semibold text-ub-text-primary mb-3 flex items-center gap-2">
          <span className="w-1.5 h-4 rounded-full bg-ub-volatile" />
          Advanced Strategies ({advancedStrategies.length})
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {advancedStrategies.map((s) => (
            <StrategyCard key={s.id} strategy={s} onToggle={handleToggle} />
          ))}
        </div>
      </section>
    </div>
  );
}