'use client';

import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useKronosHotlist, useWatchlist } from '@/hooks/useApi';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Search,
  X,
  Flame,
  Newspaper,
  Star,
  Clock,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  ExternalLink,
  Activity,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

interface HotStock {
  rank: number;
  symbol: string;
  price: number;
  changePct: number;
  volume: string;
  hotness: number;
  reason: string;
}

interface NewsFocusStock {
  symbol: string;
  name: string;
  price: number;
  changePct: number;
  headline: string;
  source: string;
  sentiment: 'BUY' | 'SELL' | 'WATCH';
  catalyst: string;
  url: string;
  publishedAt: string;
}

interface CustomStock {
  symbol: string;
  price: number;
  changePct: number;
}

// ─────────────────────────────────────────────
// Indian number formatting
// ─────────────────────────────────────────────

function formatINR(n: number | undefined | null): string {
  if (typeof n !== 'number' || isNaN(n)) return '₹0.00';
  return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatChangePercent(pct: number | undefined | null): string {
  if (typeof pct !== 'number' || isNaN(pct)) return '0.00%';
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function changeColor(pct: number | undefined | null): string {
  if (typeof pct !== 'number' || isNaN(pct)) return 'text-ub-text-muted';
  return pct >= 0 ? 'text-ub-profit' : 'text-ub-loss';
}

// ─────────────────────────────────────────────
// Symbol picker source for the search box (reference list only —
// NO prices or market data are hardcoded; every displayed price
// comes from the live-quotes API).
// ─────────────────────────────────────────────

const FO_UNIVERSE = [
  'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY',
  'ICICIBANK', 'SBIN', 'BHARTIARTL', 'ITC', 'KOTAKBANK', 'LT', 'WIPRO',
  'AXISBANK', 'MARUTI', 'SUNPHARMA', 'TMPV', 'TMCV', 'BAJFINANCE', 'HCLTECH',
  'ADANIENT', 'TATAPOWER', 'JSWSTEEL', 'DRREDDY', 'PIIND', 'DIVISLAB',
  'ASIANPAINT', 'TITAN', 'ULTRACEMCO', 'NESTLEIND', 'TECHM', 'ONGC',
  'NTPC', 'POWERGRID', 'COALINDIA', 'BPCL', 'HINDUNILVR', 'BAJAJFINSV',
  'INDUSINDBK', 'GRASIM', 'M&M', 'EICHERMOT', 'HEROMOTOCO', 'BRITANNIA',
];

export default function WatchlistPage() {
  const { data: hotData } = useKronosHotlist();
  const { data: apiWatchlist } = useWatchlist();

  // Start empty — NO hardcoded placeholder data. Both lists populate
  // exclusively from the backend APIs (Kronos hotlist + active watchlist)
  // and the live-quotes price sync below.
  const [kronosStocks, setKronosStocks] = useState<HotStock[]>([]);
  const [customStocks, setCustomStocks] = useState<CustomStock[]>([]);
  const [lastSyncTime, setLastSyncTime] = useState<string>('');

  const kronosSymbolsRef = useRef<string[]>([]);
  const customSymbolsRef = useRef<string[]>([]);

  useEffect(() => {
    kronosSymbolsRef.current = kronosStocks.map((s) => s.symbol);
  }, [kronosStocks]);

  useEffect(() => {
    customSymbolsRef.current = customStocks.map((s) => s.symbol);
  }, [customStocks]);

  // Hydrate from API if available
  useEffect(() => {
    if (hotData && Array.isArray(hotData) && hotData.length > 0) {
      setKronosStocks(hotData);
    }
  }, [hotData]);

  useEffect(() => {
    if (apiWatchlist) {
      const rawList = Array.isArray(apiWatchlist)
        ? apiWatchlist
        : (apiWatchlist as { watchlist?: unknown[] }).watchlist;

      if (Array.isArray(rawList) && rawList.length > 0) {
        const mapped: CustomStock[] = rawList
          .map((item: any) => {
            if (typeof item === 'string') {
              return { symbol: item, price: 0, changePct: 0 };
            }
            if (item && typeof item === 'object' && item.symbol) {
              return {
                symbol: String(item.symbol),
                price: typeof item.price === 'number' ? item.price : 0,
                changePct: typeof item.changePct === 'number' ? item.changePct : 0,
              };
            }
            return null;
          })
          .filter((item): item is CustomStock => item !== null);

        if (mapped.length > 0) {
          setCustomStocks(mapped);
        }
      }
    }
  }, [apiWatchlist]);

  // 1. Sync Live Quotes for all stocks from Live Market Quotes API
  const syncLivePrices = useCallback(async () => {
    try {
      const allSymbols = Array.from(
        new Set([
          ...kronosSymbolsRef.current,
          ...customSymbolsRef.current,
        ])
      ).filter(Boolean);

      if (allSymbols.length === 0) return;

      const res = await fetch(`/api/live-quotes?symbols=${allSymbols.join(',')}`, { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        if (json.success && json.data) {
          const quotes = json.data;
          setKronosStocks((prev) =>
            prev.map((item) => {
              const q = quotes[item.symbol];
              if (q && q.price > 0) {
                return {
                  ...item,
                  price: q.price,
                  changePct: q.changePct,
                };
              }
              return item;
            }),
          );

          setCustomStocks((prev) =>
            prev.map((item) => {
              const q = quotes[item.symbol];
              if (q && q.price > 0) {
                return {
                  ...item,
                  price: q.price,
                  changePct: q.changePct,
                };
              }
              return item;
            }),
          );

          setLastSyncTime(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
        }
      }
    } catch {
      // Live sync error handling
    }
  }, []);

  useEffect(() => {
    syncLivePrices();
    const interval = setInterval(() => {
      syncLivePrices();
    }, 5000);
    return () => clearInterval(interval);
  }, [syncLivePrices]);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);

  const filteredUniverse = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const q = searchQuery.toUpperCase();
    const addedSymbols = new Set(customStocks.map((s) => s.symbol));
    return FO_UNIVERSE.filter(
      (s) => s.includes(q) && !addedSymbols.has(s),
    ).slice(0, 8);
  }, [searchQuery, customStocks]);

  const addStock = async (symbol: string) => {
    if (customStocks.find((s) => s.symbol === symbol)) return;
    setSearchQuery('');
    setSearchFocused(false);

    // Fetch real live price directly from live market quote feed
    try {
      const res = await fetch(`/api/live-quotes?symbols=${symbol}`, { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        const q = json.data?.[symbol];
        if (q && q.price > 0) {
          setCustomStocks((prev) => [...prev, { symbol, price: q.price, changePct: q.changePct }]);
          return;
        }
      }
    } catch (err) {
      console.error('Failed fetching live quote for stock:', err);
    }

    setCustomStocks((prev) => [...prev, { symbol, price: 0, changePct: 0 }]);
  };

  const removeStock = (symbol: string) => {
    setCustomStocks((prev) => prev.filter((s) => s.symbol !== symbol));
  };

  return (
    <div className="space-y-4">
      <Tabs defaultValue="hotlist" className="space-y-4">
        <TabsList className="bg-ub-surface border border-ub-border">
          <TabsTrigger
            value="hotlist"
            className="data-[state=active]:bg-ub-accent/15 data-[state=active]:text-ub-accent text-ub-text-muted gap-1.5"
          >
            <Flame className="h-3.5 w-3.5" />
            Kronos Hot List
          </TabsTrigger>
          <TabsTrigger
            value="custom"
            className="data-[state=active]:bg-ub-accent/15 data-[state=active]:text-ub-accent text-ub-text-muted gap-1.5"
          >
            <Star className="h-3.5 w-3.5" />
            My Custom List
          </TabsTrigger>
        </TabsList>

        {/* ── Tab 1: Kronos Hot List ── */}
        <TabsContent value="hotlist">
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                  <Flame className="h-4 w-4 text-ub-accent" />
                  Kronos Hot List (Live Market LTP)
                </CardTitle>
                <div className="flex items-center gap-3">
                  <span className="text-[11px] text-ub-text-muted flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Last Sync: {lastSyncTime || 'Live'}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={syncLivePrices}
                    className="h-6 px-2 text-xs text-ub-accent hover:bg-ub-accent/10"
                  >
                    <RefreshCw className="h-3 w-3 mr-1" />
                    Refresh
                  </Button>
                </div>
              </div>
              <p className="text-xs text-ub-text-muted mt-0.5">Top-ranked momentum and breakout candidates updated with real-time ticks</p>
            </CardHeader>
            <CardContent className="p-4 pt-2">
              <ScrollArea className="h-[460px]">
                <Table>
                  <TableHeader>
                    <TableRow className="border-ub-border hover:bg-transparent">
                      <TableHead className="w-12 text-ub-text-muted text-[11px]">#</TableHead>
                      <TableHead className="text-ub-text-muted text-[11px]">Symbol</TableHead>
                      <TableHead className="text-right text-ub-text-muted text-[11px]">LTP (Live)</TableHead>
                      <TableHead className="text-right text-ub-text-muted text-[11px]">Change %</TableHead>
                      <TableHead className="text-right text-ub-text-muted text-[11px] hidden sm:table-cell">Volume</TableHead>
                      <TableHead className="text-ub-text-muted text-[11px] hidden md:table-cell">Hotness</TableHead>
                      <TableHead className="text-ub-text-muted text-[11px] hidden lg:table-cell">Setup Catalyst</TableHead>
                      <TableHead className="text-ub-text-muted text-[11px]">Signal</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {kronosStocks.length === 0 && (
                      <TableRow className="border-ub-border/50 hover:bg-transparent">
                        <TableCell colSpan={8} className="py-12 text-center">
                          <div className="flex flex-col items-center gap-2">
                            <Activity className="h-5 w-5 text-ub-text-disabled" />
                            <p className="text-xs text-ub-text-muted">
                              No hotlist data yet — the Kronos Hot List populates from
                              today&rsquo;s generated watchlist (pre-market 08:45 IST or on
                              the next engine scan).
                            </p>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                    {kronosStocks.map((stock) => (
                      <TableRow
                        key={stock.symbol}
                        className="border-ub-border/50 hover:bg-ub-surface-hover transition-colors"
                      >
                        <TableCell className="font-mono text-xs text-ub-text-muted font-medium">
                          {stock.rank}
                        </TableCell>
                        <TableCell className="font-bold text-xs text-ub-text-primary tracking-wide">
                          {stock.symbol}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-ub-text-primary font-bold">
                          {formatINR(stock.price)}
                        </TableCell>
                        <TableCell className={cn('text-right font-mono text-xs font-semibold', changeColor(stock.changePct))}>
                          {formatChangePercent(stock.changePct)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-ub-text-muted hidden sm:table-cell">
                          {stock.volume}
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          <div className="flex items-center gap-2">
                            <Progress
                              value={stock.hotness}
                              className="h-1.5 flex-1"
                            />
                            <span className="text-[10px] text-ub-text-primary font-mono w-8 text-right font-semibold">
                              {stock.hotness}%
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-ub-text-muted hidden lg:table-cell max-w-[280px] truncate" title={stock.reason}>
                          {stock.reason}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className="text-[10px] px-1.5 py-0 border-ub-accent/30 text-ub-accent bg-ub-accent/5"
                          >
                            Kronos AI
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Tab 2: My Custom List ── */}
        <TabsContent value="custom">
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-sm font-semibold text-ub-text-primary flex items-center gap-2">
                <Star className="h-4 w-4 text-ub-accent" />
                My Custom Watchlist
              </CardTitle>
              <p className="text-xs text-ub-text-muted mt-1">Add symbols to monitor live prices</p>
            </CardHeader>
            <CardContent className="p-4 pt-2 space-y-4">
              <div className="relative max-w-sm">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-ub-text-muted" />
                <Input
                  type="text"
                  placeholder="Search F&O symbol (e.g. RELIANCE, TCS)..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onFocus={() => setSearchFocused(true)}
                  onBlur={() => setTimeout(() => setSearchFocused(false), 200)}
                  className="pl-8 bg-ub-surface border-ub-border text-ub-text-primary text-xs"
                />
                {searchFocused && filteredUniverse.length > 0 && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-ub-surface border border-ub-border rounded-lg shadow-xl z-50 overflow-hidden">
                    {filteredUniverse.map((sym) => (
                      <button
                        key={sym}
                        onMouseDown={() => addStock(sym)}
                        className="w-full text-left px-3 py-2 text-xs text-ub-text-primary hover:bg-ub-surface-hover flex items-center justify-between"
                      >
                        <span className="font-semibold">{sym}</span>
                        <span className="text-[10px] text-ub-accent font-medium">+ Add</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                {customStocks.length === 0 && (
                  <p className="text-xs text-ub-text-muted col-span-full py-6 text-center">
                    No symbols yet — search above to add symbols to your custom watchlist.
                    Prices update live every 5 seconds once added.
                  </p>
                )}
                {customStocks.map((stock) => (
                  <div
                    key={stock.symbol}
                    className="p-3 rounded-lg border border-ub-border bg-ub-surface/60 flex items-center justify-between relative group"
                  >
                    <div>
                      <span className="text-xs font-bold text-ub-text-primary block">{stock.symbol}</span>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="font-mono text-xs text-ub-text-primary">{formatINR(stock.price)}</span>
                        <span className={cn('font-mono text-[10px] font-semibold', changeColor(stock.changePct))}>
                          {formatChangePercent(stock.changePct)}
                        </span>
                      </div>
                    </div>
                    <button
                      onClick={() => removeStock(stock.symbol)}
                      className="p-1 rounded text-ub-text-muted hover:text-ub-loss opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
