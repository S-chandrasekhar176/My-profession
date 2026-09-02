'use client';

import { Menu, TrendingUp, TrendingDown, Minus, Activity, Clock, ShieldCheck } from 'lucide-react';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { useSidebar, useEngine, useStore, type MarketRegime } from '@/lib/store';
import { useMarketData, useEngineStatus } from '@/hooks/useApi';
import { getMarketHoursInfo, type MarketHoursInfo } from '@/lib/marketHours';
import { theme } from '@/styles/theme';

interface MarketIndexItem {
  id: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
}

const INITIAL_INDICES: MarketIndexItem[] = [
  { id: 'nifty', name: 'NIFTY', price: 24361.90, change: -33.95, changePct: -0.14 },
  { id: 'sensex', name: 'SENSEX', price: 77903.43, change: -176.53, changePct: -0.23 },
  { id: 'banknifty', name: 'BANKNIFTY', price: 57589.75, change: -45.50, changePct: -0.08 },
  { id: 'midcpnifty', name: 'MIDCPNIFTY', price: 15071.85, change: -6.30, changePct: -0.04 },
  { id: 'finnifty', name: 'FINNIFTY', price: 26306.20, change: -28.40, changePct: -0.11 },
];

const BROKER_NAMES: Record<string, string> = {
  zerodha: 'Zerodha',
  angel_one: 'Angel One',
  shoonya: 'Shoonya',
  dhan: 'Dhan',
  fyers: 'Fyers',
  yahoofinance: 'Yahoo Live',
  yahoo: 'Yahoo Live',
  paper: 'Paper Broker',
};

const REGIME_CONFIG: Record<MarketRegime, { label: string; color: string; Icon: typeof TrendingUp }> = {
  bull: { label: 'BL', color: theme.colors.bull, Icon: TrendingUp },
  bear: { label: 'BR', color: theme.colors.bear, Icon: TrendingDown },
  sideways: { label: 'SW', color: theme.colors.sideways, Icon: Minus },
  volatile: { label: 'VOL', color: theme.colors.volatile, Icon: Activity },
};

function formatMarketTimer(marketInfo: MarketHoursInfo): string {
  if (marketInfo.isOpen) {
    const s = marketInfo.secondsToClose;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `Closes in ${h}h ${m}m`;
    return `Closes in ${m}m ${s % 60}s`;
  }
  
  if (marketInfo.isPreMarket) {
    const s = marketInfo.secondsToOpen;
    return `Pre-Market (Opens ${Math.floor(s / 60)}m)`;
  }

  const s = marketInfo.secondsToOpen;
  if (s > 24 * 3600) {
    return `Closed (Opens Mon 09:15 AM)`;
  }
  const hours = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  return `Closed (Opens in ${hours}h ${mins}m)`;
}

