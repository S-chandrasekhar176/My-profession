import { MarketRegime } from '@/lib/store';

export interface Position {
  id: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  entry: number;
  current: number;
  qty: number;
  pnl: number;
  bookedLevels: number[];
}

export interface Trade {
  id: string;
  time: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  pnl: number;
}

export interface DashboardData {
  todayPnl: number;
  todayPnlPercent: number;
  activePositions: number;
  longCount: number;
  shortCount: number;
  winRate: number;
  totalTradesCount: number;
  winningTradesCount: number;
  todayWinRate: number;
  todayTradesCount: number;
  todayWinningTradesCount: number;
  hasTradeHistory: boolean;
  riskUsed: number;
  totalCapital: number;
  capitalUsed: number;
  freeCapital: number;
  dayPnl: number;
  totalPnl: number;
  positions: Position[];
  recentTrades: Trade[];
  engineStatus: string;
  engineMode: string;
  regime: MarketRegime;
  regimeConfidence: number;
  activeStrategies: string[];
  signalsGenerated: number;
  signalsConfirmed: number;
  signalsSkipped: number;
}

export function formatINR(value: number): string {
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  const parts = abs.toFixed(2).split('.');
  const intPart = parts[0];
  const decPart = parts[1];
  let formatted = '';
  if (intPart.length <= 3) {
    formatted = intPart;
  } else {
    const last3 = intPart.slice(-3);
    const rest = intPart.slice(0, -3);
    formatted = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
  }
  return `₹${sign}${formatted}.${decPart}`;
}

export function formatPercent(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function getRiskColor(pct: number): string {
  if (pct < 50) return '#22c55e';
  if (pct <= 80) return '#f59e0b';
  return '#ef4444';
}

export function getRiskLabel(pct: number): string {
  if (pct < 50) return 'Low';
  if (pct <= 80) return 'Medium';
  return 'High';
}

export const REGIME_CONFIG: Record<MarketRegime, { label: string; colorClass: string; bgClass: string; borderClass: string }> = {
  bull: {
    label: 'Bull',
    colorClass: 'text-ub-bull',
    bgClass: 'bg-ub-bull/15',
    borderClass: 'border-ub-bull/30',
  },
  bear: {
    label: 'Bear',
    colorClass: 'text-ub-bear',
    bgClass: 'bg-ub-bear/15',
    borderClass: 'border-ub-bear/30',
  },
  sideways: {
    label: 'Sideways',
    colorClass: 'text-ub-sideways',
    bgClass: 'bg-ub-sideways/15',
    borderClass: 'border-ub-sideways/30',
  },
  volatile: {
    label: 'Volatile',
    colorClass: 'text-ub-volatile',
    bgClass: 'bg-ub-volatile/15',
    borderClass: 'border-ub-volatile/30',
  },
};
