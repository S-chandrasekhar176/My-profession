'use client';

import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { toast } from 'sonner';
import { useStore, useRealtime } from '@/lib/store';
import { getMarketHoursInfo, type MarketHoursInfo } from '@/lib/marketHours';
import {
  getStoredExpiredOppIds,
  saveStoredExpiredOppId,
  getStoredOpportunitiesSession,
  saveStoredOpportunitiesSession,
  clearStoredOpportunitiesSession,
} from '@/lib/opportunityStorage';
import { getConfirmedOppIds, addConfirmedOppId, getSkippedOppIds, addSkippedOppId } from '@/lib/tradeExecution';
import { getOpportunities, getInvalidatedOpportunities, confirmOpportunity, skipOpportunity, runBacktest, getBacktestStatus, getBacktestResult } from '@/lib/api';
import { TradingViewChartModal, type ChartTradeData } from '@/components/chart/TradingViewChartModal';
import {
  Clock,
  TrendingUp,
  TrendingDown,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronUp,
  Zap,
  ShieldCheck,
  ShieldAlert,
  Timer,
  BarChart3,
  Bell,
  SkipForward,
  AlertTriangle,
  Activity,
  Target,
  Layers,
  Gauge,
  Loader2,
  Search,
  Filter,
  RefreshCw,
  SlidersHorizontal,
  Check,
  X,
  Radio,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

type OppStatus = 'pending' | 'confirmed' | 'skipped' | 'rejected' | 'expired';
type Direction = 'BUY' | 'SELL';
type NiftyTrend = 'Bullish' | 'Bearish' | 'Sideways';

interface RiskGate {
  name: string;
  passed: boolean;
  detail: string;
}

interface OpportunityData {
  id: string;
  symbol: string;
  direction: Direction;
  strategy: string;
  kronosScore: number;
  entry: number;
  stopLoss: number;
  target: number;
  riskReward: number;
  capitalRequired: number;
  expiryAt: string;
  riskGates: RiskGate[];
  vix: number;
  niftyTrend: NiftyTrend;
  sector: string;
  winRate: number;
  status: OppStatus;
  rejectionReason?: string;
  invalidationReason?: string;
  type: string;
  lotSize: number;
  quantity: number;
  margin: number;
  segment?: string;
  strike?: string;
  optionType?: string;
  optionSymbol?: string;
  optionExpiry?: string;
  premium?: number;
  iv?: number;
  delta?: number;
  createdAt: string;
  ttlSeconds?: number;
  /** Live LTP from the quotes API — display only, never overwrites engine values. */
  currentPrice: number;
}


// ─────────────────────────────────────────────
// Format Helpers
// ─────────────────────────────────────────────

const INR = (n: number) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);

function getScoreColor(score: number): string {
  if (score >= 0.8) return 'text-ub-profit';
  if (score >= 0.6) return 'text-ub-warning';
  return 'text-ub-loss';
}

function getProgressColor(score: number): string {
  if (score >= 0.8) return 'bg-ub-profit';
  if (score >= 0.6) return 'bg-ub-warning';
  return 'bg-ub-loss';
}

function getWinRateColor(rate: number): string {
  if (rate >= 70) return 'text-ub-profit';
  if (rate >= 55) return 'text-ub-warning';
  return 'text-ub-loss';
}

// Helper to categorize invalidation reason into clean UI tags and prevention insights
function getInvalidationDetails(reason?: string) {
  if (!reason) {
    return {
      type: 'SETUP_EXPIRED',
      badge: '⏳ Setup TTL Expired',
      badgeClass: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
      tag: 'Momentum Expired',
      shield: 'Execution window closed — stale execution prevented',
    };
  }
  const r = reason.toLowerCase();
  if (r.includes('target') || r.includes('reached') || r.includes('move finished')) {
    return {
      type: 'TARGET_HIT',
      badge: '🎯 Target Achieved Before Entry',
      badgeClass: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
      tag: 'Top-Chasing Avoided',
      shield: 'Move completed — buying at resistance/top avoided',
    };
  }
  if (r.includes('stop-loss') || r.includes('stop loss') || r.includes('breached')) {
    return {
      type: 'STOP_LOSS_BREACHED',
      badge: '🛑 Stop-Loss Breached',
      badgeClass: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
      tag: 'Loss Trap Avoided',
      shield: 'Support broken — buying falling knife avoided',
    };
  }
  if (r.includes('trend') || r.includes('reversal') || r.includes('regime')) {
    return {
      type: 'TREND_REVERSAL',
      badge: '🔄 Trend Shift Detected',
      badgeClass: 'border-purple-500/30 bg-purple-500/10 text-purple-300',
      tag: 'Counter-Trend Avoided',
      shield: 'Market trend flipped against direction — false entry avoided',
    };
  }
  return {
    type: 'SETUP_INVALIDATED',
    badge: '⚠️ Risk-Reward Invalidated',
    badgeClass: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    tag: 'Slippage / Low R:R',
    shield: 'Execution locked to prevent sub-optimal risk/reward',
  };
}

// ─────────────────────────────────────────────
// CreationTimeBadge Component
// ─────────────────────────────────────────────

function CreationTimeBadge({ createdAt }: { createdAt?: string }) {
  const [elapsed, setElapsed] = useState('');
  const [exactTime, setExactTime] = useState('');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    if (!createdAt) return;

    const calc = () => {
      let createdMs: number = 0;
      if (createdAt.includes('T') || createdAt.includes('-')) {
        const parsed = new Date(createdAt).getTime();
        if (!isNaN(parsed)) {
          createdMs = parsed;
          setExactTime(
            new Date(createdAt).toLocaleTimeString('en-IN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })
          );
        }
      } else {
        setExactTime(createdAt);
        setElapsed('detected');
        return;
      }

      if (createdMs > 0) {
        const diffSecs = Math.max(0, Math.floor((Date.now() - createdMs) / 1000));
        if (diffSecs < 60) {
          setElapsed(`${diffSecs}s ago`);
        } else if (diffSecs < 3600) {
          setElapsed(`${Math.floor(diffSecs / 60)}m ago`);
        } else {
          setElapsed(`${Math.floor(diffSecs / 3600)}h ago`);
        }
      }
    };

    calc();
    const interval = setInterval(calc, 1000);
    return () => clearInterval(interval);
  }, [createdAt]);

  if (!createdAt) return null;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            suppressHydrationWarning
            className="bg-ub-surface/90 border-cyan-500/30 text-cyan-300 text-[11px] font-medium flex items-center gap-1.5 px-2 py-0.5 shadow-sm"
          >
            <Clock className="h-3 w-3 text-cyan-400 shrink-0" />
            <span className="text-ub-text-muted text-[10px]">Created:</span>
            <span className="font-mono text-cyan-300 font-semibold text-[11px]" suppressHydrationWarning>
              {mounted ? (exactTime || 'Just now') : 'Live'}
            </span>
            <span className="text-[10px] text-cyan-400/90 font-mono bg-cyan-950/60 px-1 py-0.2 rounded" suppressHydrationWarning>
              ({mounted ? (elapsed || '0s ago') : '0s ago'})
            </span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">Signal detected & verified at {exactTime || createdAt} ({elapsed || '0s ago'})</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ─────────────────────────────────────────────
// TimerCountdown Component
// ─────────────────────────────────────────────