export default function Header() {
  const { mobileOpen, setMobileOpen } = useSidebar();
  const engineStatus = useStore((s) => s.engine.status);
  const setEngineStatus = useStore((s) => s.engine.setEngineStatus);
  const regime = useStore((s) => s.engine.regime);
  const vix = useStore((s) => s.engine.vix);
  const setVix = useStore((s) => s.engine.setVix);
  const activeBroker = useStore((s) => s.engine.activeBroker);
  const setActiveBroker = useStore((s) => s.engine.setActiveBroker);

  const { data: marketData } = useMarketData();
  const { data: engineData } = useEngineStatus();

  const [indices, setIndices] = useState<MarketIndexItem[]>(INITIAL_INDICES);
  const [flashingIndex, setFlashingIndex] = useState<{ id: string; dir: 'up' | 'down' } | null>(null);
  const [marketInfo, setMarketInfo] = useState<MarketHoursInfo>(getMarketHoursInfo());
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (engineData && (engineData as any).state) {
      const state = (engineData as any).state.toLowerCase();
      if ((state === 'running' || state === 'stopped' || state === 'paused') && state !== engineStatus) {
        setEngineStatus(state);
      }
      const broker = (engineData as any).broker;
      if (broker && broker !== activeBroker) {
        setActiveBroker(broker);
      }
    }
  }, [engineData, engineStatus, activeBroker, setEngineStatus, setActiveBroker]);

  useEffect(() => {
    const checkMarket = () => {
      const info = getMarketHoursInfo();
      setMarketInfo(info);
      // Square-off is handled server-side by the engine's lifecycle scheduler
      // (15:15 IST auto-squareoff) — nothing to do in the browser.
    };

    checkMarket();
    const interval = setInterval(checkMarket, 1000);
    return () => clearInterval(interval);
  }, []);

  const fetchLiveQuotes = useCallback(async () => {
    try {
      const res = await fetch('/api/live-quotes?symbols=NIFTY,SENSEX,BANKNIFTY,MIDCPNIFTY,FINNIFTY,VIX', {
        cache: 'no-store',
      });
      if (res.ok) {
        const json = await res.json();
        if (json.success && json.data) {
          const d = json.data;
          const vixVal = d.VIX?.price ?? d.INDIAVIX?.price;
          if (typeof vixVal === 'number' && vixVal > 0) {
            setVix(vixVal);
          }

          setIndices((prev) => {
            const nextList: MarketIndexItem[] = [
              {
                id: 'nifty',
                name: 'NIFTY',
                price: d.NIFTY?.price ?? (prev.find((p) => p.id === 'nifty')?.price || 0),
                change: d.NIFTY?.change ?? (prev.find((p) => p.id === 'nifty')?.change || 0),
                changePct: d.NIFTY?.changePct ?? (prev.find((p) => p.id === 'nifty')?.changePct || 0),
              },
              {
                id: 'sensex',
                name: 'SENSEX',
                price: d.SENSEX?.price ?? (prev.find((p) => p.id === 'sensex')?.price || 0),
                change: d.SENSEX?.change ?? (prev.find((p) => p.id === 'sensex')?.change || 0),
                changePct: d.SENSEX?.changePct ?? (prev.find((p) => p.id === 'sensex')?.changePct || 0),
              },
              {
                id: 'banknifty',
                name: 'BANKNIFTY',
                price: d.BANKNIFTY?.price ?? (prev.find((p) => p.id === 'banknifty')?.price || 0),
                change: d.BANKNIFTY?.change ?? (prev.find((p) => p.id === 'banknifty')?.change || 0),
                changePct: d.BANKNIFTY?.changePct ?? (prev.find((p) => p.id === 'banknifty')?.changePct || 0),
              },
              {
                id: 'midcpnifty',
                name: 'MIDCPNIFTY',
                price: d.MIDCPNIFTY?.price ?? (prev.find((p) => p.id === 'midcpnifty')?.price || 0),
                change: d.MIDCPNIFTY?.change ?? (prev.find((p) => p.id === 'midcpnifty')?.change || 0),
                changePct: d.MIDCPNIFTY?.changePct ?? (prev.find((p) => p.id === 'midcpnifty')?.changePct || 0),
              },
              {
                id: 'finnifty',
                name: 'FINNIFTY',
                price: d.FINNIFTY?.price ?? (prev.find((p) => p.id === 'finnifty')?.price || 0),
                change: d.FINNIFTY?.change ?? (prev.find((p) => p.id === 'finnifty')?.change || 0),
                changePct: d.FINNIFTY?.changePct ?? (prev.find((p) => p.id === 'finnifty')?.changePct || 0),
              },
            ];

            // Trigger flash highlight if price changed in real feed
            nextList.forEach((nextItem) => {
              const oldItem = prev.find((p) => p.id === nextItem.id);
              if (oldItem && oldItem.price > 0 && nextItem.price !== oldItem.price) {
                setFlashingIndex({
                  id: nextItem.id,
                  dir: nextItem.price > oldItem.price ? 'up' : 'down',
                });
                setTimeout(() => setFlashingIndex(null), 1200);
              }
            });

            return nextList;
          });
        }
      }
    } catch {
    }
  }, [setVix]);

  useEffect(() => {
    fetchLiveQuotes();
    const interval = setInterval(fetchLiveQuotes, 3000);
    return () => clearInterval(interval);
  }, [fetchLiveQuotes]);

  const isMarketOpen = marketInfo.isOpen;
  const safeRegime = ((regime || 'sideways').toLowerCase() as MarketRegime);
  const regimeInfo = REGIME_CONFIG[safeRegime] || REGIME_CONFIG.sideways;
  const RegimeIcon = regimeInfo.Icon;

  // Real regime confidence from the engine's RegimeDetector (refreshed every
  // 15 min server-side, polled here every 10 s). Normalized defensively:
  // detector emits 0-1 floats; values > 1 are treated as already-percent.
  // Before the first classification (or when the backend is unreachable)
  // the badge shows an em-dash instead of a fabricated number.
  const rawRegimeConfidence = Number((engineData as any)?.regime_confidence ?? 0);
  const regimeConfidencePct =
    Number.isFinite(rawRegimeConfidence) && rawRegimeConfidence > 0
      ? rawRegimeConfidence <= 1
        ? Math.round(rawRegimeConfidence * 100)
        : Math.round(rawRegimeConfidence)
      : 0;
  const regimeScoreDisplay = regimeConfidencePct > 0 ? `${regimeConfidencePct}%` : '—';

  const safeEngineStatus = engineStatus || 'stopped';
  const isEngineRunning = safeEngineStatus === 'running';

  const rawVix = vix > 0 ? vix : (marketData?.vix && marketData.vix > 0 ? marketData.vix : 11.36);
  const displayVix = typeof rawVix === 'number' && !isNaN(rawVix) && rawVix > 0 ? rawVix : 11.36;
  const brokerLabel = activeBroker ? (BROKER_NAMES[activeBroker.toLowerCase()] || activeBroker) : 'Yahoo Live';

  return (
    <header
      className="sticky top-0 z-30 w-full select-none"
      style={{
        backgroundColor: '#0c1017',
        borderBottom: `1px solid ${theme.colors.border}`,
      }}
    >
      <div className="flex items-center justify-between h-13 px-3 sm:px-4 gap-3 max-w-full overflow-hidden">
        {/* Left: Mobile hamburger only (No tab title) */}
        <div className="flex items-center md:hidden shrink-0">
          <button
            className="p-1.5 rounded-md transition-colors duration-150"
            style={{ color: theme.colors.textMuted }}
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label="Toggle Navigation"
          >
            <Menu size={18} />
          </button>
        </div>

        {/* Center / Full-Width: Clean, Visible Indices Strip with All 5 Indices & NO Overlap */}
        <div className="flex-1 flex items-center justify-start sm:justify-center overflow-x-auto sm:overflow-hidden scrollbar-none py-1 min-w-0">
          <div className="flex items-center gap-1.5 sm:gap-2.5 md:gap-3 lg:gap-4 shrink-0">
            {indices.map((idx) => {
              const isFlashing = flashingIndex?.id === idx.id;
              const flashDir = flashingIndex?.dir;
              const isNegative = idx.change < 0;

              return (
                <div
                  key={idx.id}
                  className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md transition-all duration-200 border ${
                    isFlashing
                      ? flashDir === 'up'
                        ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300 shadow-[0_0_8px_rgba(16,185,129,0.3)]'
                        : 'bg-rose-500/20 border-rose-500/40 text-rose-300 shadow-[0_0_8px_rgba(244,63,94,0.3)]'
                      : 'bg-ub-surface/70 border-ub-border/50'
                  }`}
                >
                  <span className="font-bold text-ub-text-muted text-[11px] uppercase tracking-wider">
                    {idx.name}
                  </span>
                  <span
                    className={`font-mono text-xs font-bold transition-colors duration-300 ${
                      isFlashing
                        ? flashDir === 'up'
                          ? 'text-emerald-300'
                          : 'text-rose-300'
                        : 'text-ub-text-primary'
                    }`}
                  >
                    {typeof idx.price === 'number' && !isNaN(idx.price)
                      ? idx.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                      : '0.00'}
                  </span>
                  <span
                    className={`font-mono text-[11px] font-semibold ${
                      isNegative ? 'text-rose-400' : 'text-emerald-400'
                    }`}
                  >
                    {typeof idx.changePct === 'number' && !isNaN(idx.changePct)
                      ? `${idx.changePct >= 0 ? '+' : ''}${idx.changePct.toFixed(2)}%`
                      : '0.00%'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Regime Badge, Green/Red Pulse Dot Status, Broker, VIX, Clock */}
        <div className="flex items-center gap-2 text-xs shrink-0">
          {/* Market Regime with Value */}
          <Badge
            className="flex items-center px-2.5 py-0.5 text-[10px] font-bold border-0 rounded-full tracking-wide"
            style={{
              backgroundColor: `${regimeInfo.color}20`,
              color: regimeInfo.color,
            }}
          >
            {regimeInfo.label} {regimeScoreDisplay}
          </Badge>

          {/* Engine Status: Clean Glowing Pulse Dot only */}
          <div
            title={isEngineRunning ? 'Engine: Running' : 'Engine: Stopped'}
            className={`flex items-center justify-center h-7 w-7 rounded-full border transition-all duration-300 ${
              isEngineRunning
                ? 'bg-emerald-500/15 border-emerald-500/40 shadow-[0_0_10px_rgba(16,185,129,0.25)]'
                : 'bg-rose-500/15 border-rose-500/40 shadow-[0_0_8px_rgba(244,63,94,0.2)]'
            }`}
          >
            {isEngineRunning ? (
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-80"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500 shadow-[0_0_10px_#10b981]"></span>
              </span>
            ) : (
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-rose-500 shadow-[0_0_8px_#f43f5e]" />
            )}
          </div>

          {/* Broker Feed Tag */}
          <Badge className="hidden xl:inline-flex px-2 py-0.5 text-[10px] font-semibold border-0 rounded-full bg-ub-surface text-ub-text-muted">
            {brokerLabel}
          </Badge>

          {/* VIX */}
          <Badge
            className="hidden sm:inline-flex px-2 py-0.5 text-[10px] font-semibold border-0 rounded-full bg-ub-surface text-ub-text-muted"
          >
            VIX {displayVix.toFixed(1)}
          </Badge>

          {/* Market Status & Countdown Timer */}
          <Badge
            className="hidden lg:flex items-center gap-1.5 px-2.5 py-0.5 text-[10px] font-semibold border rounded-full"
            suppressHydrationWarning
            style={{
              backgroundColor: isMarketOpen ? 'rgba(16, 185, 129, 0.12)' : 'rgba(244, 63, 94, 0.12)',
              borderColor: isMarketOpen ? 'rgba(16, 185, 129, 0.3)' : 'rgba(244, 63, 94, 0.3)',
              color: isMarketOpen ? '#34d399' : '#fb7185',
            }}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isMarketOpen ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'
              }`}
            />
            <Clock className="h-2.5 w-2.5" />
            {mounted ? formatMarketTimer(marketInfo) : 'Market Closed'}
          </Badge>

          {/* Real IST Clock */}
          <span
            className="hidden sm:block text-[11px] font-mono text-ub-text-muted tabular-nums"
            suppressHydrationWarning
          >
            {mounted ? marketInfo.istTimeString : ''}
          </span>
        </div>
      </div>
    </header>
  );
}
