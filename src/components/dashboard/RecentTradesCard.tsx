'use client';

import Link from 'next/link';
import { Eye, ArrowUpRight } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Trade, formatINR } from './DashboardTypes';

export default function RecentTradesCard({ trades }: { trades: Trade[] }) {
  return (
    <Card className="border-ub-border bg-ub-surface md:col-span-2 xl:col-span-4">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <Eye className="h-4 w-4 text-ub-accent" />
          Recent Trades
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <Table>
          <TableHeader>
            <TableRow className="border-ub-border hover:bg-transparent">
              <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8">Time</TableHead>
              <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8">Symbol</TableHead>
              <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-center">Direction</TableHead>
              <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-right">P&L</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trades.slice(0, 5).map((trade) => {
              const isProfit = trade.pnl >= 0;
              return (
                <TableRow key={trade.id} className="border-ub-border hover:bg-ub-surface-hover transition-colors">
                  <TableCell className="text-xs font-mono text-ub-text-muted py-2.5">
                    {trade.time}
                  </TableCell>
                  <TableCell className="text-xs font-semibold text-ub-text-primary py-2.5">
                    {trade.symbol}
                  </TableCell>
                  <TableCell className="py-2.5 text-center">
                    <Badge
                      variant="outline"
                      className={`text-[10px] font-bold ${
                        trade.direction === 'BUY'
                          ? 'border-ub-profit/30 text-ub-profit bg-ub-profit/10'
                          : 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
                      }`}
                    >
                      {trade.direction}
                    </Badge>
                  </TableCell>
                  <TableCell className={`text-xs font-mono font-semibold py-2.5 text-right ${
                    isProfit ? 'text-ub-profit' : 'text-ub-loss'
                  }`}>
                    {isProfit ? '+' : ''}{formatINR(trade.pnl)}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {trades.length === 0 && (
          <div className="flex flex-col items-center justify-center py-6 text-ub-text-muted">
            <Eye className="h-8 w-8 mb-2 opacity-30" />
            <p className="text-xs">No trades today</p>
          </div>
        )}
        <div className="mt-3 flex justify-end">
          <Link href="/trades">
            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-ub-accent hover:text-ub-accent-hover hover:bg-ub-accent/10 h-7 cursor-pointer"
            >
              View All Trades
              <ArrowUpRight className="h-3 w-3 ml-1" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
