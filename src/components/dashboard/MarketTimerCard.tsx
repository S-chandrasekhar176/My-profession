'use client';

import { useState, useEffect } from 'react';
import { Clock } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { useStore } from '@/lib/store';

export default function MarketTimerCard({ className = '' }: { className?: string }) {
  const [now, setNow] = useState(() => new Date());
  const storeMarketCloseSeconds = useStore((s) => s.engine.marketCloseSeconds);

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1_000);
    return () => clearInterval(interval);
  }, []);

  const istString = now.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  let marketCloseSeconds = storeMarketCloseSeconds;
  if (!marketCloseSeconds) {
    const istNow = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const isWeekday = istNow.getDay() >= 1 && istNow.getDay() <= 5;
    const sec = istNow.getHours() * 3600 + istNow.getMinutes() * 60 + istNow.getSeconds();
    const openSec = 9 * 3600 + 15 * 60; // 09:15
    const closeSec = 15 * 3600 + 30 * 60; // 15:30
    if (isWeekday && sec >= openSec && sec < closeSec) {
      marketCloseSeconds = closeSec - sec;
    }
  }

  const hours = Math.floor(marketCloseSeconds / 3600);
  const minutes = Math.floor((marketCloseSeconds % 3600) / 60);
  const seconds = Math.floor(marketCloseSeconds % 60);
  const pad = (n: number) => String(n).padStart(2, '0');
  const timeToClose = marketCloseSeconds > 0
    ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
    : 'Market Closed';

  const isUrgent = marketCloseSeconds > 0 && marketCloseSeconds < 1800;

  return (
    <Card className={`border-ub-border bg-ub-surface xl:col-span-2 self-start h-fit ${className}`}>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <Clock className="h-4 w-4 text-ub-accent" />
          Market Timer
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-ub-text-muted" />
            <span className="text-lg font-mono font-bold text-ub-text-primary tracking-wider">
              {istString}
            </span>
            <Badge variant="outline" className="text-[10px] font-medium border-ub-border text-ub-text-muted">
              IST
            </Badge>
          </div>
          <Separator className="bg-ub-border" />
          <div className="flex items-center justify-between">
            <span className="text-xs text-ub-text-muted">Time to Close</span>
            <span className={`text-sm font-mono font-semibold ${isUrgent ? 'text-ub-warning' : 'text-ub-text-primary'}`}>
              {timeToClose}
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
