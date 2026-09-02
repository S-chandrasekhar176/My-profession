'use client';

import { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Clock, Layers, Target, ShieldAlert } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { useStore } from '@/lib/store';

/* ─────────────────────────────────────────────
   Types (mirror the backend payloads)
   ───────────────────────────────────────────── */

export interface BookingLevel {
  level?: number;
  trigger_price?: number;
  price?: number;
  book_pct?: number;
  book_percent?: number;
}

export interface ExpectedDuration {
  min_minutes: number;
  max_minutes: number;
  basis: string;
  velocity_per_5m?: number;
  candles_to_target?: number;
}

export interface TradePlanPosition {
  id: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  strategy?: string;
  entry: number;
  current: number;
  stopLoss?: number;
  target?: number;
  qty?: number;
  entryTime?: string | null;
  bookingLevels?: BookingLevel[];
  expectedDuration?: ExpectedDuration | null;
}

/* ─────────────────────────────────────────────
   Helpers
   ───────────────────────────────────────────── */

function levelPrice(l: BookingLevel): number {
  return Number(l.trigger_price ?? l.price ?? 0) || 0;
}

function levelPct(l: BookingLevel): number {
  return Number(l.book_pct ?? l.book_percent ?? 0) || 0;
}

function fmt(n: number, digits = 2): string {
  if (!Number.isFinite(n) || n === 0) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function elapsedMinutes(entryTime?: string | null): number | null {
  if (!entryTime) return null;
  const ts = Date.parse(entryTime);
  if (Number.isNaN(ts)) return null;
  return Math.max(0, Math.floor((Date.now() - ts) / 60000));
}

/* ─────────────────────────────────────────────
   Component
   ───────────────────────────────────────────── */

const BROKER_LABELS: Record<string, string> = {
  angel_one: 'Angel One',
  shoonya: 'Shoonya',
  dhan: 'Dhan',
  fyers: 'Fyers',
  zerodha: 'Zerodha',
  kite: 'Zerodha',
  paper: 'Paper',
  yahoo: 'Yahoo',
  yahoofinance: 'Yahoo',
};

/**
 * Live Trade Plan card (P0.5-c) — the pre-marked plan for ONE open position:
 * broker-aware execution label, live LTP ladder (SL / entry / T1–T4 / target
 * with ₹ + % distances), progress bar between SL and target, and the dynamic
 * duration estimate (elapsed vs expected) with overstay escalation.
 */
export default function LiveTradePlanCard({ position }: { position: TradePlanPosition }) {
  const engineMode = useStore((s) => s.engine.mode);
  const activeBroker = useStore((s) => s.engine.activeBroker);
  const [nowTick, setNowTick] = useState(Date.now());

  // 30s tick so elapsed-vs-expected and distances stay fresh even when the
  // LTP overlay hasn't changed.
  useEffect(() => {
    const t = setInterval(() => setNowTick(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { setNowTick(Date.now()); }, [position.current]);

  const isLong = position.direction === 'BUY';
  const ltp = position.current > 0 ? position.current : position.entry;
  const sl = position.stopLoss ?? 0;
  const target = position.target ?? 0;

  // Live distance from LTP to a level (positive = level above LTP)
  const dist = (level: number) => (level > 0 ? level - ltp : 0);
  const distPct = (level: number) => (level > 0 && ltp > 0 ? ((level - ltp) / ltp) * 100 : 0);

  // Live R:R from LTP
  const riskLeg = isLong ? ltp - sl : sl - ltp;
  const rewardLeg = isLong ? target - ltp : ltp - target;
  const liveRR = riskLeg > 0 && rewardLeg > 0 ? rewardLeg / riskLeg : 0;

  // Progress between SL (0%) and target (100%)
  const span = isLong ? target - sl : sl - target;
  const covered = isLong ? ltp - sl : sl - ltp;
  const progress = span > 0 ? Math.min(100, Math.max(0, (covered / span) * 100)) : 0;

  // Duration / overstay
  const mins = elapsedMinutes(position.entryTime);
  const dur = position.expectedDuration;
  const hasDuration = Boolean(dur && dur.max_minutes > 0);
  const overstayRatio =
    hasDuration && mins !== null ? mins / Math.max(1, dur!.min_minutes) : 0;
  const overstayLevel =
    overstayRatio >= 2 ? 'red' : overstayRatio >= 1 ? 'amber' : 'ok';

  // Broker-aware execution label
  const brokerKey = (activeBroker || 'paper').toLowerCase();
  const brokerLabel = BROKER_LABELS[brokerKey] || brokerKey;
  const isLiveExecution = engineMode === 'live' && brokerKey !== 'paper';
  const executionLabel = isLiveExecution ? `Live order → ${brokerLabel}` : 'Paper fill';

  // Booking ladder (T1..Tn), sorted by distance from LTP
  const ladder = (position.bookingLevels ?? [])
    .map((l) => ({ price: levelPrice(l), pct: levelPct(l) }))
    .filter((l) => l.price > 0)
    .sort((a, b) => (isLong ? a.price - b.price : b.price - a.price));

  const levelRow = (
    label: string,
    price: number,
    tone: 'sl' | 'entry' | 'target' | 'level',
    icon?: React.ReactNode,
    extra?: string,
  ) => {
    const toneClass =
      tone === 'sl'
        ? 'text-ub-loss'
        : tone === 'target'
          ? 'text-ub-profit'
          : tone === 'entry'
            ? 'text-ub-text-primary'
            : 'text-ub-accent';
    const d = dist(price);
    const dp = distPct(price);
    return (
      <div className="flex items-center justify-between gap-2 py-1" key={label}>
        <span className="flex items-center gap-1.5 text-[11px] text-ub-text-muted min-w-0">
          {icon}
          <span className="truncate">{label}</span>
          {extra && <span className="text-ub-text-disabled">({extra})</span>}
        </span>
        <span className="flex items-baseline gap-2 shrink-0">
          <span className={`text-xs font-mono font-semibold ${toneClass}`}>
            {fmt(price)}
          </span>
          <span className={`text-[10px] font-mono ${d >= 0 ? 'text-ub-text-disabled' : 'text-ub-text-muted'}`}>
            {d >= 0 ? '+' : ''}{fmt(d)} ({dp >= 0 ? '+' : ''}{dp.toFixed(2)}%)
          </span>
        </span>
      </div>
    );
  };

  return (
    <div className="rounded-lg border border-ub-border bg-ub-background/40 p-3 space-y-2.5" data-testid="live-trade-plan">
      {/* Header row */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm font-bold text-ub-text-primary font-mono">{position.symbol}</span>
        <Badge
          variant="outline"
          className={`text-[10px] font-bold px-1.5 py-0 ${
            isLong
              ? 'border-ub-profit/30 text-ub-profit bg-ub-profit/10'
              : 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
          }`}
        >
          {isLong ? <ArrowUpRight className="h-3 w-3 mr-0.5" /> : <ArrowDownRight className="h-3 w-3 mr-0.5" />}
          {position.direction}
        </Badge>
        {position.strategy && (
          <Badge variant="outline" className="text-[10px] border-ub-accent/20 text-ub-accent bg-ub-accent/5">
            {position.strategy}
          </Badge>
        )}
        <Badge
          variant="outline"
          className={`text-[10px] ${
            isLiveExecution
              ? 'border-ub-warning/40 text-ub-warning bg-ub-warning/10'
              : 'border-ub-border text-ub-text-muted'
          }`}
          title={
            isLiveExecution
              ? 'Orders are routed to the live broker account'
              : 'Simulated fills — no real money moves'
          }
        >
          {executionLabel}
        </Badge>
        <span className="ml-auto text-xs font-mono text-ub-text-primary" aria-label="Last traded price">
          LTP {fmt(ltp)}
        </span>
      </div>

      {/* Progress between SL and target */}
      {sl > 0 && target > 0 && span > 0 && (
        <div className="space-y-1">
          <Progress value={progress} className="h-1.5 bg-ub-loss/20" />
          <div className="flex justify-between text-[10px] font-mono text-ub-text-disabled">
            <span className="text-ub-loss">SL {fmt(sl)}</span>
            <span>entry {fmt(position.entry)}</span>
            <span className="text-ub-profit">TGT {fmt(target)}</span>
          </div>
        </div>
      )}

      {/* Price ladder */}
      <div className="rounded-md border border-ub-border/60 px-2.5 py-1 divide-y divide-ub-border/40">
        {levelRow('Stop-loss', sl, 'sl', <ShieldAlert className="h-3 w-3" aria-hidden="true" />)}
        {levelRow('Entry (filled)', position.entry, 'entry', <Layers className="h-3 w-3" aria-hidden="true" />)}
        {ladder.map((l, i) =>
          levelRow(
            `T${i + 1} book`,
            l.price,
            'level',
            <Target className="h-3 w-3" aria-hidden="true" />,
            l.pct > 0 ? `${l.pct}%` : undefined,
          ),
        )}
        {levelRow('Target', target, 'target', <Target className="h-3 w-3" aria-hidden="true" />)}
      </div>

      {/* Footer: live R:R + duration */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
        <span className="text-ub-text-muted">
          Live R:R{' '}
          <span className="font-mono font-semibold text-ub-text-primary">
            {liveRR > 0 ? `1:${liveRR.toFixed(2)}` : '—'}
          </span>
        </span>
        {hasDuration ? (
          <span
            className={`flex items-center gap-1 ${
              overstayLevel === 'red'
                ? 'text-ub-loss font-semibold'
                : overstayLevel === 'amber'
                  ? 'text-ub-warning font-medium'
                  : 'text-ub-text-muted'
            }`}
            title={`Estimate basis: ${dur!.basis} (velocity ₹${fmt(dur!.velocity_per_5m ?? 0)}/5m candle)`}
          >
            <Clock className="h-3 w-3" aria-hidden="true" />
            {mins !== null ? `${mins}m elapsed` : '—'} · est {dur!.min_minutes}–{dur!.max_minutes}m
            {overstayLevel !== 'ok' && <span className="ml-1">· overstay</span>}
          </span>
        ) : (
          <span className="text-ub-text-muted flex items-center gap-1">
            <Clock className="h-3 w-3" aria-hidden="true" />
            {mins !== null ? `${mins}m elapsed` : '—'} · no duration estimate
          </span>
        )}
        <span className="ml-auto text-ub-text-disabled" aria-hidden="true">{nowTick ? '' : ''}</span>
      </div>
    </div>
  );
}
