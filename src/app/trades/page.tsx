'use client';

import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
  ArrowUpDown,
  ArrowLeftRight,
  TrendingUp,
  TrendingDown,
  Download,
  Filter,
  RotateCcw,
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  Wifi,
  Target,
  Pencil,
  XCircle,
  Search,
  Trash2,
} from 'lucide-react';
import { usePositions, useTrades } from '@/hooks/useApi';
import { clearAllPaperData } from '@/lib/tradeExecution';
import { modifyPositionStopLoss, modifyPositionTarget } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { TradingViewChartModal, type ChartTradeData } from '@/components/chart/TradingViewChartModal';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { format, parseISO } from 'date-fns';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

type Direction = 'BUY' | 'SELL';

interface BookedLevel {
  level: number;
  achieved: boolean;
}

interface Position {
  id: string;
  symbol: string;
  direction: Direction;
  entry: number;
  current: number;
  quantity: number;
  remainingQty: number;
  stopLoss: number;
  target: number;
  unrealizedPnl: number;
  bookedLevels: BookedLevel[];
  strategy: string;
}

interface HistoricalTrade {
  id: string;
  time: string;
  symbol: string;
  direction: Direction;
  strategy: string;
  entry: number;
  exit: number;
  quantity: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  duration: string;
  exitReason: string;
}

// ─────────────────────────────────────────────
// Strategy List
// ─────────────────────────────────────────────

const STRATEGIES = [
  'All Strategies',
  'Momentum Breakout',
  'Mean Reversion',
  'VWAP Bounce',
  'Supertrend Follow',
  'RSI Divergence',
  'Gap Fill',
  'Opening Range',
  'Volume Profile',
  'Support Resistance',
  'EMA Crossover',
  'Bollinger Squeeze',
  'Ichimoku Cloud',
  'Order Block',
  'Fibonacci Retrace',
];

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

const INR = (n: number) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);

const INR_SHORT = (n: number) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(n);

// ─────────────────────────────────────────────
// Positions Tab Component
// ─────────────────────────────────────────────

