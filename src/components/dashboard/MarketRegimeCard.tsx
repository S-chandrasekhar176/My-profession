'use client';

import { Radio, TrendingUp, TrendingDown, BarChart3, AlertTriangle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { MarketRegime } from '@/lib/store';
import { REGIME_CONFIG } from './DashboardTypes';

interface MarketRegimeCardProps {
  regime: MarketRegime;
  regimeConfidence: number;
  activeStrategies: string[];
}

export default function MarketRegimeCard({
  regime,
  regimeConfidence,
  activeStrategies,
}: MarketRegimeCardProps) {
  const regimeCfg = REGIME_CONFIG[regime] || REGIME_CONFIG.sideways;

  return (
    <Card className="border-ub-border bg-ub-surface xl:col-span-2">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <Radio className="h-4 w-4 text-ub-accent" />
          Market Regime
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Badge
              variant="outline"
              className={`text-xs font-bold px-3 py-1 ${regimeCfg.colorClass} ${regimeCfg.bgClass} ${regimeCfg.borderClass}`}
            >
              {regime === 'bull' && <TrendingUp className="h-3.5 w-3.5 mr-1" />}
              {regime === 'bear' && <TrendingDown className="h-3.5 w-3.5 mr-1" />}
              {regime === 'sideways' && <BarChart3 className="h-3.5 w-3.5 mr-1" />}
              {regime === 'volatile' && <AlertTriangle className="h-3.5 w-3.5 mr-1" />}
              {regimeCfg.label} Market
            </Badge>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-ub-text-muted">Confidence</span>
              <span className="text-xs font-mono font-bold text-ub-text-primary">{regimeConfidence}%</span>
            </div>
          </div>
          <Progress
            value={regimeConfidence}
            className="h-1.5 bg-ub-border"
          />
          <Separator className="bg-ub-border" />
          <div>
            <p className="text-[11px] text-ub-text-muted mb-2">Active Strategies</p>
            <div className="flex flex-wrap gap-1.5">
              {activeStrategies.map((strategy, idx) => (
                <Badge
                  key={idx}
                  variant="outline"
                  className="text-[10px] font-medium border-ub-accent/20 text-ub-accent bg-ub-accent/5"
                >
                  {strategy}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
