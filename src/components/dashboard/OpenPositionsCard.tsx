'use client';

import { Activity, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Position, formatINR } from './DashboardTypes';

export default function OpenPositionsCard({ positions }: { positions: Position[] }) {
  return (
    <Card className="border-ub-border bg-ub-surface md:col-span-1 xl:col-span-3">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-xs font-semibold text-ub-text-primary flex items-center gap-2">
          <Activity className="h-4 w-4 text-ub-accent" />
          Open Positions
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <ScrollArea className="max-h-[300px]">
          <Table>
            <TableHeader>
              <TableRow className="border-ub-border hover:bg-transparent">
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8">Symbol</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-center">Dir</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-right">Entry</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-right">Current</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-center">Qty</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-right">P&L</TableHead>
                <TableHead className="text-[11px] text-ub-text-muted font-semibold uppercase tracking-wider h-8 text-center hidden sm:table-cell">Booked</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {positions.map((pos) => {
                const pnlPositive = pos.pnl >= 0;
                return (
                  <TableRow key={pos.id} className="border-ub-border hover:bg-ub-surface-hover transition-colors">
                    <TableCell className="text-xs font-semibold text-ub-text-primary py-2.5">
                      {pos.symbol}
                    </TableCell>
                    <TableCell className="py-2.5 text-center">
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-bold ${
                          pos.direction === 'BUY'
                            ? 'border-ub-profit/30 text-ub-profit bg-ub-profit/10'
                            : 'border-ub-loss/30 text-ub-loss bg-ub-loss/10'
                        }`}
                      >
                        {pos.direction === 'BUY' ? (
                          <ArrowUpRight className="h-3 w-3 mr-0.5" />
                        ) : (
                          <ArrowDownRight className="h-3 w-3 mr-0.5" />
                        )}
                        {pos.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ub-text-muted py-2.5 text-right">
                      {pos.entry.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ub-text-primary py-2.5 text-right">
                      {pos.current.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-xs font-mono text-ub-text-primary py-2.5 text-center">
                      {pos.qty}
                    </TableCell>
                    <TableCell className={`text-xs font-mono font-semibold py-2.5 text-right ${
                      pnlPositive ? 'text-ub-profit' : 'text-ub-loss'
                    }`}>
                      {pnlPositive ? '+' : ''}{formatINR(pos.pnl)}
                    </TableCell>
                    <TableCell className="py-2.5 text-center hidden sm:table-cell">
                      {pos.bookedLevels.length > 0 ? (
                        <div className="flex flex-wrap gap-1 justify-center">
                          {pos.bookedLevels.map((level, idx) => (
                            <Badge
                              key={idx}
                              variant="outline"
                              className="text-[9px] font-mono border-ub-accent/30 text-ub-accent bg-ub-accent/5"
                            >
                              {level.toFixed(1)}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-[11px] text-ub-text-muted">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </ScrollArea>
        {positions.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-ub-text-muted">
            <Activity className="h-8 w-8 mb-2 opacity-30" />
            <p className="text-xs">No open positions</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