function PositionsTab({
  resetSignal,
  onOpenChart,
}: {
  resetSignal: number;
  onOpenChart?: (trade: ChartTradeData) => void;
}) {
  const { data: positionsData, closeAsync, isClosing } = usePositions();
  const [positions, setPositions] = useState<Position[]>([]);
  const [closeDialogId, setCloseDialogId] = useState<string | null>(null);
  const [modifyDialog, setModifyDialog] = useState<{
    open: boolean;
    id: string;
    field: 'stopLoss' | 'target';
  }>({ open: false, id: '', field: 'stopLoss' });
  const [modifyValue, setModifyValue] = useState('');

  const apiPositionsRef = useRef(positionsData);
  apiPositionsRef.current = positionsData;

  const positionsRef = useRef(positions);
  positionsRef.current = positions;

  // Map an engine API position to the UI shape. The ENGINE database is the
  // single source of truth — no local ledger, no fabricated fields.
  const mapApiPosition = useCallback((p: any) => {
    // remaining_qty can be 0 while nothing is booked (legacy rows) — fall
    // back to quantity minus booked so the UI never shows a phantom 0 size.
    const rawRemaining = Number(p.remaining_qty ?? p.remaining_quantity);
    const quantity = Number(p.quantity) || 0;
    const booked = Number(p.booked_qty) || 0;
    const remainingQty = (Number.isFinite(rawRemaining) && rawRemaining > 0)
      ? rawRemaining
      : Math.max(0, quantity - booked) || quantity;
    const bookedLevel = Number(p.partial_book_level ?? 0) || 0;
    const dir = String(p.direction || '').toUpperCase();
    const direction: Direction = dir === 'LONG' || dir === 'BUY' ? 'BUY' : 'SELL';
    return {
      id: p.id || p.position_id,
      symbol: p.symbol,
      direction,
      entry: p.entry_price || p.entry || 0,
      current: p.current_price || p.current || p.entry_price || 0,
      quantity,
      remainingQty,
      stopLoss: p.current_trailing_sl || p.stop_loss || p.stopLoss || 0,
      target: p.target,
      unrealizedPnl: p.unrealized_pnl || 0,
      bookedLevels: [1, 2, 3].map((lvl) => ({ level: lvl, achieved: lvl <= bookedLevel })),
      strategy: p.strategy || '—',
    };
  }, []);

  // Stable position loader — re-runs whenever the engine API refetches
  const refreshPositions = useCallback(() => {
    const apiPositions = apiPositionsRef.current;
    if (Array.isArray(apiPositions) && apiPositions.length > 0) {
      setPositions(apiPositions.map(mapApiPosition));
    } else {
      setPositions([]);
    }
  }, [mapApiPosition]);

  useEffect(() => {
    refreshPositions();
  }, [resetSignal, positionsData, refreshPositions]);

  // Real-time live quotes polling — DISPLAY ONLY overlay (current price +
  // unrealized P&L). Never writes anywhere; the engine ledger (positions,
  // bookings, square-offs) is managed server-side.
  useEffect(() => {
    const pollQuotes = async () => {
      const current = positionsRef.current;
      if (current.length === 0) return;
      const symbols = Array.from(new Set(current.map((p) => p.symbol)));
      try {
        const res = await fetch(`/api/live-quotes?symbols=${symbols.join(',')}`);
        if (res.ok) {
          const json = await res.json();
          if (json.success && json.data) {
            const quotes = json.data;
            setPositions((prev) =>
              prev.map((pos) => {
                const live = quotes[pos.symbol];
                if (!live || !(live.price > 0)) return pos;
                const cur = live.price;
                const pnl = pos.direction === 'BUY'
                  ? (cur - pos.entry) * pos.remainingQty
                  : (pos.entry - cur) * pos.remainingQty;
                if (cur === pos.current) return pos;
                return { ...pos, current: cur, unrealizedPnl: +pnl.toFixed(2) };
              })
            );
          }
        }
      } catch { }
    };
    pollQuotes();
    const interval = setInterval(pollQuotes, 4000);
    return () => clearInterval(interval);
  }, []);

  const totalUnrealizedPnl = useMemo(
    () => positions.reduce((sum, p) => sum + p.unrealizedPnl, 0),
    [positions]
  );

  const totalInvested = useMemo(
    () => positions.reduce((sum, p) => sum + p.entry * p.quantity, 0),
    [positions]
  );

  const handleClosePosition = async (id: string) => {
    const pos = positions.find((p) => p.id === id);
    setCloseDialogId(null);
    try {
      await closeAsync({ id, payload: { exit_price: pos?.current, exit_reason: 'MANUAL' } });
      toast.success(`Position ${pos?.symbol || ''} closed via engine — archived in Trade History.`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Close failed';
      toast.error(`Failed to close ${pos?.symbol || 'position'}: ${detail}`);
    }
    refreshPositions();
  };

  const handleModify = async () => {
    const val = parseFloat(modifyValue);
    if (isNaN(val) || val <= 0) return;
    const { id, field } = modifyDialog;
    const pos = positions.find((p) => p.id === id);
    setModifyDialog({ open: false, id: '', field: 'stopLoss' });
    setModifyValue('');
    try {
      if (field === 'stopLoss') {
        await modifyPositionStopLoss(id, val);
      } else {
        await modifyPositionTarget(id, val);
      }
      toast.success(`${pos?.symbol} ${field === 'stopLoss' ? 'Stop Loss' : 'Target'} updated to ${INR(val)} (engine)`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || 'Update failed');
    }
    refreshPositions();
  };

  const openModify = (id: string, field: 'stopLoss' | 'target') => {
    const pos = positions.find((p) => p.id === id);
    setModifyValue(pos ? String(pos[field]) : '');
    setModifyDialog({ open: true, id, field });
  };

  return (
    <div className="space-y-4">
      {/* Summary Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div className="h-9 w-9 rounded-lg bg-ub-accent/10 flex items-center justify-center">
              <ArrowLeftRight className="h-4.5 w-4.5 text-ub-accent" />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-ub-text-muted">Total Positions</p>
              <p className="text-xl font-bold text-ub-text-primary">{positions.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div
              className={`h-9 w-9 rounded-lg flex items-center justify-center ${totalUnrealizedPnl >= 0 ? 'bg-ub-profit/10' : 'bg-ub-loss/10'
                }`}
            >
              {totalUnrealizedPnl >= 0 ? (
                <TrendingUp className="h-4.5 w-4.5 text-ub-profit" />
              ) : (
                <TrendingDown className="h-4.5 w-4.5 text-ub-loss" />
              )}
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-ub-text-muted">Unrealized P&L</p>
              <p
                className={`text-xl font-bold ${totalUnrealizedPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'
                  }`}
              >
                {INR(totalUnrealizedPnl)}
              </p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div className="h-9 w-9 rounded-lg bg-ub-warning/10 flex items-center justify-center">
              <Target className="h-4.5 w-4.5 text-ub-warning" />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-ub-text-muted">Total Invested</p>
              <p className="text-xl font-bold text-ub-text-primary">{INR_SHORT(totalInvested)}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* WS Hint */}
      <div className="flex items-center gap-2 text-xs text-ub-text-muted">
        <Wifi className="h-3 w-3 text-ub-accent" />
        <span>Prices update in real-time via WebSocket</span>
      </div>

      {/* Positions Table */}
      {positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="h-16 w-16 rounded-full bg-ub-surface border border-ub-border flex items-center justify-center mb-4">
            <ArrowLeftRight className="h-7 w-7 text-ub-text-muted" />
          </div>
          <h3 className="text-lg font-semibold text-ub-text-primary mb-1">No open positions</h3>
          <p className="text-sm text-ub-text-muted">
            Your confirmed trades will appear here once executed.
          </p>
        </div>
      ) : (
        <Card className="bg-ub-surface border-ub-border">
          <ScrollArea className="max-h-[600px]">
            <Table>
              <TableHeader>
                <TableRow className="border-ub-border hover:bg-transparent">
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Symbol</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Dir</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Entry</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Current</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Qty</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Remain</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">SL</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Target</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">P&L</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Booked</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map((pos) => (
                  <TableRow
                    key={pos.id}
                    className="border-ub-border/50 hover:bg-ub-surface-hover transition-colors"
                  >
                    <TableCell className="font-bold text-ub-text-primary">{pos.symbol}</TableCell>
                    <TableCell>
                      <Badge
                        className={`text-[10px] font-semibold px-1.5 py-0 h-5 ${pos.direction === 'BUY'
                            ? 'bg-ub-profit/15 text-ub-profit border-ub-profit/30'
                            : 'bg-ub-loss/15 text-ub-loss border-ub-loss/30'
                          }`}
                        variant="outline"
                      >
                        {pos.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-primary">
                      {INR(pos.entry)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-accent">
                      {INR(pos.current)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-primary">
                      {pos.quantity}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-muted">
                      {pos.remainingQty}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-loss">
                      {INR(pos.stopLoss)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-profit">
                      {INR(pos.target)}
                    </TableCell>
                    <TableCell
                      className={`text-right font-mono text-sm font-semibold ${pos.unrealizedPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'
                        }`}
                    >
                      {pos.unrealizedPnl >= 0 ? '+' : ''}
                      {INR(pos.unrealizedPnl)}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {pos.bookedLevels.map((bl, idx) => (
                          <span
                            key={idx}
                            className={`text-[10px] font-mono font-medium ${bl.achieved ? 'text-ub-profit' : 'text-ub-text-muted'
                              }`}
                          >
                            L{idx + 1}
                            {bl.achieved ? <Check className="inline h-2.5 w-2.5 ml-0.5" /> : null}
                          </span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-[11px] border-cyan-500/40 bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 font-semibold"
                          onClick={() =>
                            onOpenChart?.({
                              symbol: pos.symbol,
                              direction: pos.direction,
                              entry: pos.entry,
                              stopLoss: pos.stopLoss,
                              target: pos.target,
                              quantity: pos.remainingQty,
                              pnl: pos.unrealizedPnl,
                              strategy: pos.strategy || 'Intraday Strategy',
                            })
                          }
                          title="Open real-time interactive candlestick chart"
                        >
                          <TrendingUp className="h-3 w-3 mr-1" />
                          Chart
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 text-xs text-ub-text-muted hover:text-ub-text-primary"
                            >
                              Actions
                            </Button>
                          </DropdownMenuTrigger>
                        <DropdownMenuContent className="bg-ub-surface border-ub-border" align="end">
                          <DropdownMenuItem
                            className="text-ub-text-primary focus:bg-ub-surface-hover focus:text-ub-text-primary"
                            onClick={() => openModify(pos.id, 'stopLoss')}
                          >
                            <Pencil className="h-3.5 w-3.5 mr-2" />
                            Modify SL
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-ub-text-primary focus:bg-ub-surface-hover focus:text-ub-text-primary"
                            onClick={() => openModify(pos.id, 'target')}
                          >
                            <Pencil className="h-3.5 w-3.5 mr-2" />
                            Modify Target
                          </DropdownMenuItem>
                          <DropdownMenuSeparator className="bg-ub-border" />
                          <DropdownMenuItem
                            className="text-ub-loss focus:bg-ub-loss/10 focus:text-ub-loss"
                            onClick={() => setCloseDialogId(pos.id)}
                          >
                            <XCircle className="h-3.5 w-3.5 mr-2" />
                            Close Position
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </TableCell>
                </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollArea>
        </Card>
      )}

      {/* Close Position Alert Dialog */}
      <AlertDialog open={!!closeDialogId} onOpenChange={(open) => !open && setCloseDialogId(null)}>
        <AlertDialogContent className="bg-ub-surface border-ub-border">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-ub-text-primary">Close Position?</AlertDialogTitle>
            <AlertDialogDescription className="text-ub-text-muted">
              Are you sure you want to close your{' '}
              <span className="text-ub-text-primary font-semibold">
                {positions.find((p) => p.id === closeDialogId)?.symbol}
              </span>{' '}
              position? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="py-2">
            <div className="flex justify-between p-2 rounded bg-ub-background border border-ub-border/50 text-sm">
              <span className="text-ub-text-muted">Unrealized P&L</span>
              <span
                className={`font-semibold font-mono ${(positions.find((p) => p.id === closeDialogId)?.unrealizedPnl ?? 0) >= 0
                    ? 'text-ub-profit'
                    : 'text-ub-loss'
                  }`}
              >
                {INR(positions.find((p) => p.id === closeDialogId)?.unrealizedPnl ?? 0)}
              </span>
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-ub-border text-ub-text-muted hover:text-ub-text-primary">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-ub-loss hover:bg-ub-loss/90 text-white"
              onClick={() => closeDialogId && handleClosePosition(closeDialogId)}
            >
              Close Position
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Modify SL/Target Dialog */}
      <Dialog
        open={modifyDialog.open}
        onOpenChange={(open) => !open && setModifyDialog({ ...modifyDialog, open: false })}
      >
        <DialogContent className="bg-ub-surface border-ub-border max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-ub-text-primary">
              Modify {modifyDialog.field === 'stopLoss' ? 'Stop Loss' : 'Target'}
            </DialogTitle>
            <DialogDescription className="text-ub-text-muted">
              Update the {modifyDialog.field === 'stopLoss' ? 'stop loss' : 'target'} for{' '}
              <span className="text-ub-text-primary font-semibold">
                {positions.find((p) => p.id === modifyDialog.id)?.symbol}
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              type="number"
              step="0.05"
              value={modifyValue}
              onChange={(e) => setModifyValue(e.target.value)}
              className="bg-ub-background border-ub-border text-ub-text-primary font-mono"
              placeholder="Enter new value"
            />
          </div>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              className="border-ub-border text-ub-text-muted"
              onClick={() => setModifyDialog({ ...modifyDialog, open: false })}
            >
              Cancel
            </Button>
            <Button
              className="bg-ub-accent hover:bg-ub-accent-hover text-ub-background font-semibold"
              onClick={handleModify}
            >
              Update
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
}

// ─────────────────────────────────────────────
// Trade History Tab Component
// ─────────────────────────────────────────────

const ITEMS_PER_PAGE = 20;

type ResultFilter = 'all' | 'win' | 'loss';

function HistoryTab({
  resetSignal,
  onOpenChart,
}: {
  resetSignal: number;
  onOpenChart?: (trade: ChartTradeData) => void;
}) {
  const { data: tradesData, isLoading: isLoadingTrades } = useTrades();
  const isLoading = isLoadingTrades;
  const [page, setPage] = useState(1);

  // Engine API is the single source of truth for trade history —
  // no local localStorage trade ledger.
  const trades = useMemo(() => {
    const apiMapped: HistoricalTrade[] = Array.isArray(tradesData)
      ? tradesData.map((t: any) => ({
        id: t.trade_id || t.id,
        time: t.exit_time || t.entry_time || t.created_at || new Date().toISOString(),
        symbol: t.symbol,
        direction: t.direction === 'LONG' ? 'BUY' : t.direction === 'SHORT' ? 'SELL' : t.direction,
        strategy: t.strategy || '—',
        entry: t.entry_price || t.entry,
        exit: t.exit_price || t.exit || t.entry_price,
        quantity: t.quantity,
        grossPnl: t.pnl || 0,
        fees: (t.fees || 0) + (t.brokerage || 0),
        netPnl: t.net_pnl || 0,
        duration: t.holding_duration || 'Intraday',
        exitReason: t.exit_reason || 'MANUAL',
      }))
      : [];

    return apiMapped.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime());
  }, [tradesData]);

  const availableStrategies = useMemo(() => {
    const fromTrades = Array.from(new Set(trades.map((t) => t.strategy).filter(Boolean)));
    return ['All Strategies', ...fromTrades];
  }, [trades]);

  // Filters
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('All Strategies');
  const [symbolFilter, setSymbolFilter] = useState('');
  const [resultFilter, setResultFilter] = useState<ResultFilter>('all');

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (dateFrom) {
        const from = new Date(dateFrom);
        if (new Date(t.time) < from) return false;
      }
      if (dateTo) {
        const to = new Date(dateTo);
        to.setHours(23, 59, 59, 999);
        if (new Date(t.time) > to) return false;
      }
      if (strategyFilter !== 'All Strategies' && t.strategy !== strategyFilter) return false;
      if (symbolFilter && !t.symbol.toLowerCase().includes(symbolFilter.toLowerCase())) return false;
      if (resultFilter === 'win' && t.netPnl < 0) return false;
      if (resultFilter === 'loss' && t.netPnl >= 0) return false;
      return true;
    });
  }, [trades, dateFrom, dateTo, strategyFilter, symbolFilter, resultFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / ITEMS_PER_PAGE));
  const safePage = Math.min(page, totalPages);
  const paginatedTrades = useMemo(
    () => filteredTrades.slice((safePage - 1) * ITEMS_PER_PAGE, safePage * ITEMS_PER_PAGE),
    [filteredTrades, safePage]
  );

  const totals = useMemo(() => {
    const totalTrades = filteredTrades.length;
    const totalPnl = filteredTrades.reduce((sum, t) => sum + t.netPnl, 0);
    const wins = filteredTrades.filter((t) => t.netPnl > 0).length;
    const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
    return { totalTrades, totalPnl, winRate };
  }, [filteredTrades]);

  const handleApplyFilters = () => {
    setPage(1);
    toast.success('Filters applied', {
      description: `${filteredTrades.length} trades match your criteria.`,
    });
  };

  const handleResetFilters = () => {
    setDateFrom('');
    setDateTo('');
    setStrategyFilter('All Strategies');
    setSymbolFilter('');
    setResultFilter('all');
    setPage(1);
    toast.info('Filters reset');
  };

  const handleExportCSV = useCallback(() => {
    const headers = ['Time', 'Symbol', 'Direction', 'Strategy', 'Entry', 'Exit', 'Qty', 'Gross P&L', 'Fees', 'Net P&L', 'Duration', 'Exit Reason'];
    const rows = filteredTrades.map((t) => [
      format(parseISO(t.time), 'yyyy-MM-dd HH:mm'),
      t.symbol,
      t.direction,
      t.strategy,
      t.entry.toFixed(2),
      t.exit.toFixed(2),
      t.quantity,
      t.grossPnl.toFixed(2),
      t.fees.toFixed(2),
      t.netPnl.toFixed(2),
      t.duration,
      t.exitReason,
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `ultrabot-trades-${format(new Date(), 'yyyy-MM-dd')}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('CSV exported', {
      description: `${filteredTrades.length} trades exported.`,
    });
  }, [filteredTrades]);

  return (
    <div className="space-y-4">
      {/* Filter Bar */}
      <Card className="bg-ub-surface border-ub-border">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Filter className="h-4 w-4 text-ub-text-muted" />
            <span className="text-sm font-semibold text-ub-text-primary">Filters</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            <div className="space-y-1">
              <label className="text-[10px] uppercase tracking-wider text-ub-text-muted">From Date</label>
              <Input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="bg-ub-background border-ub-border text-ub-text-primary text-sm h-9"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] uppercase tracking-wider text-ub-text-muted">To Date</label>
              <Input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="bg-ub-background border-ub-border text-ub-text-primary text-sm h-9"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] uppercase tracking-wider text-ub-text-muted">Strategy</label>
              <Select value={strategyFilter} onValueChange={setStrategyFilter}>
                <SelectTrigger className="bg-ub-background border-ub-border text-ub-text-primary text-sm h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-ub-surface border-ub-border">
                  {availableStrategies.map((s) => (
                    <SelectItem key={s} value={s} className="text-ub-text-primary focus:bg-ub-surface-hover focus:text-ub-text-primary">
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] uppercase tracking-wider text-ub-text-muted">Symbol</label>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ub-text-muted" />
                <Input
                  type="text"
                  placeholder="Search symbol..."
                  value={symbolFilter}
                  onChange={(e) => setSymbolFilter(e.target.value)}
                  className="bg-ub-background border-ub-border text-ub-text-primary text-sm h-9 pl-8"
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] uppercase tracking-wider text-ub-text-muted">Result</label>
              <Select value={resultFilter} onValueChange={(v) => setResultFilter(v as ResultFilter)}>
                <SelectTrigger className="bg-ub-background border-ub-border text-ub-text-primary text-sm h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-ub-surface border-ub-border">
                  <SelectItem value="all" className="text-ub-text-primary focus:bg-ub-surface-hover focus:text-ub-text-primary">
                    All
                  </SelectItem>
                  <SelectItem value="win" className="text-ub-profit focus:bg-ub-surface-hover focus:text-ub-profit">
                    Win
                  </SelectItem>
                  <SelectItem value="loss" className="text-ub-loss focus:bg-ub-surface-hover focus:text-ub-loss">
                    Loss
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex items-center gap-2 mt-3">
            <Button
              size="sm"
              className="bg-ub-accent hover:bg-ub-accent-hover text-ub-background font-semibold text-xs h-8 px-4"
              onClick={handleApplyFilters}
            >
              Apply
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="border-ub-border text-ub-text-muted hover:text-ub-text-primary text-xs h-8 px-4"
              onClick={handleResetFilters}
            >
              <RotateCcw className="h-3 w-3 mr-1" />
              Reset
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Trade Table */}
      {isLoading ? (
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex gap-4">
                {Array.from({ length: 10 }).map((_, j) => (
                  <Skeleton key={j} className="h-5 flex-1 bg-ub-surface-active" />
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      ) : filteredTrades.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="h-16 w-16 rounded-full bg-ub-surface border border-ub-border flex items-center justify-center mb-4">
            <Search className="h-7 w-7 text-ub-text-muted" />
          </div>
          <h3 className="text-lg font-semibold text-ub-text-primary mb-1">No trades yet</h3>
          <p className="text-sm text-ub-text-muted">
            {symbolFilter || dateFrom || dateTo || strategyFilter !== 'All Strategies'
              ? 'No trades match your filters. Try adjusting them.'
              : 'Your completed trades will appear here.'}
          </p>
        </div>
      ) : (
        <Card className="bg-ub-surface border-ub-border">
          <ScrollArea className="max-h-[420px]">
            <Table>
              <TableHeader>
                <TableRow className="border-ub-border hover:bg-transparent">
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Time</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Symbol</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Dir</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Strategy</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Entry</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Exit</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Qty</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Gross P&L</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Fees</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Net P&L</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Duration</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider">Exit Reason</TableHead>
                  <TableHead className="text-ub-text-muted text-[11px] uppercase tracking-wider text-right">Chart</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedTrades.map((trade) => (
                  <TableRow
                    key={trade.id}
                    className="border-ub-border/50 hover:bg-ub-surface-hover transition-colors"
                  >
                    <TableCell className="text-xs text-ub-text-muted font-mono whitespace-nowrap">
                      {format(parseISO(trade.time), 'dd MMM HH:mm')}
                    </TableCell>
                    <TableCell className="font-bold text-ub-text-primary text-sm">
                      {trade.symbol}
                    </TableCell>
                    <TableCell>
                      <Badge
                        className={`text-[10px] font-semibold px-1.5 py-0 h-5 ${trade.direction === 'BUY'
                            ? 'bg-ub-profit/15 text-ub-profit border-ub-profit/30'
                            : 'bg-ub-loss/15 text-ub-loss border-ub-loss/30'
                          }`}
                        variant="outline"
                      >
                        {trade.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-ub-text-muted">{trade.strategy}</TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-primary">
                      {INR(trade.entry)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-primary">
                      {INR(trade.exit)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-muted">
                      {trade.quantity}
                    </TableCell>
                    <TableCell
                      className={`text-right font-mono text-sm ${trade.grossPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'
                        }`}
                    >
                      {trade.grossPnl >= 0 ? '+' : ''}
                      {INR(trade.grossPnl)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-ub-text-muted">
                      {INR(trade.fees)}
                    </TableCell>
                    <TableCell
                      className={`text-right font-mono text-sm font-bold ${trade.netPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'
                        }`}
                    >
                      {trade.netPnl >= 0 ? '+' : ''}
                      {INR(trade.netPnl)}
                    </TableCell>
                    <TableCell className="text-xs text-ub-text-muted whitespace-nowrap">
                      {trade.duration}
                    </TableCell>
                    <TableCell>
                      <Badge
                        className={`text-[10px] px-1.5 py-0 h-5 ${trade.exitReason === 'Target Hit'
                            ? 'bg-ub-profit/15 text-ub-profit border-ub-profit/30'
                            : 'bg-ub-loss/15 text-ub-loss border-ub-loss/30'
                          }`}
                        variant="outline"
                      >
                        {trade.exitReason}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6 px-2 text-[10px] border-cyan-500/40 bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 font-semibold"
                        onClick={() =>
                          onOpenChart?.({
                            symbol: trade.symbol,
                            direction: trade.direction,
                            entry: trade.entry,
                            stopLoss: +(trade.entry * (trade.direction === 'BUY' ? 0.985 : 1.015)).toFixed(2),
                            target: trade.exit,
                            quantity: trade.quantity,
                            pnl: trade.netPnl,
                            strategy: trade.strategy || 'Historical Strategy',
                          })
                        }
                        title="View chart with trade entry and exit levels"
                      >
                        <TrendingUp className="h-2.5 w-2.5 mr-1" />
                        Chart
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollArea>

          {/* Footer: Summary + Pagination + Export */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 px-4 py-3 border-t border-ub-border">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <div className="text-ub-text-muted">
                Total Trades:{' '}
                <span className="font-semibold text-ub-text-primary">{totals.totalTrades}</span>
              </div>
              <Separator orientation="vertical" className="h-4 bg-ub-border" />
              <div className="text-ub-text-muted">
                Total P&L:{' '}
                <span
                  className={`font-bold ${totals.totalPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'
                    }`}
                >
                  {totals.totalPnl >= 0 ? '+' : ''}
                  {INR(totals.totalPnl)}
                </span>
              </div>
              <Separator orientation="vertical" className="h-4 bg-ub-border" />
              <div className="text-ub-text-muted">
                Win Rate:{' '}
                <span
                  className={`font-bold ${totals.winRate >= 50 ? 'text-ub-profit' : 'text-ub-loss'
                    }`}
                >
                  {totals.winRate.toFixed(1)}%
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="border-ub-border text-ub-text-muted hover:text-ub-text-primary text-xs h-8"
                onClick={handleExportCSV}
              >
                <Download className="h-3.5 w-3.5 mr-1.5" />
                Export CSV
              </Button>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="border-ub-border text-ub-text-muted hover:text-ub-text-primary h-8 w-8 p-0"
                  onClick={() => setPage(Math.max(1, safePage - 1))}
                  disabled={safePage <= 1}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="text-xs text-ub-text-muted px-2">
                  {safePage} / {totalPages}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-ub-border text-ub-text-muted hover:text-ub-text-primary h-8 w-8 p-0"
                  onClick={() => setPage(Math.min(totalPages, safePage + 1))}
                  disabled={safePage >= totalPages}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────

export default function TradesPage() {
  const [resetSignal, setResetSignal] = useState(0);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [selectedChartTrade, setSelectedChartTrade] = useState<ChartTradeData | null>(null);

  const handleResetPaper = useCallback(() => {
    // Clears the browser-side cache only. The engine database (positions,
    // trades, sessions) is the ledger and is NOT touched from the UI.
    clearAllPaperData();
    setResetSignal((s) => s + 1);
    setResetDialogOpen(false);
    toast.success('Local browser cache cleared — engine ledger reloaded from server.');
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ArrowLeftRight className="h-6 w-6 text-ub-accent" />
          <h1 className="text-2xl font-bold text-ub-text-primary">Trades</h1>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="border-ub-loss/40 text-ub-loss hover:bg-ub-loss/10 hover:text-ub-loss text-xs"
          onClick={() => setResetDialogOpen(true)}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1.5" />
          Clear Local Cache
        </Button>
      </div>

      {/* Reset Confirmation Dialog */}
      <AlertDialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
        <AlertDialogContent className="bg-ub-surface border-ub-border">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-ub-text-primary">Clear Local Browser Cache?</AlertDialogTitle>
            <AlertDialogDescription className="text-ub-text-muted">
              This clears the browser-side cache (session opportunities, confirmed/skipped IDs, legacy local positions). The engine database — real positions and trade history — is untouched and will simply be re-fetched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-ub-border text-ub-text-muted hover:text-ub-text-primary">Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-ub-loss hover:bg-ub-loss/90 text-white"
              onClick={handleResetPaper}
            >
              Yes, Clear Local Cache
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Tabs */}
      <Tabs defaultValue="positions" className="w-full">
        <TabsList className="bg-ub-background border border-ub-border">
          <TabsTrigger
            value="positions"
            className="data-[state=active]:bg-ub-surface-active data-[state=active]:text-ub-text-primary text-ub-text-muted text-sm"
          >
            Open Positions
          </TabsTrigger>
          <TabsTrigger
            value="history"
            className="data-[state=active]:bg-ub-surface-active data-[state=active]:text-ub-text-primary text-ub-text-muted text-sm"
          >
            Trade History
          </TabsTrigger>
        </TabsList>

        <TabsContent value="positions" className="mt-4">
          <PositionsTab
            resetSignal={resetSignal}
            onOpenChart={(trade) => setSelectedChartTrade(trade)}
          />
        </TabsContent>

        <TabsContent value="history" className="mt-4">
          <HistoryTab
            resetSignal={resetSignal}
            onOpenChart={(trade) => setSelectedChartTrade(trade)}
          />
        </TabsContent>
      </Tabs>

      {/* TradingView Real-Time Candlestick Chart Modal */}
      <TradingViewChartModal
        isOpen={!!selectedChartTrade}
        onClose={() => setSelectedChartTrade(null)}
        trade={selectedChartTrade}
      />
    </div>
  );
}