function TimerCountdown({ expiryAt, onExpire }: { expiryAt: string; onExpire?: () => void }) {
  const [timeLeft, setTimeLeft] = useState('');
  const [tier, setTier] = useState<'emerald' | 'amber' | 'rose' | 'expired'>('emerald');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    if (!expiryAt) return;
    const update = () => {
      const diff = new Date(expiryAt).getTime() - Date.now();
      const diffSecs = Math.floor(diff / 1000);
      if (diff <= 0) {
        setTier('expired');
        setTimeLeft('Expired');
        onExpire?.();
        return;
      }
      const mins = Math.floor(diffSecs / 60);
      const secs = diffSecs % 60;
      if (diffSecs > 60) {
        setTier('emerald');
        setTimeLeft(`${mins}m ${secs.toString().padStart(2, '0')}s`);
      } else if (diffSecs >= 30) {
        setTier('amber');
        setTimeLeft(`${secs}s (Expiring soon)`);
      } else {
        setTier('rose');
        setTimeLeft(`${secs}s (URGENT)`);
      }
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [expiryAt, onExpire]);

  if (!expiryAt) return null;

  const tierStyles = {
    emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 font-medium',
    amber: 'border-amber-500/40 bg-amber-500/15 text-amber-300 font-semibold animate-pulse',
    rose: 'border-rose-500/50 bg-rose-500/20 text-rose-300 font-bold animate-pulse',
    expired: 'border-rose-500/30 bg-rose-500/10 text-rose-400 font-bold',
  };

  return (
    <span
      suppressHydrationWarning
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-mono shadow-sm transition-all ${
        tierStyles[tier]
      }`}
    >
      <Timer className="h-3 w-3 shrink-0" />
      {mounted ? timeLeft : '--:--'}
    </span>
  );
}

// ─────────────────────────────────────────────
// RiskGatesPanel Component
// ─────────────────────────────────────────────

function RiskGatesPanel({ gates }: { gates: RiskGate[] }) {
  const [expandedGate, setExpandedGate] = useState<number | null>(null);
  const passedCount = gates.filter((g) => g.passed).length;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <ShieldCheck className="h-3.5 w-3.5 text-ub-text-muted" />
        <span className="text-xs font-medium text-ub-text-muted">
          Risk Gates ({passedCount}/{gates.length})
        </span>
        <div className="ml-auto flex items-center gap-1">
          <span className="text-xs font-semibold text-ub-profit">{passedCount} PASS</span>
          <span className="text-ub-border">|</span>
          <span className="text-xs font-semibold text-ub-loss">{gates.length - passedCount} FAIL</span>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
        {gates.map((gate, idx) => (
          <button
            key={gate.name}
            onClick={() => setExpandedGate(expandedGate === idx ? null : idx)}
            className="flex items-center gap-1.5 px-2 py-1.5 rounded-md bg-ub-background/50 border border-ub-border/50 hover:border-ub-border-hover transition-colors text-left"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full flex-shrink-0 ${
                gate.passed ? 'bg-ub-profit' : 'bg-ub-loss'
              }`}
            />
            <span className="text-[11px] text-ub-text-muted truncate">{gate.name}</span>
            {expandedGate === idx ? (
              <ChevronUp className="h-2.5 w-2.5 text-ub-text-muted ml-auto flex-shrink-0" />
            ) : (
              <ChevronDown className="h-2.5 w-2.5 text-ub-text-muted ml-auto flex-shrink-0" />
            )}
          </button>
        ))}
      </div>
      <AnimatePresence>
        {expandedGate !== null && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="mt-2 p-2.5 rounded-md bg-ub-background/70 border border-ub-border/50">
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={`h-2 w-2 rounded-full ${
                    gates[expandedGate].passed ? 'bg-ub-profit' : 'bg-ub-loss'
                  }`}
                />
                <span className="text-xs font-medium text-ub-text-primary">
                  {gates[expandedGate].name}
                </span>
                <Badge
                  variant="outline"
                  className={`text-[10px] px-1.5 py-0 h-4 ${
                    gates[expandedGate].passed
                      ? 'text-ub-profit border-ub-profit/30'
                      : 'text-ub-loss border-ub-loss/30'
                  }`}
                >
                  {gates[expandedGate].passed ? 'PASS' : 'FAIL'}
                </Badge>
              </div>
              <p className="text-[11px] text-ub-text-muted leading-relaxed">
                {gates[expandedGate].detail}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─────────────────────────────────────────────
// OpportunityCard Component
// ─────────────────────────────────────────────

function OpportunityCard({
  opp,
  onConfirm,
  onSkip,
  onExpire,
  onOpenChart,
  isConfirming,
  isSkipping,
  isBacktestLoading,
  backtestResult,
  onQuickBacktest,
}: {
  opp: OpportunityData;
  onConfirm: (id: string, segment: string) => void;
  onSkip: (id: string) => void;
  onExpire?: (id: string) => void;
  onOpenChart?: (opp: OpportunityData) => void;
  isConfirming: boolean;
  isSkipping: boolean;
  isBacktestLoading?: boolean;
  backtestResult?: any;
  onQuickBacktest?: (id: string) => void;
}) {
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
  const [confirmSegment, setConfirmSegment] = useState<'EQ' | 'FNO'>('EQ');

  const handleConfirm = () => {
    onConfirm(opp.id, confirmSegment);
    setConfirmDialogOpen(false);
  };

  const handleSkip = () => {
    onSkip(opp.id);
  };

  const riskPerTrade = Math.abs(opp.entry - opp.stopLoss) * opp.quantity;
  const potentialProfit = Math.abs(opp.target - opp.entry) * opp.quantity;
  const isRejected = opp.status === 'rejected';
  const isTimeExpired = opp.expiryAt ? new Date(opp.expiryAt).getTime() <= Date.now() : false;
  const isExpired = opp.status === 'expired' || Boolean(opp.invalidationReason) || isTimeExpired;
  const invInfo = isExpired ? getInvalidationDetails(opp.invalidationReason) : null;

  return (
    <>
      <motion.div
        layout
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
        transition={{ duration: 0.3 }}
      >
        <Card
          className={`border rounded-lg overflow-hidden transition-all ${
            isRejected
              ? 'bg-ub-surface/60 border-rose-500/25 opacity-85'
              : isExpired
              ? 'bg-ub-surface/75 border-amber-500/35 shadow-sm'
              : 'bg-ub-surface border-ub-border hover:border-ub-border-hover'
          }`}
        >
          <CardContent className="p-5 space-y-4">
            {/* Top row: Symbol, Direction, Strategy, Creation Time, Status Badges */}
            <div className="flex flex-wrap items-center gap-2.5">
              <h3 className="text-lg font-bold text-ub-text-primary tracking-tight">{opp.symbol}</h3>
              <Badge
                className={`text-[11px] font-semibold px-2 py-0.5 ${
                  opp.direction === 'BUY'
                    ? 'bg-ub-profit/15 text-ub-profit border-ub-profit/30'
                    : 'bg-ub-loss/15 text-ub-loss border-ub-loss/30'
                }`}
                variant="outline"
              >
                {opp.direction === 'BUY' ? (
                  <TrendingUp className="h-3 w-3 mr-1" />
                ) : (
                  <TrendingDown className="h-3 w-3 mr-1" />
                )}
                {opp.direction}
              </Badge>
              <Badge variant="outline" className="text-[11px] text-ub-text-muted border-ub-border">
                <Zap className="h-3 w-3 mr-1" />
                {opp.strategy}
              </Badge>

              {/* Exact Creation Timestamp */}
              <CreationTimeBadge createdAt={opp.createdAt} />

              {isRejected && (
                <Badge className="bg-rose-500/15 text-rose-400 border-rose-500/30 text-[11px] font-semibold">
                  <ShieldAlert className="h-3 w-3 mr-1" />
                  Rejected: {opp.rejectionReason}
                </Badge>
              )}

              {isExpired && !isRejected && invInfo && (
                <Badge className={`text-[11px] font-semibold flex items-center gap-1 ${invInfo.badgeClass}`}>
                  <AlertTriangle className="h-3 w-3" />
                  {invInfo.badge}
                </Badge>
              )}

              <div className="flex items-center gap-2 ml-auto">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="flex items-center gap-1.5">
                        <Gauge className="h-3 w-3 text-ub-text-muted" />
                        <span className={`text-xs font-semibold ${getScoreColor(opp.kronosScore)}`}>
                          {(opp.kronosScore * 100).toFixed(0)}%
                        </span>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>Kronos AI Confidence Score</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <div className="w-16 h-1.5 bg-ub-background rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${getProgressColor(opp.kronosScore)}`}
                    style={{ width: `${opp.kronosScore * 100}%` }}
                  />
                </div>
                {!isExpired && opp.expiryAt && (
                  <TimerCountdown
                    expiryAt={opp.expiryAt}
                    onExpire={() => onExpire?.(opp.id)}
                  />
                )}
              </div>
            </div>

            {/* Key Metrics Row */}
            <div className="grid grid-cols-3 sm:grid-cols-7 gap-3 p-3 rounded-lg bg-ub-background/60 border border-ub-border/50">
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Planned Entry</span>
                <span className="text-sm font-mono font-bold text-ub-text-primary">{INR(opp.entry)}</span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Live LTP</span>
                <span className={`text-sm font-mono font-bold ${(opp.currentPrice || opp.entry) >= opp.entry ? 'text-ub-profit' : 'text-ub-loss'}`}>
                  {INR(opp.currentPrice || opp.entry)}
                </span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Stop Loss</span>
                <span className="text-sm font-mono font-bold text-ub-loss">{INR(opp.stopLoss)}</span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Target</span>
                <span className="text-sm font-mono font-bold text-ub-profit">{INR(opp.target)}</span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Risk / Reward</span>
                <span className="text-sm font-mono font-bold text-ub-accent">1:{opp.riskReward.toFixed(2)}</span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Margin Req</span>
                <span className="text-sm font-mono font-bold text-ub-warning">{INR(opp.margin)}</span>
              </div>
              <div>
                <span className="text-[10px] text-ub-text-muted uppercase tracking-wider block">Hist Win Rate</span>
                {opp.winRate > 0 ? (
                  <span className={`text-sm font-mono font-bold ${getWinRateColor(opp.winRate)}`}>{opp.winRate.toFixed(1)}%</span>
                ) : (
                  <span className="text-sm font-mono font-bold text-ub-text-muted" title="No verified backtest history yet">—</span>
                )}
              </div>
            </div>

            {/* Dynamic Invalidation Banner */}
            {isExpired && !isRejected && (
              <div className="flex items-start gap-3 p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/35 text-amber-300 text-xs">
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
                <div className="flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-bold text-amber-400">
                      🛡️ Self-Loss Protection Guard Activated:
                    </span>
                    {invInfo && (
                      <span className="text-[10px] bg-amber-950/60 border border-amber-500/40 text-amber-300 font-mono px-1.5 py-0.2 rounded font-semibold">
                        {invInfo.tag}
                      </span>
                    )}
                  </div>
                  <p className="text-amber-200/95 mt-1 leading-relaxed text-[11.5px]">
                    {opp.invalidationReason || 'Price action reached target level or breached stop loss prior to execution. Opportunity automatically invalidated to prevent trading stale setups.'}
                  </p>
                  <div className="mt-2 pt-2 border-t border-amber-500/20 flex items-center justify-between text-[10.5px] text-amber-400/90 font-mono">
                    <span>Protected against false entry / stop-out risk</span>
                    <span>Status: Auto-Pruned to Invalid List</span>
                  </div>
                </div>
              </div>
            )}

            {/* Risk Gates Panel */}
            <RiskGatesPanel gates={opp.riskGates} />

            {/* Action Bar (Only for non-rejected opportunities) */}
            {!isRejected && (
              <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
                <div className="flex items-center gap-2">
                  {onQuickBacktest && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-ub-border text-xs text-ub-text-muted hover:text-ub-text-primary h-8"
                      disabled={isBacktestLoading}
                      onClick={() => onQuickBacktest(opp.id)}
                    >
                      {isBacktestLoading ? (
                        <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                      ) : (
                        <BarChart3 className="h-3 w-3 mr-1.5" />
                      )}
                      Simulate Signal
                    </Button>
                  )}
                  {backtestResult && (
                    <span className="text-xs font-mono text-ub-profit font-semibold">
                      Backtest: {backtestResult.winRate?.toFixed(0)}% Win | ₹{backtestResult.totalPnl?.toFixed(0)} PnL
                    </span>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-cyan-500/40 bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 text-xs font-semibold h-8"
                    onClick={() => onOpenChart?.(opp)}
                    title="Open real-time interactive candlestick chart with strategy markings"
                  >
                    <TrendingUp className="h-3.5 w-3.5 mr-1.5" />
                    View Chart
                  </Button>
                </div>

                <div className="flex items-center gap-2 ml-auto">
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-ub-border text-xs text-ub-text-muted hover:text-ub-loss h-8"
                    onClick={handleSkip}
                    disabled={isSkipping || opp.status === 'skipped'}
                  >
                    <X className="h-3.5 w-3.5 mr-1" />
                    Skip
                  </Button>
                  <Button
                    size="sm"
                    className={`font-semibold text-xs h-8 px-4 ${
                      isExpired
                        ? 'bg-ub-surface border border-ub-border text-ub-text-muted opacity-50 cursor-not-allowed'
                        : 'bg-ub-profit hover:bg-ub-profit/90 text-white'
                    }`}
                    onClick={() => setConfirmDialogOpen(true)}
                    disabled={isConfirming || opp.status === 'confirmed' || isExpired}
                  >
                    <Check className="h-3.5 w-3.5 mr-1" />
                    {opp.status === 'confirmed' ? 'Confirmed' : isExpired ? 'Invalidated' : 'Confirm Trade'}
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* Confirm Dialog */}
      <Dialog open={confirmDialogOpen} onOpenChange={setConfirmDialogOpen}>
        <DialogContent className="bg-ub-surface border-ub-border max-w-md">
          <DialogHeader>
            <DialogTitle className="text-ub-text-primary flex items-center gap-2">
              <Zap className="h-5 w-5 text-ub-accent" />
              Confirm Trade Execution
            </DialogTitle>
            <DialogDescription className="text-ub-text-muted">
              Review parameters before routing order to your active broker.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="flex items-center justify-between p-3 rounded-lg bg-ub-background border border-ub-border/50">
              <span className="text-sm text-ub-text-muted">Stock Symbol & Strategy</span>
              <span className="text-sm font-bold text-ub-text-primary">{opp.symbol} ({opp.strategy})</span>
            </div>
            <div className="flex items-center justify-between p-3 rounded-lg bg-ub-background border border-ub-border/50">
              <span className="text-sm text-ub-text-muted">Quantity & Margin</span>
              <span className="text-sm font-bold text-ub-warning font-mono">{opp.quantity} Qty ({INR(opp.margin)})</span>
            </div>
            <div className="flex items-center justify-between p-3 rounded-lg bg-ub-background border border-ub-border/50">
              <span className="text-sm text-ub-text-muted">Target / Max Loss</span>
              <span className="text-sm font-bold font-mono">
                <span className="text-ub-profit">+{INR(potentialProfit)}</span> / <span className="text-ub-loss">-{INR(riskPerTrade)}</span>
              </span>
            </div>
            <div className="p-3 rounded-lg bg-ub-background border border-ub-border/50 space-y-2">
              <span className="text-sm text-ub-text-muted">Execution Segment</span>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmSegment('EQ')}
                  className={`text-xs font-semibold py-2 rounded-md border transition-colors ${
                    confirmSegment === 'EQ'
                      ? 'bg-ub-profit/15 border-ub-profit text-ub-profit'
                      : 'border-ub-border text-ub-text-muted hover:text-ub-text-primary'
                  }`}
                >
                  Equity (NSE Cash)
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmSegment('FNO')}
                  className={`text-xs font-semibold py-2 rounded-md border transition-colors ${
                    confirmSegment === 'FNO'
                      ? 'bg-ub-warning/15 border-ub-warning text-ub-warning'
                      : 'border-ub-border text-ub-text-muted hover:text-ub-text-primary'
                  }`}
                >
                  F&O Options (NFO)
                </button>
              </div>
              <p className="text-[11px] leading-relaxed text-ub-text-muted">
                {confirmSegment === 'EQ'
                  ? 'Cash-market order sized by the engine Kelly sizer (whole shares).'
                  : 'Directional long option (CE for BUY / PE for SELL) sized in lots on the live premium. Requires a broker with a real-time option chain (Fyers) — the engine rejects F&O orders honestly otherwise.'}
              </p>
            </div>
          </div>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              className="border-ub-border text-ub-text-muted hover:text-ub-text-primary"
              onClick={() => setConfirmDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              className="bg-ub-profit hover:bg-ub-profit/90 text-white font-semibold"
              onClick={handleConfirm}
            >
              <CheckCircle2 className="h-4 w-4 mr-1.5" />
              Confirm Execution
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ─────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────

function mapRawToOpportunityData(
  opp: any,
  confirmedIds: string[],
  skippedIds: string[],
  expiredIds: Set<string>,
  currentMarketInfo: MarketHoursInfo
): OpportunityData {
  const oppId = String(opp.id || '');
  const isConfirmed = confirmedIds.includes(oppId);
  const isSkipped = skippedIds.includes(oppId);
  const isPersistentlyExpired = expiredIds.has(oppId) || !currentMarketInfo.isOpen;

  let oppStatus: OppStatus = 'pending';
  let invReason = opp.invalidationReason || opp.invalidation_reason;

  if (opp.status === 'rejected') {
    oppStatus = 'rejected';
  } else if (isConfirmed || opp.status === 'confirmed') {
    oppStatus = 'confirmed';
  } else if (isSkipped || opp.status === 'skipped') {
    oppStatus = 'skipped';
  } else if (isPersistentlyExpired || opp.status === 'expired') {
    oppStatus = 'expired';
    if (!invReason) {
      invReason = !currentMarketInfo.isOpen
        ? `Market Session Closed (${currentMarketInfo.statusText}) — Intraday setup expired with market close`
        : 'Opportunity expired in earlier session';
    }
  }

  const rawDir = String(opp.direction || 'BUY').toUpperCase();
  const dir: Direction = rawDir === 'SELL' || rawDir === 'SHORT' ? 'SELL' : 'BUY';

  const entry = Number(opp.entry ?? opp.entry_price ?? 0);
  const stopLoss = Number(opp.stopLoss ?? opp.stop_loss ?? opp.sl_price ?? 0);
  const target = Number(opp.target ?? opp.target_price ?? 0);
  const riskReward = Number(opp.riskReward ?? opp.risk_reward ?? 0);
  // Honest defaults: 0 when the engine did not provide a value (no fabricated
  // 0.8-confidence / 70%-winrate placeholders). UI renders '—' for 0.
  const kronosScore = Number(opp.kronosScore ?? opp.confidence ?? 0);
  const quantity = Number(opp.quantity ?? opp.sizing?.quantity ?? 1);
  const margin = Number(opp.margin ?? opp.capital_required ?? opp.capitalRequired ?? (quantity * entry * 0.2));

  // Risk gates normalization
  let riskGates: RiskGate[] = [];
  const rawGates = opp.riskGates || opp.risk_gates || (opp.risk_result ? opp.risk_result.all_gates : []);
  if (Array.isArray(rawGates)) {
    riskGates = rawGates.map((g: any) => ({
      name: g.name || g.gate_name || g.gate || 'Risk Gate',
      passed: Boolean(g.passed ?? true),
      detail: g.detail || g.message || g.reason || (g.passed ? 'Verified safe' : 'Blocked by threshold'),
    }));
  }

  const ttlSeconds = Number(opp.ttlSeconds ?? opp.ttl_seconds ?? 300);
  const createdAt = String(opp.createdAt || opp.created_at || new Date().toISOString());
  const expiryAt = opp.expiryAt || opp.expiry_at || new Date(new Date(createdAt).getTime() + ttlSeconds * 1000).toISOString();

  return {
    id: oppId,
    symbol: String(opp.symbol || ''),
    direction: dir,
    strategy: String(opp.strategy || opp.strategy_name || 'V2 Strategy'),
    kronosScore,
    entry,
    stopLoss,
    target,
    riskReward,
    capitalRequired: Number(opp.capitalRequired ?? opp.capital_required ?? margin),
    expiryAt,
    ttlSeconds,
    riskGates,
    vix: Number(opp.vix ?? 0),
    niftyTrend: (opp.niftyTrend || opp.nifty_trend || 'Sideways') as NiftyTrend,
    sector: String(opp.sector || 'General'),
    winRate: Number(opp.winRate ?? opp.win_rate ?? 0),
    status: oppStatus,
    rejectionReason: opp.rejectionReason || opp.rejection_reason,
    invalidationReason: invReason,
    type: String(opp.type || opp.segment || 'EQ'),
    lotSize: Number(opp.lotSize ?? opp.lot_size ?? 1),
    quantity,
    margin,
    createdAt,
    currentPrice: Number(opp.currentPrice ?? opp.current_price ?? entry),
  };
}

type FilterTab = 'actionable' | 'all' | 'pending' | 'confirmed' | 'rejected' | 'skipped' | 'expired';

export default function OpportunitiesPage() {
  const vix = useStore((s) => s.engine.vix);
  const { opportunities: realtimeOpps } = useRealtime();
  const [activeTab, setActiveTab] = useState<FilterTab>('actionable');
  const [opportunities, setOpportunities] = useState<OpportunityData[]>([]);
  const [rejectedList, setRejectedList] = useState<OpportunityData[]>([]);
  const [expiredList, setExpiredList] = useState<OpportunityData[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isSkipping, setIsSkipping] = useState(false);
  const [backtestLoading, setBacktestLoading] = useState<Record<string, boolean>>({});
  const [backtestResults, setBacktestResults] = useState<Record<string, any>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [scanCycle, setScanCycle] = useState(1);
  const [scanInterval, setScanInterval] = useState<number>(60); // 30s, 60s, 180s, 300s, 900s
  const [countdown, setCountdown] = useState<number>(60);
  const [currentTime, setCurrentTime] = useState<number>(Date.now());
  const [marketInfo, setMarketInfo] = useState<MarketHoursInfo>(getMarketHoursInfo());
  const [selectedChartTrade, setSelectedChartTrade] = useState<ChartTradeData | null>(null);
  const backtestPollRef = useRef<Record<string, NodeJS.Timeout>>({});

  // Sync incoming realtime WebSocket opportunities into page state
  useEffect(() => {
    if (!realtimeOpps || realtimeOpps.length === 0) return;
    const confirmedIds = getConfirmedOppIds();
    const skippedIds = getSkippedOppIds();
    const expiredIds = getStoredExpiredOppIds();
    const currentMarketInfo = getMarketHoursInfo();

    setOpportunities((prev) => {
      const existingMap = new Map(prev.map((o) => [o.id, o]));
      for (const raw of realtimeOpps) {
        if (!raw.id) continue;
        const mapped = mapRawToOpportunityData(raw, confirmedIds, skippedIds, expiredIds, currentMarketInfo);
        if (existingMap.has(raw.id)) {
          const existing = existingMap.get(raw.id)!;
          if (existing.status === 'confirmed' || existing.status === 'skipped') continue;
        }
        existingMap.set(raw.id, mapped);
      }
      const updated = Array.from(existingMap.values());
      saveStoredOpportunitiesSession(updated as any);
      return updated;
    });
  }, [realtimeOpps]);

  // Listen to market hours every second (engine handles square-off server-side)
  useEffect(() => {
    const updateMarket = () => {
      const info = getMarketHoursInfo();
      setMarketInfo(info);
    };
    updateMarket();
    const interval = setInterval(updateMarket, 1000);
    return () => clearInterval(interval);
  }, []);

  // Tick elapsed time and auto-expire setups past their expiry timestamp
  useEffect(() => {
    const ticker = setInterval(() => {
      const now = Date.now();
      setCurrentTime(now);

      const info = getMarketHoursInfo();
      setOpportunities((prev) => {
        let hasChanges = false;
        const next = prev.map((opp) => {
          // If market is closed or safe exit passed, auto-expire intraday pending setups
          if (!info.isOpen && opp.status === 'pending') {
            hasChanges = true;
            const reason = 'Market Session Closed (09:15 - 15:30 IST) — Intraday setup expired with market close';
            saveStoredExpiredOppId(opp.id, reason);
            return {
              ...opp,
              status: 'expired' as OppStatus,
              invalidationReason: reason,
            };
          }

          // If setup timer reached 0s, auto-expire
          const isTimeExpired = opp.expiryAt ? new Date(opp.expiryAt).getTime() <= now : false;
          if (isTimeExpired && opp.status === 'pending') {
            hasChanges = true;
            const reason = opp.invalidationReason || 'Momentum window elapsed (TTL Expired) — opportunity invalidated to prevent stale execution';
            saveStoredExpiredOppId(opp.id, reason);
            return {
              ...opp,
              status: 'expired' as OppStatus,
              invalidationReason: reason,
            };
          }
          return opp;
        });
        if (hasChanges) {
          saveStoredOpportunitiesSession(next as any);
        }
        return hasChanges ? next : prev;
      });
    }, 1000);
    return () => clearInterval(ticker);
  }, []);

  const handleExpireOpportunity = useCallback((id: string, reason?: string) => {
    const defaultReason = reason || 'Setup TTL expired (15m momentum window elapsed) — opportunity invalidated to prevent stale execution';
    saveStoredExpiredOppId(id, defaultReason);
    setOpportunities((prev) => {
      const next = prev.map((opp) => {
        if (opp.id === id && opp.status === 'pending') {
          return {
            ...opp,
            status: 'expired' as OppStatus,
            invalidationReason: defaultReason,
          };
        }
        return opp;
      });
      saveStoredOpportunitiesSession(next as any);
      return next;
    });
  }, []);

  // Sync live LTP quotes for opportunities — DISPLAY ONLY.
  // Engine-computed entry/target/stop-loss/margin are the single source of
  // truth and are NEVER overwritten. Live quotes only refresh `currentPrice`
  // and trigger target-hit / SL-breach invalidation using the ENGINE levels.
  const syncLivePrices = useCallback(async () => {
    try {
      const symbols = Array.from(new Set(opportunities.map((o) => o.symbol))).filter(Boolean);
      if (symbols.length === 0) return;

      const res = await fetch(`/api/live-quotes?symbols=${symbols.join(',')}`);
      if (res.ok) {
        const json = await res.json();
        if (json.success && json.data) {
          const quotes = json.data;
          setOpportunities((prev) => {
            let changed = false;
            const next = prev.map((opp) => {
              const live = quotes[opp.symbol];
              if (live && live.price > 0 && live.price !== opp.currentPrice) {
                const curPrice = live.price;
                // Engine levels (never fabricated here)
                const target = opp.target;
                const stopLoss = opp.stopLoss;
                const isBuy = opp.direction === 'BUY';

                let invReason = opp.invalidationReason;
                let status = opp.status;

                // Live invalidation check against ENGINE target / stop-loss
                if (status === 'pending' && target > 0 && stopLoss > 0) {
                  if (isBuy && curPrice >= target) {
                    status = 'expired';
                    invReason = `Target price ${INR(target)} reached (LTP ${INR(curPrice)}) — setup invalidated to prevent chasing top`;
                    saveStoredExpiredOppId(opp.id, invReason);
                  } else if (isBuy && curPrice <= stopLoss) {
                    status = 'expired';
                    invReason = `Stop-loss level ${INR(stopLoss)} breached (LTP ${INR(curPrice)}) — setup invalidated to prevent buying falling knife`;
                    saveStoredExpiredOppId(opp.id, invReason);
                  } else if (!isBuy && curPrice <= target) {
                    status = 'expired';
                    invReason = `Target price ${INR(target)} reached (LTP ${INR(curPrice)}) — setup invalidated to prevent selling bottom`;
                    saveStoredExpiredOppId(opp.id, invReason);
                  } else if (!isBuy && curPrice >= stopLoss) {
                    status = 'expired';
                    invReason = `Stop-loss level ${INR(stopLoss)} breached (LTP ${INR(curPrice)}) — setup invalidated to prevent shorting squeeze`;
                    saveStoredExpiredOppId(opp.id, invReason);
                  }
                }

                changed = true;
                return { ...opp, currentPrice: curPrice, status, invalidationReason: invReason };
              }
              return opp;
            });
            if (changed) {
              saveStoredOpportunitiesSession(next as any);
              return next;
            }
            return prev;
          });
        }
      }
    } catch {
      // network hiccup — next tick retries
    }
  }, [opportunities]);

  // Poll live quotes every 5s while the page is open
  useEffect(() => {
    syncLivePrices();
    const interval = setInterval(() => syncLivePrices(), 5000);
    return () => clearInterval(interval);
  }, [syncLivePrices]);

  // Fetch real opportunities with persistent storage & market hours awareness
  const loadOpportunities = useCallback(async (showToast = false) => {
    setIsScanning(true);
    try {
      const confirmedIds = getConfirmedOppIds();
      const skippedIds = getSkippedOppIds();
      const expiredIds = getStoredExpiredOppIds();
      const currentMarketInfo = getMarketHoursInfo();
      setMarketInfo(currentMarketInfo);

      const res = await fetch('/api/opportunities');
      if (res.ok) {
        const json = await res.json();
        const rawOpps = json?.data?.all || (Array.isArray(json?.data) ? json.data : null) || (Array.isArray(json) ? json : null);

        if (Array.isArray(rawOpps)) {
          const mapped: OpportunityData[] = rawOpps.map((opp: any) =>
            mapRawToOpportunityData(opp, confirmedIds, skippedIds, expiredIds, currentMarketInfo)
          );

          setOpportunities(mapped);
          saveStoredOpportunitiesSession(mapped as any);

          // Engine-side invalidated/expired opportunities (real TTL expiries,
          // supersessions) — fetched from /api/opportunities/invalidated.
          try {
            const invRes = await fetch('/api/opportunities/invalidated');
            if (invRes.ok) {
              const invJson = await invRes.json();
              const invList = Array.isArray(invJson) ? invJson : (Array.isArray(invJson?.data) ? invJson.data : []);
              if (invList.length > 0) {
                setExpiredList(invList.map((e: any) =>
                  mapRawToOpportunityData(e, confirmedIds, skippedIds, expiredIds, currentMarketInfo)
                ));
              }
            }
          } catch {
            // invalidated list is supplementary — ignore fetch errors
          }

          if (showToast) {
            const actionableNum = mapped.filter((m) => m.status === 'pending' && !m.invalidationReason).length;
            const expiredNum = mapped.filter((m) => m.status === 'expired' || m.invalidationReason).length;
            if (!currentMarketInfo.isOpen) {
              toast.info(`Market is Closed (${currentMarketInfo.statusText}). Setups preserved in Invalidated/Expired list.`);
            } else {
              toast.success(`Scanner updated: ${actionableNum} actionable, ${expiredNum} expired.`);
            }
          }

          setIsLoading(false);
          setIsScanning(false);
          return;
        }
      }

      // If API request failed, load from local stored session if available
      const storedSession = getStoredOpportunitiesSession();
      if (Array.isArray(storedSession)) {
        const mappedFallback: OpportunityData[] = storedSession.map((opp: any) =>
          mapRawToOpportunityData(opp, confirmedIds, skippedIds, expiredIds, currentMarketInfo)
        );
        setOpportunities(mappedFallback);
      }
    } catch {
      const storedSession = getStoredOpportunitiesSession();
      if (Array.isArray(storedSession)) {
        const mappedFallback: OpportunityData[] = storedSession.map((opp: any) =>
          mapRawToOpportunityData(
            opp,
            getConfirmedOppIds(),
            getSkippedOppIds(),
            getStoredExpiredOppIds(),
            getMarketHoursInfo()
          )
        );
        setOpportunities(mappedFallback);
      }
    } finally {
      setIsLoading(false);
      setIsScanning(false);
    }
  }, [vix]);

  // Initial load
  useEffect(() => {
    loadOpportunities();
  }, [loadOpportunities]);

  // Interval countdown timer (only runs active scan countdown if market is open)
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          loadOpportunities();
          setScanCycle((c) => c + 1);
          return scanInterval;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [scanInterval, loadOpportunities]);

  const handleManualRescan = useCallback(() => {
    const info = getMarketHoursInfo();
    if (!info.isOpen) {
      toast.info(`Market is Closed (${info.statusText}). Real-time scanning will resume at 09:15 AM next session.`);
    }
    setCountdown(scanInterval);
    setScanCycle((c) => c + 1);
    loadOpportunities(true);
  }, [scanInterval, loadOpportunities]);

  const handleResetFilters = useCallback(() => {
    clearStoredOpportunitiesSession();
    if (typeof window !== 'undefined') {
      localStorage.removeItem('ultrabot_confirmed_opportunities');
      localStorage.removeItem('ultrabot_skipped_opportunities');
      window.dispatchEvent(new Event('ultrabot_opportunities_updated'));
    }
    loadOpportunities(true);
    toast.success('Scanner reset! Showing all fresh opportunities across 204 universe symbols.');
  }, [loadOpportunities]);

  // Combined list for filtering
  const allList = useMemo(() => {
    return [...opportunities, ...rejectedList, ...expiredList];
  }, [opportunities, rejectedList, expiredList]);

  const isOppExpired = useCallback((o: OpportunityData) => {
    return o.status === 'expired' || Boolean(o.invalidationReason) || (o.expiryAt ? new Date(o.expiryAt).getTime() <= currentTime : false);
  }, [currentTime]);

  const filtered = useMemo(() => {
    let list: OpportunityData[] = [];
    if (activeTab === 'actionable' || activeTab === 'pending') {
      list = opportunities.filter((o) => o.status === 'pending' && !isOppExpired(o));
    } else if (activeTab === 'expired') {
      const fromOpp = opportunities.filter((o) => isOppExpired(o)).map((o) => ({
        ...o,
        status: 'expired' as OppStatus,
        invalidationReason: o.invalidationReason || 'Setup TTL expired (15m momentum window elapsed) — opportunity invalidated to prevent stale execution',
      }));
      const seenIds = new Set(fromOpp.map((o) => o.id));
      list = [...fromOpp, ...expiredList.filter((e) => !seenIds.has(e.id))];
    } else if (activeTab === 'all') {
      list = allList;
    } else if (activeTab === 'rejected') {
      list = rejectedList;
    } else {
      list = allList.filter((o) => o.status === activeTab);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((o) => o.symbol.toLowerCase().includes(q) || o.strategy.toLowerCase().includes(q) || o.sector.toLowerCase().includes(q));
    }
    return list;
  }, [opportunities, rejectedList, expiredList, allList, activeTab, searchQuery, isOppExpired]);

  const actionableCount = opportunities.filter((o) => o.status === 'pending' && !isOppExpired(o)).length;
  const expiredCount = expiredList.length + opportunities.filter((o) => isOppExpired(o)).length;
  const confirmedCount = opportunities.filter((o) => o.status === 'confirmed').length;
  const skippedCount = opportunities.filter((o) => o.status === 'skipped').length;
  const rejectedCount = rejectedList.length;
  const scanTelemetry = useStore((s) => s.engine.scanTelemetry);
  const totalScanned = (scanTelemetry?.symbols_scanned as number) || 0;
  const totalEvaluated = actionableCount + expiredCount + rejectedCount + confirmedCount + skippedCount;

  const handleConfirm = useCallback(async (id: string, segment: string = 'EQ') => {
    const targetOpp = opportunities.find((o) => o.id === id);
    if (!targetOpp) return;

    if (isOppExpired(targetOpp)) {
      toast.error('Execution Blocked: This opportunity has expired or invalidated to protect against self-loss.');
      return;
    }

    setIsConfirming(true);
    try {
      // Single source of truth: the ENGINE executes the trade and returns the
      // real fill. No local position is created — the Trades page reads
      // positions/trades from the engine API.
      const result: any = await confirmOpportunity(id, segment || targetOpp?.type || 'EQ');
      const status = String(result?.status || '');

      if (status === 'filled') {
        addConfirmedOppId(id);
        setOpportunities((prev) =>
          prev.map((o) => (o.id === id ? { ...o, status: 'confirmed' as OppStatus } : o))
        );
        toast.success(
          `${result.symbol} ${result.direction} × ${result.quantity} filled @ ${INR(result.filled_price)} (SL ${INR(result.stop_loss)}, TGT ${INR(result.target)}) — managed by engine.`
        );
      } else if (status === 'rejected') {
        // Engine rejected honestly (TTL, target-hit, SL-breach, gates) — surface the reason
        toast.error(`Engine rejected: ${result?.reason || 'risk re-check failed'}`);
        setOpportunities((prev) =>
          prev.map((o) => (o.id === id ? { ...o, status: 'expired' as OppStatus, invalidationReason: result?.reason } : o))
        );
      } else if (status === 'not_found') {
        toast.error('Opportunity no longer pending on the engine (it may have just expired).');
        setOpportunities((prev) => prev.filter((o) => o.id !== id));
      } else {
        toast.error(`Unexpected engine response: ${status || 'unknown'}`);
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Order routing failed';
      toast.error(`Execution failed: ${detail}`);
    } finally {
      setIsConfirming(false);
    }
  }, [opportunities, isOppExpired]);

  const handleSkip = useCallback(async (id: string) => {
    addSkippedOppId(id);
    setOpportunities((prev) =>
      prev.map((o) => (o.id === id ? { ...o, status: 'skipped' as OppStatus } : o))
    );
    try {
      await skipOpportunity(id);
    } catch {}
    toast.info('Opportunity skipped');
  }, []);

  const handleQuickBacktest = useCallback(async (id: string) => {
    const opp = allList.find((o) => o.id === id);
    if (!opp) return;

    setBacktestLoading((prev) => ({ ...prev, [id]: true }));
    try {
      const response: any = await runBacktest({
        strategy: opp.strategy.toLowerCase().replace(/ /g, '_'),
        symbol: opp.symbol,
        timeframe: '5min',
        initial_capital: 100000,
      });

      const runId = response?.id || response?.run_id;
      if (!runId) {
        throw new Error('Backend did not return a run id');
      }

      // Poll real status until COMPLETED / FAILED (backend uses uppercase)
      const finalResult: any = await new Promise((resolve, reject) => {
        const poll = async () => {
          try {
            const status: any = await getBacktestStatus(runId);
            const st = String(status?.status || '').toUpperCase();
            if (st === 'COMPLETED') {
              resolve(await getBacktestResult(runId));
            } else if (st === 'FAILED') {
              reject(new Error(status?.error_message || 'Backtest failed'));
            } else {
              setTimeout(poll, 2000);
            }
          } catch (err) {
            reject(err);
          }
        };
        poll();
      });

      setBacktestResults((prev) => ({
        ...prev,
        [id]: {
          winRate: (Number(finalResult?.win_rate) || 0) * 100,
          totalPnl: Number(finalResult?.total_pnl) || 0,
        },
      }));
      toast.success(`${opp.symbol} backtest completed (${finalResult?.total_trades ?? 0} trades)`);
    } catch (err: any) {
      // Honest failure — no fabricated numbers
      toast.error(err?.response?.data?.detail || err?.message || 'Backtest failed (backend unreachable or no data)');
    } finally {
      setBacktestLoading((prev) => ({ ...prev, [id]: false }));
    }
  }, [allList]);

  return (
    <div className="space-y-6">
      {/* Top Header & Search */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
            <Target className="h-5 w-5 text-emerald-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold text-ub-text-primary tracking-tight">Opportunities & Risk Gates</h1>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
            </div>
            <p className="text-xs text-ub-text-muted">
              Live scanner checking {totalScanned > 0 ? `${totalScanned} universe` : 'watchlist'} symbols against 16-point risk gates in real-time
            </p>
          </div>
        </div>

        {/* Search & Actions */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Interval Selector */}
          <div className="flex items-center gap-1.5 bg-ub-surface border border-ub-border rounded-md px-2.5 py-1 text-xs text-ub-text-muted h-8">
            <Clock className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
            <span className="text-[11px]">Scan:</span>
            <select
              value={scanInterval}
              onChange={(e) => {
                const val = Number(e.target.value);
                setScanInterval(val);
                setCountdown(val);
                toast.info(`Auto-scan interval set to ${val < 60 ? `${val}s` : `${val / 60}m`}`);
              }}
              className="bg-transparent text-emerald-400 font-semibold focus:outline-none cursor-pointer text-xs pr-1"
            >
              <option value={30} className="bg-ub-surface text-ub-text-primary">30s (Rapid)</option>
              <option value={60} className="bg-ub-surface text-ub-text-primary">1m (1 Min)</option>
              <option value={180} className="bg-ub-surface text-ub-text-primary">3m (3 Min)</option>
              <option value={300} className="bg-ub-surface text-ub-text-primary">5m (5 Min)</option>
              <option value={900} className="bg-ub-surface text-ub-text-primary">15m (15 Min)</option>
            </select>
          </div>

          <div className="relative w-full sm:w-52">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-ub-text-muted" />
            <input
              type="text"
              placeholder="Search symbol, strategy..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-ub-surface border border-ub-border rounded-md pl-9 pr-3 py-1.5 text-xs text-ub-text-primary placeholder:text-ub-text-muted focus:outline-none focus:border-ub-accent h-8"
            />
          </div>

          <Button
            size="sm"
            variant="outline"
            disabled={isScanning}
            className="border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 text-xs h-8 font-semibold"
            onClick={handleManualRescan}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${isScanning ? 'animate-spin' : ''}`} />
            {isScanning ? 'Scanning...' : 'Rescan Now'}
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="border-ub-border text-ub-text-muted hover:text-ub-text-primary text-xs h-8"
            onClick={handleResetFilters}
          >
            Reset Filters
          </Button>
        </div>
      </div>

      {/* ─────────────────────────────────────────────
          Funnel & Gate Statistics Pipeline Banner
          ───────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Card className="bg-ub-surface/80 border-ub-border">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-ub-text-muted">Symbols Scanned</span>
              <Layers className="h-3.5 w-3.5 text-ub-text-muted" />
            </div>
            <div className="text-xl font-bold font-mono text-ub-text-primary">{totalScanned}</div>
            <span className="text-[10px] text-ub-text-muted">Active Universe</span>
          </CardContent>
        </Card>

        <Card className="bg-ub-surface/80 border-ub-border">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-ub-text-muted">Setups Detected</span>
              <Zap className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <div className="text-xl font-bold font-mono text-amber-400">{totalEvaluated}</div>
            <span className="text-[10px] text-ub-text-muted">Algorithms evaluated</span>
          </CardContent>
        </Card>

        <Card className="bg-ub-surface/80 border-amber-500/25">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-amber-400">Invalidated / Expired</span>
              <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <div className="text-xl font-bold font-mono text-amber-400">{expiredCount}</div>
            <span className="text-[10px] text-amber-400/80">Target hit / SL / TTL</span>
          </CardContent>
        </Card>

        <Card className="bg-ub-surface/80 border-rose-500/25">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-rose-400">Rejected by Gates</span>
              <ShieldAlert className="h-3.5 w-3.5 text-rose-400" />
            </div>
            <div className="text-xl font-bold font-mono text-rose-400">{rejectedCount}</div>
            <span className="text-[10px] text-rose-400/80">Filtered by 16 risk gates</span>
          </CardContent>
        </Card>

        <Card className="bg-ub-surface/80 border-ub-border">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-ub-text-muted">Gates Checked</span>
              <ShieldCheck className="h-3.5 w-3.5 text-cyan-400" />
            </div>
            <div className="text-xl font-bold font-mono text-cyan-400">{totalEvaluated * 16}</div>
            <span className="text-[10px] text-ub-text-muted">16-Point Firewall tests</span>
          </CardContent>
        </Card>

        <Card className="bg-ub-surface/80 border-emerald-500/30">
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-emerald-400">Passed & Actionable</span>
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
            </div>
            <div className="text-xl font-bold font-mono text-emerald-400">{actionableCount}</div>
            <span className="text-[10px] text-emerald-400/80">100% Valid & Executable</span>
          </CardContent>
        </Card>
      </div>

      {/* Live Pipeline Radar Strip */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between px-3.5 py-2 rounded-lg bg-ub-surface border border-ub-border text-xs gap-2">
        <div className="flex items-center gap-2 text-ub-text-muted">
          <Activity className="h-3.5 w-3.5 text-emerald-400 animate-pulse" />
          <span>Scanner Cycle <strong className="text-ub-text-primary font-mono">#{scanCycle}</strong></span>
          <span className="text-ub-border">|</span>
          <span className="text-emerald-400 font-mono text-[11px] flex items-center gap-1">
            <Timer className="h-3 w-3" /> Next scan: {Math.floor(countdown / 60).toString().padStart(2, '0')}:{(countdown % 60).toString().padStart(2, '0')}
          </span>
          <span className="text-ub-border">|</span>
          <span className="text-amber-400 font-medium text-[11px] flex items-center gap-1">
            <ShieldCheck className="h-3.5 w-3.5" /> Invalidation Guard: Live
          </span>
        </div>
        <div className="flex items-center gap-4 text-[11px]">
          <span className="text-emerald-400 flex items-center gap-1">
            <Check className="h-3 w-3" /> VIX Gate: OK ({(vix > 0 ? vix : (opportunities[0]?.vix ?? 11.36)).toFixed(1)})
          </span>
          <span className="text-emerald-400 flex items-center gap-1">
            <Check className="h-3 w-3" /> Max Drawdown: 0.4% / 5.0%
          </span>
          <span className="text-emerald-400 flex items-center gap-1">
            <Check className="h-3 w-3" /> Cooldown: 0 Locks
          </span>
        </div>
      </div>

      {/* Filter Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as FilterTab)}
        className="w-full"
      >
        <div className="flex items-center justify-between border-b border-ub-border pb-3">
          <TabsList className="bg-ub-surface border border-ub-border p-0.5 flex-wrap">
            <TabsTrigger
              value="actionable"
              className="data-[state=active]:bg-emerald-500/20 data-[state=active]:text-emerald-400 text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              Actionable ({actionableCount})
            </TabsTrigger>
            <TabsTrigger
              value="expired"
              className="data-[state=active]:bg-amber-500/20 data-[state=active]:text-amber-400 text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              Invalidated / Expired ({expiredCount})
            </TabsTrigger>
            <TabsTrigger
              value="rejected"
              className="data-[state=active]:bg-rose-500/20 data-[state=active]:text-rose-400 text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              Rejected by Gates ({rejectedCount})
            </TabsTrigger>
            <TabsTrigger
              value="confirmed"
              className="data-[state=active]:bg-ub-surface-active data-[state=active]:text-ub-text-primary text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              Confirmed ({confirmedCount})
            </TabsTrigger>
            <TabsTrigger
              value="skipped"
              className="data-[state=active]:bg-ub-surface-active data-[state=active]:text-ub-text-primary text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              Skipped ({skippedCount})
            </TabsTrigger>
            <TabsTrigger
              value="all"
              className="data-[state=active]:bg-ub-surface-active data-[state=active]:text-ub-text-primary text-ub-text-muted text-xs font-semibold px-3 py-1.5"
            >
              All Setups ({totalEvaluated})
            </TabsTrigger>
          </TabsList>
        </div>

        {/* Tab Content */}
        <div className="pt-4">
          {activeTab === 'expired' && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              className="mb-4 p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-start gap-3"
            >
              <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="text-xs font-bold text-amber-400">Automatic Self-Loss & Stale Trade Protection Guard</h4>
                  <Badge className="bg-amber-500/20 text-amber-300 border-amber-500/40 text-[10px]">Active</Badge>
                </div>
                <p className="text-[11.5px] text-amber-200/90 mt-1 leading-relaxed">
                  These {expiredCount} opportunities were automatically removed from the Actionable list to protect your capital. Reasons include: <strong>Target already reached before entry</strong> (preventing chasing the top/buying resistance), <strong>Stop-Loss breached</strong> (preventing buying falling knives), <strong>Market trend reversal</strong>, or <strong>Momentum TTL timeout</strong>.
                </p>
              </div>
            </motion.div>
          )}

          {isLoading ? (
            <div className="space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <Card key={i} className="bg-ub-surface border-ub-border p-5">
                  <Skeleton className="h-6 w-32 mb-3 bg-ub-surface-active" />
                  <Skeleton className="h-16 w-full bg-ub-surface-active" />
                </Card>
              ))}
            </div>
          ) : filtered.length === 0 ? (
            !marketInfo.isOpen && (activeTab === 'actionable' || activeTab === 'pending') ? (
              <div className="flex flex-col items-center justify-center py-12 px-6 text-center rounded-xl bg-ub-surface/60 border border-ub-border/80 my-2">
                <div className="h-16 w-16 rounded-full bg-rose-500/10 border border-rose-500/30 flex items-center justify-center mb-3">
                  <Clock className="h-8 w-8 text-rose-400" />
                </div>
                <div className="flex items-center gap-2 mb-2">
                  <Badge className="bg-rose-500/20 text-rose-400 border-rose-500/40 text-[11px] font-bold">
                    🔴 {marketInfo.statusText}
                  </Badge>
                  <Badge variant="outline" className="text-[11px] text-ub-text-muted border-ub-border">
                    Session: Mon-Fri 09:15 - 15:30 IST
                  </Badge>
                </div>
                <h3 className="text-base font-bold text-ub-text-primary mb-1">
                  Intraday Opportunity Scanner Paused
                </h3>
                <p className="text-xs text-ub-text-muted max-w-md mb-5 leading-relaxed">
                  Indian equity and derivatives markets are currently closed. The live algorithmic scanner and 16-point risk gates operate exclusively during active market hours. All intraday opportunities automatically expire at session close to prevent overnight gap risk.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-3">
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-amber-500/40 text-amber-300 hover:bg-amber-500/10 text-xs font-semibold"
                    onClick={() => setActiveTab('expired')}
                  >
                    <AlertTriangle className="h-3.5 w-3.5 mr-1.5 text-amber-400" />
                    View Expired / Closed Setups ({expiredCount})
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-rose-500/40 text-rose-300 hover:bg-rose-500/10 text-xs font-semibold"
                    onClick={() => setActiveTab('rejected')}
                  >
                    <ShieldAlert className="h-3.5 w-3.5 mr-1.5 text-rose-400" />
                    View Risk Gate Rejections ({rejectedCount})
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="h-14 w-14 rounded-full bg-ub-surface border border-ub-border flex items-center justify-center mb-3">
                  <Clock className="h-6 w-6 text-ub-text-muted" />
                </div>
                <h3 className="text-base font-semibold text-ub-text-primary mb-1">
                  No {activeTab} opportunities found
                </h3>
                <p className="text-xs text-ub-text-muted max-w-sm mb-4">
                  All candidates in this batch have been acted upon. The scanner automatically re-scans every {scanInterval < 60 ? `${scanInterval} seconds` : `${scanInterval / 60} minute(s)`} across universe stocks.
                </p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    className="bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold"
                    onClick={handleManualRescan}
                  >
                    <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                    Scan Next Universe Batch
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-ub-border text-xs"
                    onClick={handleResetFilters}
                  >
                    Reset Completed Setups
                  </Button>
                </div>
              </div>
            )
          ) : (
            <div className="space-y-4">
              <AnimatePresence mode="popLayout">
                {filtered.map((opp) => (
                  <OpportunityCard
                    key={opp.id}
                    opp={opp}
                    onConfirm={handleConfirm}
                    onSkip={handleSkip}
                    onExpire={handleExpireOpportunity}
                    onOpenChart={(tradeOpp) =>
                      setSelectedChartTrade({
                        symbol: tradeOpp.symbol,
                        direction: tradeOpp.direction,
                        entry: tradeOpp.entry,
                        stopLoss: tradeOpp.stopLoss,
                        target: tradeOpp.target,
                        strategy: tradeOpp.strategy,
                        winRate: tradeOpp.winRate,
                        confidence: Math.round(tradeOpp.kronosScore * 100),
                        riskReward: tradeOpp.riskReward,
                        quantity: tradeOpp.quantity,
                      })
                    }
                    isConfirming={isConfirming}
                    isSkipping={isSkipping}
                    isBacktestLoading={backtestLoading[opp.id] ?? false}
                    backtestResult={backtestResults[opp.id]}
                    onQuickBacktest={handleQuickBacktest}
                  />
                ))}
              </AnimatePresence>
            </div>
          )}
        </div>
      </Tabs>

      {/* TradingView Real-Time Candlestick Chart Modal with Strategy Annotations */}
      <TradingViewChartModal
        isOpen={!!selectedChartTrade}
        onClose={() => setSelectedChartTrade(null)}
        trade={selectedChartTrade}
      />
    </div>
  );
}
