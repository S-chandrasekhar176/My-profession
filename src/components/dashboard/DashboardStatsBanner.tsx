'use client';

import { TrendingUp, TrendingDown, Activity, ShieldAlert } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DashboardData, formatINR, formatPercent, getRiskColor, getRiskLabel } from './DashboardTypes';

function CircularProgress({ value, size = 68, strokeWidth = 5, color = '#00d09c' }: {
  value: number;
  size?: number;
  strokeWidth?: number;
  color?: string;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="#1e293b"
        strokeWidth={strokeWidth}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        className="transition-all duration-700 ease-out"
      />
      <text
        x={size / 2}
        y={size / 2}
        className="fill-ub-text-primary text-xs font-bold"
        textAnchor="middle"
        dominantBaseline="central"
        transform={`rotate(90, ${size / 2}, ${size / 2})`}
      >
        {value.toFixed(0)}%
      </text>
    </svg>
  );
}

function StatCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="border-ub-border bg-ub-surface hover:bg-ub-surface-hover transition-colors">
      <CardContent className="p-4">
        <p className="text-xs font-medium text-ub-text-muted mb-2">{title}</p>
        {children}
      </CardContent>
    </Card>
  );
}

export default function DashboardStatsBanner({ data }: { data: DashboardData }) {
  const pnlIsPositive = data.todayPnl >= 0;
  const pnlColor = data.todayPnl > 0 ? 'text-ub-profit' : data.todayPnl < 0 ? 'text-ub-loss' : 'text-ub-text-muted';
  const riskColor = getRiskColor(data.riskUsed);

  return (
    <>
      {/* Card 1: Today's P&L */}
      <StatCard title="Today's P&L">
        <div className="flex items-center gap-3">
          <div className={`h-10 w-10 rounded-lg flex items-center justify-center ${pnlIsPositive ? 'bg-ub-profit/10' : 'bg-ub-loss/10'}`}>
            {pnlIsPositive ? (
              <TrendingUp className="h-5 w-5 text-ub-profit" />
            ) : (
              <TrendingDown className="h-5 w-5 text-ub-loss" />
            )}
          </div>
          <div>
            <p className={`text-xl font-bold font-mono ${pnlColor}`}>
              {formatINR(data.todayPnl)}
            </p>
            <p className={`text-xs font-medium ${pnlColor}`}>
              {formatPercent(data.todayPnlPercent)}
            </p>
          </div>
        </div>
      </StatCard>

      {/* Card 2: Active Positions */}
      <StatCard title="Active Positions">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-ub-accent/10 flex items-center justify-center">
            <Activity className="h-5 w-5 text-ub-accent" />
          </div>
          <div>
            <p className="text-xl font-bold text-ub-text-primary font-mono">
              {data.activePositions}
            </p>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-ub-profit font-medium">
                {data.longCount} Long
              </span>
              <span className="text-ub-border">|</span>
              <span className="text-xs text-ub-loss font-medium">
                {data.shortCount} Short
              </span>
            </div>
          </div>
        </div>
      </StatCard>

      {/* Card 3: Dynamic Win Rate */}
      <StatCard title="Win Rate">
        <div className="flex items-center gap-4">
          <CircularProgress
            value={data.hasTradeHistory ? data.winRate : 0}
            size={68}
            strokeWidth={5}
            color={!data.hasTradeHistory ? '#64748b' : data.winRate >= 60 ? '#22c55e' : data.winRate >= 40 ? '#f59e0b' : '#ef4444'}
          />
          <div className="flex flex-col">
            <span className="text-xs text-ub-text-muted">
              {data.hasTradeHistory
                ? `${data.winningTradesCount}/${data.totalTradesCount} Won (All-Time)`
                : 'Trades Won'}
            </span>
            <span
              className={`text-sm font-semibold ${
                !data.hasTradeHistory
                  ? 'text-ub-text-muted'
                  : data.winRate >= 60
                  ? 'text-ub-profit'
                  : data.winRate >= 40
                  ? 'text-ub-warning'
                  : 'text-ub-loss'
              }`}
            >
              {!data.hasTradeHistory
                ? 'No Trades Yet'
                : data.winRate >= 60
                ? 'Good'
                : data.winRate >= 40
                ? 'Moderate'
                : 'Needs Tuning'}
            </span>
            {data.todayTradesCount > 0 && (
              <span className="text-[10px] text-cyan-400 font-mono mt-0.5">
                Today: {data.todayWinningTradesCount}/{data.todayTradesCount} ({data.todayWinRate}%)
              </span>
            )}
          </div>
        </div>
      </StatCard>

      {/* Card 4: Risk Used */}
      <StatCard title="Risk Used">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5" style={{ color: riskColor }} />
              <span className="text-xl font-bold font-mono" style={{ color: riskColor }}>
                {data.riskUsed}%
              </span>
            </div>
            <Badge
              variant="outline"
              className="text-[10px] font-semibold"
              style={{
                color: riskColor,
                borderColor: riskColor + '40',
                backgroundColor: riskColor + '15',
              }}
            >
              {getRiskLabel(data.riskUsed)}
            </Badge>
          </div>
          <div className="h-2 w-full rounded-full bg-ub-border overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.min(data.riskUsed, 100)}%`,
                backgroundColor: riskColor,
              }}
            />
          </div>
          <p className="text-[11px] text-ub-text-muted">
            {data.riskUsed < 50
              ? 'Healthy risk utilization'
              : data.riskUsed <= 80
                ? 'Approaching limit — exercise caution'
                : 'High risk — consider reducing exposure'}
          </p>
        </div>
      </StatCard>
    </>
  );
}
