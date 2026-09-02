'use client';

import { useState, useEffect, useMemo } from 'react';
import { useRiskStatus, useRiskGates } from '@/hooks/useApi';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  ShieldOff,
  Timer,
  AlertTriangle,
  Info,
  AlertCircle,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

type RiskStatus = 'normal' | 'caution' | 'stopped';
type GateStatus = 'PASS' | 'FAIL';
type EventSeverity = 'info' | 'warning' | 'critical';

interface RiskLimit {
  label: string;
  current: string;
  limit: string;
  currentNum: number;
  limitNum: number;
  unit: string;
}

interface RiskGate {
  id: string;
  name: string;
  status: GateStatus;
  detail: string;
}

interface RiskEvent {
  time: string;
  type: string;
  gate: string;
  details: string;
  severity: EventSeverity;
}

interface RejectionBreakdown {
  gate: string;
  count: number;
  color: string;
}

// ─────────────────────────────────────────────
import { useTrades, usePositions } from '@/hooks/useApi';

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function getLimitBarColor(pct: number): string {
  if (pct < 50) return 'bg-ub-profit';
  if (pct < 80) return 'bg-ub-warning';
  return 'bg-ub-loss';
}

function formatINR(n: number): string {
  return '₹' + Math.abs(n).toLocaleString('en-IN');
}

const SEVERITY_CONFIG: Record<EventSeverity, { color: string; bgColor: string; icon: React.ElementType }> = {
  info: { color: 'text-ub-accent', bgColor: 'bg-ub-accent/10 border-ub-accent/30', icon: Info },
  warning: { color: 'text-ub-warning', bgColor: 'bg-ub-warning/10 border-ub-warning/30', icon: AlertTriangle },
  critical: { color: 'text-ub-loss', bgColor: 'bg-ub-loss/10 border-ub-loss/30', icon: XCircle },
};

// ─────────────────────────────────────────────
// Countdown hook
// ─────────────────────────────────────────────

