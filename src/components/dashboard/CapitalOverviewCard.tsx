'use client';

import { BarChart3 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { DashboardData, formatINR } from './DashboardTypes';

export default function CapitalOverviewCard({ data }: { data: DashboardData }) {
  const capitalUsedPct = data.totalCapital > 0 ? (data.capitalUsed / data.totalCapital) * 100 : 0;

  return (
    <Card className="border-ub-border bg-ub-surface md:col-span-1 xl:col-span-3">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-ub-accent" />
          Capital Overview
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <div>
            <p className="text-[11px] text-ub-text-muted uppercase tracking-wider">Total Capital</p>
            <p className="text-sm font-bold text-ub-text-primary font-mono mt-0.5">
              {formatINR(data.totalCapital)}
            </p>
          </div>
          <div>
            <p className="text-[11px] text-ub-text-muted uppercase tracking-wider">Capital Used</p>
            <p className="text-sm font-bold text-ub-warning font-mono mt-0.5">
              {formatINR(data.capitalUsed)}
            </p>
          </div>
          <div>
            <p className="text-[11px] text-ub-text-muted uppercase tracking-wider">Free Capital</p>
            <p className="text-sm font-bold text-ub-profit font-mono mt-0.5">
              {formatINR(data.freeCapital)}
            </p>
          </div>
          <div>
            <p className="text-[11px] text-ub-text-muted uppercase tracking-wider">Day P&L</p>
            <p className={`text-sm font-bold font-mono mt-0.5 ${data.dayPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'}`}>
              {formatINR(data.dayPnl)}
            </p>
          </div>
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-ub-text-muted">Capital Utilization</span>
            <span className="text-ub-text-primary font-medium font-mono">{capitalUsedPct.toFixed(1)}%</span>
          </div>
          <Progress
            value={capitalUsedPct}
            className="h-2 bg-ub-border [&>div]:bg-ub-accent"
          />
        </div>
        <Separator className="my-3 bg-ub-border" />
        <div className="flex items-center justify-between">
          <span className="text-xs text-ub-text-muted">Total P&L (All Time)</span>
          <span className={`text-base font-bold font-mono ${data.totalPnl >= 0 ? 'text-ub-profit' : 'text-ub-loss'}`}>
            {formatINR(data.totalPnl)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
