'use client';

import { useState, useEffect } from 'react';
import { Zap, Wifi, Timer, AlertTriangle, Power, Square } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { BROKER_LIST, useStore } from '@/lib/store';

function UptimeTicker({ startedAt }: { startedAt: number | null }) {
  const [uptime, setUptime] = useState('00:00:00');

  useEffect(() => {
    if (!startedAt) { setUptime('00:00:00'); return; }
    const tick = () => {
      const diff = Math.floor((Date.now() - startedAt) / 1000);
      const h = String(Math.floor(diff / 3600)).padStart(2, '0');
      const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
      const s = String(diff % 60).padStart(2, '0');
      setUptime(`${h}:${m}:${s}`);
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [startedAt]);

  return <span className="font-mono text-xs font-semibold text-ub-text-primary">{uptime}</span>;
}

interface EngineStatusCardProps {
  engineStatus: 'running' | 'stopped' | 'paused' | 'error';
  engineMode: 'paper' | 'live';
  activeBrokerId: string | null;
  startedAt: number | null;
  errorMessage?: string | null;
  onOpenStartDialog: () => void;
  onStopEngine: () => void;
}

export default function EngineStatusCard({
  engineStatus,
  engineMode,
  activeBrokerId,
  startedAt,
  errorMessage,
  onOpenStartDialog,
  onStopEngine,
}: EngineStatusCardProps) {
  const activeBrokerName = activeBrokerId
    ? (BROKER_LIST.find((b) => b.id === activeBrokerId)?.name ?? activeBrokerId)
    : null;

  return (
    <Card className="border-ub-border bg-ub-surface xl:col-span-2">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <Zap className="h-4 w-4 text-ub-accent" />
          Engine Status
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="space-y-3">
          {/* Top row: Status + Mode + Broker */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div
                className={`h-2.5 w-2.5 rounded-full ${
                  engineStatus === 'running'
                    ? 'bg-ub-profit animate-pulse'
                    : engineStatus === 'error'
                      ? 'bg-ub-loss animate-pulse'
                      : engineStatus === 'paused'
                        ? 'bg-ub-warning'
                        : 'bg-ub-text-disabled'
                }`}
              />
              <Badge
                variant="outline"
                className={`text-xs font-semibold ${
                  engineStatus === 'running'
                    ? 'border-ub-profit/30 text-ub-profit bg-ub-profit/10'
                    : engineStatus === 'error'
                      ? 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
                      : engineStatus === 'paused'
                        ? 'border-ub-warning/30 text-ub-warning bg-ub-warning/10'
                        : 'border-ub-text-disabled/30 text-ub-text-disabled bg-ub-text-disabled/10'
                }`}
              >
                {engineStatus.charAt(0).toUpperCase() + engineStatus.slice(1)}
              </Badge>
            </div>
            <Badge
              variant="outline"
              className={`text-[10px] font-semibold ${
                engineMode === 'live'
                  ? 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
                  : 'border-ub-accent/30 text-ub-accent bg-ub-accent/10'
              }`}
            >
              {engineMode === 'live' ? '🔴 ' : '🟢 '}
              {engineMode.charAt(0).toUpperCase() + engineMode.slice(1)}
            </Badge>
          </div>

          {/* Active broker + uptime (shown when running) */}
          {engineStatus === 'running' && (
            <div className="flex items-center justify-between px-3 py-2 rounded-md" style={{ backgroundColor: 'rgba(0, 208, 156, 0.06)', border: '1px solid rgba(0, 208, 156, 0.12)' }}>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <Wifi size={13} className="text-ub-profit" />
                  <span className="text-[11px] text-ub-text-muted">Connected via</span>
                </div>
                <span className="text-xs font-semibold text-ub-text-primary">
                  {activeBrokerName || (engineMode === 'live' ? 'Live Broker' : 'Paper Broker')}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <Timer size={13} className="text-ub-text-disabled" />
                <span className="text-[11px] text-ub-text-muted">Uptime</span>
                <UptimeTicker startedAt={startedAt} />
              </div>
            </div>
          )}

          {/* Error message */}
          {engineStatus === 'error' && errorMessage && (
            <div className="flex items-start gap-2 px-3 py-2.5 rounded-md" style={{ backgroundColor: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
              <AlertTriangle size={14} className="shrink-0 mt-0.5 text-ub-loss" />
              <p className="text-[11px] text-ub-loss leading-relaxed">{errorMessage}</p>
            </div>
          )}

          {/* Stopped: show idle guidance */}
          {engineStatus === 'stopped' && !activeBrokerId && (
            <p className="text-[11px] text-ub-text-disabled">Engine is idle. Select a mode and broker to start trading.</p>
          )}

          <div className="flex gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={engineStatus === 'running'}
                  onClick={onOpenStartDialog}
                  className={`flex-1 h-9 text-xs font-semibold border-ub-border ${
                    engineStatus !== 'running'
                      ? 'hover:bg-ub-profit/15 hover:text-ub-profit hover:border-ub-profit/40 text-ub-text-muted'
                      : 'opacity-40 cursor-not-allowed'
                  }`}
                >
                  <Power className="h-3.5 w-3.5 mr-1.5" />
                  Start Engine
                </Button>
              </TooltipTrigger>
              <TooltipContent className="bg-ub-surface border-ub-border text-ub-text-primary text-xs">
                Choose mode & broker to start
              </TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={engineStatus === 'stopped'}
                  onClick={onStopEngine}
                  className={`flex-1 h-9 text-xs font-semibold border-ub-border ${
                    engineStatus !== 'stopped'
                      ? 'hover:bg-ub-loss/15 hover:text-ub-loss hover:border-ub-loss/40 text-ub-text-muted'
                      : 'opacity-40 cursor-not-allowed'
                  }`}
                >
                  <Square className="h-3.5 w-3.5 mr-1.5" />
                  Stop
                </Button>
              </TooltipTrigger>
              <TooltipContent className="bg-ub-surface border-ub-border text-ub-text-primary text-xs">
                Stop the trading engine
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
