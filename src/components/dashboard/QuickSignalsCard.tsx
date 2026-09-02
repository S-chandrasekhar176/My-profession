'use client';

import { SignalHigh } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface QuickSignalsCardProps {
  signalsGenerated: number;
  signalsConfirmed: number;
  signalsSkipped: number;
}

export default function QuickSignalsCard({
  signalsGenerated,
  signalsConfirmed,
  signalsSkipped,
}: QuickSignalsCardProps) {
  const confirmationRate = signalsGenerated > 0
    ? `${((signalsConfirmed / signalsGenerated) * 100).toFixed(0)}% confirmation rate`
    : 'No signals today';

  return (
    <Card className="border-ub-border bg-ub-surface xl:col-span-2">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <SignalHigh className="h-4 w-4 text-ub-accent" />
          Quick Signals
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="grid grid-cols-3 gap-3">
          <div className="text-center p-2 rounded-lg bg-ub-background/50">
            <p className="text-lg font-bold font-mono text-ub-accent">{signalsGenerated}</p>
            <p className="text-[10px] text-ub-text-muted mt-0.5">Generated</p>
          </div>
          <div className="text-center p-2 rounded-lg bg-ub-background/50">
            <p className="text-lg font-bold font-mono text-ub-profit">{signalsConfirmed}</p>
            <p className="text-[10px] text-ub-text-muted mt-0.5">Confirmed</p>
          </div>
          <div className="text-center p-2 rounded-lg bg-ub-background/50">
            <p className="text-lg font-bold font-mono text-ub-warning">{signalsSkipped}</p>
            <p className="text-[10px] text-ub-text-muted mt-0.5">Skipped</p>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-1.5 text-[11px] text-ub-text-muted">
          <SignalHigh className="h-3 w-3" />
          <span>{confirmationRate}</span>
        </div>
      </CardContent>
    </Card>
  );
}