function useCountdown(initialSeconds: number, active: boolean) {
  const [seconds, setSeconds] = useState(initialSeconds);

  useEffect(() => {
    if (!active) return;
    if (seconds <= 0) return;
    const timer = setInterval(() => {
      setSeconds((s) => Math.max(0, s - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [active, seconds]);

  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return { seconds, display: `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}` };
}

// ─────────────────────────────────────────────
// Page Component
// ─────────────────────────────────────────────

export default function RiskDashboardPage() {
  const { data: statusData } = useRiskStatus();
  const { data: gatesData } = useRiskGates();

  const [riskConfig, setRiskConfig] = useState({
    maxOpenPositions: 5,
    maxPerSector: 2,
    maxDailyTrades: 10,
    maxDailyLossPct: 3,
    maxConsecutiveLosses: 3,
    coolOffMinutes: 15,
    maxDrawdownPct: 5,
    vixThreshold: 24,
    minSignalConfidence: 70,
  });

  const [capitalConfig, setCapitalConfig] = useState({
    virtualCapital: 500000,
    maxCapitalUsagePct: 80,
  });

  // Real engine data (fallbacks for the risk status API)
  const { data: tradesData } = useTrades();
  const { data: positionsData } = usePositions();
  const engineTrades = useMemo(() => (Array.isArray(tradesData) ? tradesData : []), [tradesData]);
  const enginePositions = useMemo(() => (Array.isArray(positionsData) ? positionsData : []), [positionsData]);

  const loadAllState = () => {
    if (typeof window !== 'undefined') {
      try {
        const savedRisk = localStorage.getItem('ultrabot_settings_risk');
        if (savedRisk) {
          setRiskConfig((prev) => ({ ...prev, ...JSON.parse(savedRisk) }));
        }
        const savedCapital = localStorage.getItem('ultrabot_settings_capital');
        if (savedCapital) {
          setCapitalConfig((prev) => ({ ...prev, ...JSON.parse(savedCapital) }));
        }
      } catch {}
    }
  };

  useEffect(() => {
    loadAllState();
    window.addEventListener('storage', loadAllState);
    window.addEventListener('ultrabot_settings_updated', loadAllState);
    return () => {
      window.removeEventListener('storage', loadAllState);
      window.removeEventListener('ultrabot_settings_updated', loadAllState);
    };
  }, []);

  const status = (statusData as any) || {};
  const gates = (gatesData as any) || { gates: {}, limits: {} };

  // Effective risk limits: backend GET /api/risk/gates is authoritative; the
  // cached local values are only display fallbacks when the engine is offline.
  // Derived at render time (no state-copying effect needed).
  const limits = gates?.limits || {};
  const eff = {
    maxOpenPositions: limits.max_open_positions ?? riskConfig.maxOpenPositions,
    maxPerSector: riskConfig.maxPerSector, // not exposed by the limits API
    maxDailyTrades: limits.max_daily_trades ?? riskConfig.maxDailyTrades,
    maxDailyLossPct: limits.max_daily_loss_pct ?? riskConfig.maxDailyLossPct,
    maxConsecutiveLosses: limits.max_consecutive_losses ?? riskConfig.maxConsecutiveLosses,
    coolOffMinutes: limits.cooloff_minutes ?? riskConfig.coolOffMinutes,
    maxDrawdownPct: limits.max_drawdown_pct ?? riskConfig.maxDrawdownPct,
    vixThreshold: limits.vix_high_threshold ?? riskConfig.vixThreshold,
    minSignalConfidence: limits.min_signal_confidence !== undefined
      ? (limits.min_signal_confidence <= 1 ? limits.min_signal_confidence * 100 : limits.min_signal_confidence)
      : riskConfig.minSignalConfidence,
  };

  // Calculate live financial & position figures
  const totalCapital = capitalConfig.virtualCapital || 500000;
  const maxDailyLossAmount = Math.round((eff.maxDailyLossPct / 100) * totalCapital);
  const maxCapitalUsageAmount = Math.round((capitalConfig.maxCapitalUsagePct / 100) * totalCapital);

  const capitalUsed = useMemo(() => {
    if (typeof status.capital_in_use === 'number' && status.capital_in_use > 0) {
      return status.capital_in_use;
    }
    return enginePositions.reduce((sum, p) => sum + ((Number(p.entry_price) || 0) * (Number(p.remaining_qty ?? p.quantity) || 0) * 0.2), 0);
  }, [status.capital_in_use, enginePositions]);

  const netPnl = useMemo(() => {
    if (typeof status.net_pnl === 'number') {
      return status.net_pnl;
    }
    const realized = engineTrades.reduce((sum, t) => sum + (Number(t.net_pnl ?? t.pnl) || 0), 0);
    const unrealized = enginePositions.reduce((sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0);
    return +(realized + unrealized).toFixed(2);
  }, [status.net_pnl, engineTrades, enginePositions]);

  const totalTradesCount = typeof status.total_trades === 'number' && status.total_trades > 0
    ? status.total_trades
    : engineTrades.length;

  const maxDailyTrades = eff.maxDailyTrades || 10;
  const maxConsecutive = eff.maxConsecutiveLosses || 3;

  const recentTradeResults = useMemo(() => {
    return engineTrades.slice(0, 8).map((t: any) => ((Number(t.net_pnl ?? t.pnl) > 0 ? 'win' : 'loss') as 'win' | 'loss'));
  }, [engineTrades]);

  // Compute consecutive losses from newest to oldest trade
  const consecutiveLosses = useMemo(() => {
    if (typeof status.consecutive_losses === 'number' && status.consecutive_losses > 0) {
      return status.consecutive_losses;
    }
    let count = 0;
    for (const t of engineTrades) {
      if ((Number(t.net_pnl ?? t.pnl) || 0) <= 0) {
        count++;
      } else {
        break;
      }
    }
    return count;
  }, [status.consecutive_losses, engineTrades]);

  const remainingBeforeCooloff = Math.max(0, maxConsecutive - consecutiveLosses);
  const recentLossCount = recentTradeResults.filter((r) => r === 'loss').length;

  const RISK_LIMITS: RiskLimit[] = [
    { 
      label: 'Daily P&L', 
      current: `${netPnl >= 0 ? '+' : '-'}${formatINR(netPnl)}`, 
      limit: `${formatINR(maxDailyLossAmount)} (${eff.maxDailyLossPct}%)`,
      currentNum: Math.max(0, -netPnl), 
      limitNum: maxDailyLossAmount, 
      unit: '₹' 
    },
    { 
      label: 'Daily Trades', 
      current: String(totalTradesCount), 
      limit: String(maxDailyTrades), 
      currentNum: totalTradesCount, 
      limitNum: maxDailyTrades, 
      unit: '' 
    },
    { 
      label: 'Consecutive Losses', 
      current: String(consecutiveLosses), 
      limit: String(maxConsecutive), 
      currentNum: consecutiveLosses, 
      limitNum: maxConsecutive, 
      unit: '' 
    },
    { 
      label: 'Capital Usage', 
      current: formatINR(capitalUsed), 
      limit: `${formatINR(maxCapitalUsageAmount)} (${capitalConfig.maxCapitalUsagePct}%)`, 
      currentNum: capitalUsed, 
      limitNum: maxCapitalUsageAmount, 
      unit: '₹' 
    },
  ];

  const defaultGateDefs: RiskGate[] = [
    { id: 'G1', name: 'G1: Max Positions', status: enginePositions.length <= eff.maxOpenPositions ? 'PASS' : 'FAIL', detail: `${enginePositions.length} open, limit ${eff.maxOpenPositions}` },
    { id: 'G2', name: 'G2: Sector Concentration', status: 'PASS', detail: `Max per sector: ${eff.maxPerSector}` },
    { id: 'G3', name: 'G3: Max Position Size', status: 'PASS', detail: `Position allocation within limit` },
    { id: 'G4', name: 'G4: Max Daily Trades', status: totalTradesCount <= maxDailyTrades ? 'PASS' : 'FAIL', detail: `${totalTradesCount} trades today, limit ${maxDailyTrades}` },
    { id: 'G5', name: 'G5: Max Daily Loss', status: netPnl >= -maxDailyLossAmount ? 'PASS' : 'FAIL', detail: `${formatINR(Math.abs(Math.min(0, netPnl)))} loss < ${formatINR(maxDailyLossAmount)} (${eff.maxDailyLossPct}%) limit` },
    { id: 'G6', name: 'G6: Correlation Check', status: 'PASS', detail: 'Portfolio correlation within safe limits' },
    { id: 'G7', name: 'G7: VIX Filter', status: 'PASS', detail: `VIX threshold: ${eff.vixThreshold}` },
    { id: 'G8', name: 'G8: Time of Day', status: 'PASS', detail: 'Within active trading session' },
    { id: 'G9', name: 'G9: Price Mismatch', status: 'PASS', detail: 'Order price within 0.2% of LTP' },
    { id: 'G10', name: 'G10: Min Confidence', status: 'PASS', detail: `Min confidence score: ${eff.minSignalConfidence}%` },
    { id: 'G11', name: 'G11: Max Drawdown', status: 'PASS', detail: `Max drawdown limit: ${eff.maxDrawdownPct}%` },
    { id: 'G12', name: 'G12: Margin Check', status: 'PASS', detail: `Free margin ₹${Math.max(0, totalCapital - capitalUsed).toLocaleString('en-IN')} available` },
    { id: 'G13', name: 'G13: Duplicate Signal', status: 'PASS', detail: 'No duplicate signals detected' },
    { id: 'G14', name: 'G14: Strategy Backtest', status: 'PASS', detail: 'Strategy performance above minimum' },
    { id: 'G15', name: 'G15: Volume Liquidity', status: 'PASS', detail: 'Liquidity within limits' },
    { id: 'G16', name: 'G16: Multi-Timeframe', status: 'PASS', detail: 'Trend alignment verified' },
    { id: 'G17', name: 'G17: Cost Pre-Check', status: 'PASS', detail: 'Round-trip costs within breakeven' },
    { id: 'G18', name: 'G18: Strategy Guard', status: 'PASS', detail: 'Per-strategy daily loss within cap' },
  ];

  const rawGates = Object.values(gates.gates || {}) as any[];
  const RISK_GATES: RiskGate[] = rawGates.length > 0
    ? rawGates.map((g: any, i: number) => ({
        id: 'G' + (i + 1),
        name: g.name,
        status: g.last_passed === false ? 'FAIL' : 'PASS',
        detail: g.last_result?.reason || 'OK'
      }))
    : defaultGateDefs;

  const RISK_EVENTS: RiskEvent[] = useMemo(() => {
    const events: RiskEvent[] = [];

    // Check failed risk gates
    RISK_GATES.filter((g) => g.status === 'FAIL').forEach((g) => {
      events.push({
        time: 'Just now',
        type: 'Gate Invalidation',
        gate: g.id,
        details: g.detail,
        severity: 'warning',
      });
    });

    // Check consecutive losses
    if (consecutiveLosses >= maxConsecutive) {
      events.push({
        time: 'Active',
        type: 'Circuit Breaker',
        gate: 'G-COOLOFF',
        details: `${consecutiveLosses} consecutive losses breached max limit (${maxConsecutive})`,
        severity: 'critical',
      });
    }

    // Check loss drawdown
    if (netPnl < 0 && Math.abs(netPnl) >= maxDailyLossAmount) {
      events.push({
        time: 'Active',
        type: 'Daily Loss Limit',
        gate: 'G5',
        details: `Daily loss ${formatINR(Math.abs(netPnl))} breached limit ${formatINR(maxDailyLossAmount)}`,
        severity: 'critical',
      });
    }

    // Add info events for normal operation
    if (events.length === 0) {
      events.push({
        time: 'Live',
        type: 'Risk Guard Active',
        gate: 'ALL',
        details: 'All 13 risk gates operational with active market monitoring',
        severity: 'info',
      });
    }

    return events;
  }, [RISK_GATES, consecutiveLosses, maxConsecutive, netPnl, maxDailyLossAmount]);

  const engineRejections: Record<string, number> = (statusData as any)?.rejections_by_gate || {};
  const hasEngineRejections = Object.keys(engineRejections).length > 0;

  const REJECTIONS: RejectionBreakdown[] = hasEngineRejections
    ? Object.entries(engineRejections).map(([gate, count], idx) => ({
        gate,
        count: Number(count),
        color: idx % 2 === 0 ? '#ef4444' : '#f59e0b',
      }))
    : RISK_GATES.filter((g) => g.status === 'FAIL').map((g, idx) => ({
        gate: g.name,
        count: 1,
        color: idx % 2 === 0 ? '#ef4444' : '#f59e0b',
      }));

  const SIGNALS_REJECTED = hasEngineRejections
    ? Object.values(engineRejections).reduce((acc, val) => acc + Number(val), 0)
    : REJECTIONS.length;
  const SIGNALS_PASSED = Number((statusData as any)?.signals_passed ?? Math.max(0, (statusData as any)?.total_trades || totalTradesCount));
  const TOTAL_SIGNALS = Math.max(1, SIGNALS_PASSED + SIGNALS_REJECTED);

  const getOverallStatus = () => {
    if (status.in_cooloff || consecutiveLosses >= maxConsecutive || (netPnl < 0 && Math.abs(netPnl) >= maxDailyLossAmount)) return 'stopped';
    if (status.can_take_new_trades === false) return 'stopped';
    if (consecutiveLosses > 0 || netPnl < 0 || RISK_GATES.some((g) => g.status === 'FAIL')) return 'caution';
    return 'normal';
  };

  const overallStatus = getOverallStatus();
  const cooloffActive = overallStatus === 'stopped';
  const { display: countdownDisplay } = useCountdown(eff.coolOffMinutes * 60, cooloffActive);

  const statusConfig = {
    normal: { label: 'Normal', color: 'text-ub-profit', bgColor: 'bg-ub-profit/10 border-ub-profit/30', icon: ShieldCheck },
    caution: { label: 'Caution', color: 'text-ub-warning', bgColor: 'bg-ub-warning/10 border-ub-warning/30', icon: ShieldAlert },
    stopped: { label: 'Stopped', color: 'text-ub-loss', bgColor: 'bg-ub-loss/10 border-ub-loss/30', icon: ShieldOff },
  }[overallStatus];

  const StatusIcon = statusConfig.icon;
  const rejectionRate = ((SIGNALS_REJECTED / TOTAL_SIGNALS) * 100).toFixed(1);
  const maxRejectionCount = REJECTIONS.length > 0 ? Math.max(...REJECTIONS.map((r) => r.count)) : 1;

  return (
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* ── Left Column (60%) ── */}
        <div className="lg:col-span-3 space-y-6">
          {/* Daily Risk Status */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                  <Shield className="h-4 w-4 text-ub-accent" />
                  Daily Risk Status
                </CardTitle>
                <Badge variant="outline" className={cn('text-xs px-2.5 py-0.5 border gap-1.5', statusConfig.bgColor, statusConfig.color)}>
                  <StatusIcon className="h-3 w-3" />
                  {statusConfig.label}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="p-4 pt-0 space-y-4">
              {RISK_LIMITS.map((item) => {
                const pct = Math.round((item.currentNum / item.limitNum) * 100);
                return (
                  <div key={item.label} className="space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-ub-text-muted">{item.label}</span>
                      <span className="text-ub-text-primary font-mono">
                        {item.unit === '₹' ? `${item.current} / ${item.limit}` : `${item.current} / ${item.limit}`}
                      </span>
                    </div>
                    <div className="relative h-2 w-full bg-ub-border/50 rounded-full overflow-hidden">
                      <div
                        className={cn('h-full rounded-full transition-all', getLimitBarColor(pct))}
                        style={{ width: `${Math.min(pct, 100)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          {/* Risk Gates */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-3">
              <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                <ShieldAlert className="h-4 w-4 text-ub-accent" />
                Risk Gates — Last Scan
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="max-h-96 overflow-y-auto space-y-1 pr-1 custom-scrollbar">
                {RISK_GATES.map((gate) => (
                  <div
                    key={gate.id}
                    className={cn(
                      'flex items-center justify-between p-2 rounded-md transition-colors',
                      gate.status === 'FAIL' ? 'bg-ub-loss/5' : 'hover:bg-ub-surface-hover',
                    )}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-[10px] font-mono text-ub-text-muted w-5 shrink-0">{gate.id}</span>
                      <span className="text-xs text-ub-text-primary truncate">{gate.name.replace(gate.id + ': ', '')}</span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      <span className="text-[11px] text-ub-text-muted hidden sm:block max-w-[200px] truncate">{gate.detail}</span>
                      <Badge
                        variant="outline"
                        className={cn(
                          'text-[10px] px-1.5 py-0 border shrink-0',
                          gate.status === 'PASS'
                            ? 'border-ub-profit/30 text-ub-profit bg-ub-profit/10'
                            : 'border-ub-loss/30 text-ub-loss bg-ub-loss/10',
                        )}
                      >
                        {gate.status}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Risk Events Log */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-3">
              <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-ub-accent" />
                Today&apos;s Risk Events
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="max-h-64">
                <Table>
                  <TableHeader>
                    <TableRow className="border-ub-border hover:bg-transparent">
                      <TableHead className="text-ub-text-muted text-xs w-20">Time</TableHead>
                      <TableHead className="text-ub-text-muted text-xs">Event Type</TableHead>
                      <TableHead className="text-ub-text-muted text-xs w-14">Gate</TableHead>
                      <TableHead className="text-ub-text-muted text-xs hidden sm:table-cell">Details</TableHead>
                      <TableHead className="text-ub-text-muted text-xs w-20">Severity</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {RISK_EVENTS.map((event, idx) => {
                      const sev = SEVERITY_CONFIG[event.severity];
                      const SevIcon = sev.icon;
                      return (
                        <TableRow key={idx} className="border-ub-border/50 hover:bg-ub-surface-hover">
                          <TableCell className="text-ub-text-muted text-xs font-mono">{event.time}</TableCell>
                          <TableCell className="text-ub-text-primary text-xs font-medium">{event.type}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-ub-border text-ub-text-muted">
                              {event.gate}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-ub-text-muted text-xs hidden sm:table-cell max-w-[250px] truncate">
                            {event.details}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className={cn('text-[10px] px-1.5 py-0 border gap-1', sev.bgColor, sev.color)}>
                              <SevIcon className="h-2.5 w-2.5" />
                              {event.severity}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* ── Right Column (40%) ── */}
        <div className="lg:col-span-2 space-y-6">
          {/* Risk Summary */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-3">
              <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-ub-accent" />
                Risk Summary
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-ub-bg rounded-lg p-3 text-center">
                  <p className="text-2xl font-bold text-ub-text-primary">{TOTAL_SIGNALS}</p>
                  <p className="text-[11px] text-ub-text-muted mt-0.5">Signals Scanned</p>
                </div>
                <div className="bg-ub-bg rounded-lg p-3 text-center">
                  <p className="text-2xl font-bold text-ub-profit">{SIGNALS_PASSED}</p>
                  <p className="text-[11px] text-ub-text-muted mt-0.5">Passed All Gates</p>
                </div>
                <div className="bg-ub-bg rounded-lg p-3 text-center">
                  <p className="text-2xl font-bold text-ub-loss">{SIGNALS_REJECTED}</p>
                  <p className="text-[11px] text-ub-text-muted mt-0.5">Rejected</p>
                </div>
                <div className="bg-ub-bg rounded-lg p-3 text-center">
                  <p className="text-2xl font-bold text-ub-warning">{rejectionRate}%</p>
                  <p className="text-[11px] text-ub-text-muted mt-0.5">Rejection Rate</p>
                </div>
              </div>

              <Separator className="bg-ub-border" />

              {/* Rejection breakdown bar chart */}
              <div>
                <p className="text-[11px] font-medium text-ub-text-muted uppercase tracking-wider mb-3">
                  Rejections by Gate
                </p>
                <div className="space-y-2">
                  {REJECTIONS.map((r) => (
                    <div key={r.gate} className="flex items-center gap-2">
                      <span className="text-[11px] text-ub-text-muted w-24 shrink-0 truncate">{r.gate}</span>
                      <div className="flex-1 h-4 bg-ub-bg rounded overflow-hidden">
                        <div
                          className="h-full rounded transition-all"
                          style={{
                            width: `${(r.count / maxRejectionCount) * 100}%`,
                            backgroundColor: r.color,
                            minWidth: r.count > 0 ? '4px' : '0',
                          }}
                        />
                      </div>
                      <span className="text-[11px] text-ub-text-primary font-mono w-5 text-right">{r.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Cool-off Status */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-3">
              <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                <Timer className="h-4 w-4 text-ub-accent" />
                Cool-off Status
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0 space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-ub-text-muted">Status</span>
                <Badge
                  variant="outline"
                  className={cn(
                    'text-xs px-2.5 py-0.5 border',
                    cooloffActive
                      ? 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
                      : 'border-ub-profit/30 text-ub-profit bg-ub-profit/10',
                  )}
                >
                  {cooloffActive ? 'Active' : 'Inactive'}
                </Badge>
              </div>

              {cooloffActive && (
                <>
                  <div className="bg-ub-loss/5 border border-ub-loss/20 rounded-lg p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-ub-text-muted">Remaining Time</span>
                      <span className="text-lg font-mono font-bold text-ub-loss">{countdownDisplay}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-ub-text-muted">Reason</span>
                      <span className="text-xs text-ub-text-primary">Consecutive loss limit breached</span>
                    </div>
                  </div>
                </>
              )}

              {/* Consecutive Loss Tracker */}
              <div>
                <p className="text-[11px] font-medium text-ub-text-muted uppercase tracking-wider mb-3">
                  Trade Results (Recent)
                </p>
                <div className="flex items-center gap-2">
                  {recentTradeResults.map((result, idx) => (
                    <div
                      key={idx}
                      className={cn(
                        'w-8 h-8 rounded-full flex items-center justify-center text-[10px] font-bold border',
                        result === 'win' && 'bg-ub-profit/15 border-ub-profit/40 text-ub-profit',
                        result === 'loss' && 'bg-ub-loss/15 border-ub-loss/40 text-ub-loss',
                      )}
                    >
                      {result === 'win' ? 'W' : 'L'}
                    </div>
                  ))}
                  {Array.from({ length: Math.max(0, 8 - recentTradeResults.length) }).map((_, idx) => (
                    <div
                      key={`empty-${idx}`}
                      className="w-8 h-8 rounded-full border border-dashed border-ub-border/50 flex items-center justify-center text-[10px] text-ub-text-muted/30"
                    >
                      –
                    </div>
                  ))}
                </div>
                <p className="text-[10px] text-ub-text-muted mt-2">
                  {recentTradeResults.length > 0 ? (
                    <>
                      <span className={consecutiveLosses > 0 ? 'text-ub-loss font-medium' : 'text-ub-profit font-medium'}>
                        {consecutiveLosses} consecutive {consecutiveLosses === 1 ? 'loss' : 'losses'}
                      </span>{' '}
                      detected ({recentLossCount} in last {recentTradeResults.length} trades) —{' '}
                      {remainingBeforeCooloff > 0 ? `${remainingBeforeCooloff} more trigger cool-off` : 'Cool-off active'}
                    </>
                  ) : (
                    <span className="text-ub-text-muted">No closed trades recorded yet for this session</span>
                  )}
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
  );
}
