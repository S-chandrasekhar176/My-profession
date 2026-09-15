'use client';

import React, { useState } from 'react';
import {
  Activity,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ArrowRight,
  Filter,
  Search,
  Zap,
  Sliders,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { MlEvaluation } from '@/lib/api';

interface EvaluationsAuditTabProps {
  evaluations: MlEvaluation[];
  onInspectItem: (item: any) => void;
}

export default function EvaluationsAuditTab({ evaluations, onInspectItem }: EvaluationsAuditTabProps) {
  const [filterAction, setFilterAction] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const filtered = evaluations.filter((ev) => {
    const matchesFilter = filterAction === 'ALL' || ev.action === filterAction;
    const matchesQuery =
      searchQuery === '' ||
      ev.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
      ev.strategy.toLowerCase().includes(searchQuery.toLowerCase()) ||
      ev.evaluation_id.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesFilter && matchesQuery;
  });

  return (
    <div className="space-y-4 font-mono">
      {/* Search & Filter Header */}
      <div className="rounded-xl border border-cyan-500/30 bg-[#080D1A] p-4 shadow-lg flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-cyan-400" />
          <h2 className="text-xs font-black uppercase text-white tracking-wider">
            Live Signal Evaluations & Feature Attribution Audit
          </h2>
          <Badge className="bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 text-[9px]">
            {filtered.length} RECORDS
          </Badge>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="w-3 h-3 absolute left-2.5 top-2 text-[#848e9c]" />
            <input
              type="text"
              placeholder="Search symbol, strategy..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-7 pl-7 pr-2 rounded bg-[#050811] border border-[#1A233A] text-[10px] text-white focus:outline-none focus:border-cyan-400 font-mono w-44"
            />
          </div>

          <div className="flex items-center gap-1 bg-[#050811] p-0.5 rounded border border-[#1A233A]">
            {['ALL', 'FAVORABLE', 'VETO', 'NEUTRAL'].map((act) => (
              <button
                key={act}
                onClick={() => setFilterAction(act)}
                className={`px-2 py-0.5 rounded text-[9px] font-mono transition ${
                  filterAction === act
                    ? 'bg-cyan-500 text-black font-bold shadow'
                    : 'text-[#848e9c] hover:text-white'
                }`}
              >
                {act}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Evaluations Table */}
      <div className="rounded-xl border border-cyan-500/20 bg-[#080D1A] p-3 shadow-lg overflow-x-auto">
        <table className="w-full text-left text-[10px] text-[#D1D4DC]">
          <thead className="text-[9px] text-[#848e9c] border-b border-[#1A233A] uppercase bg-[#050811]">
            <tr>
              <th className="py-2 px-2.5">Time / Symbol</th>
              <th className="py-2 px-2.5">Strategy</th>
              <th className="py-2 px-2.5">Win Prob</th>
              <th className="py-2 px-2.5">Action & Gate G21</th>
              <th className="py-2 px-2.5">Top Feature Contributions</th>
              <th className="py-2 px-2.5 text-right">Audit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1A233A]">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-8 text-center text-[#848e9c]">
                  No signal evaluations match your filter.
                </td>
              </tr>
            ) : (
              filtered.map((ev) => {
                const isVeto = ev.action === 'VETO';
                const isFavorable = ev.action === 'FAVORABLE';
                const winProb = (ev.win_probability || ev.score * 100).toFixed(1);

                return (
                  <tr
                    key={ev.evaluation_id}
                    onClick={() =>
                      onInspectItem({
                        id: ev.evaluation_id,
                        title: `Signal Evaluation: ${ev.symbol} (${ev.strategy})`,
                        category: 'NEURAL_PRIOR',
                        status: `${ev.action} (${winProb}% WR)`,
                        statusType: isFavorable ? 'success' : isVeto ? 'warning' : 'info',
                        sourceFile: 'ultrabot-web/backend/ml/inference.py (score_signal)',
                        sourceFeed: `Point-in-Time Snapshot at ${ev.timestamp}`,
                        frequency: 'Recorded per strategy trigger',
                        purpose:
                          'Captures full feature snapshot, feature attribution weights, and Risk Gate G21 decisions for end-to-end trading auditability.',
                        whatWeKnow: [
                          { label: 'Evaluation ID', value: ev.evaluation_id },
                          { label: 'Timestamp', value: ev.timestamp },
                          { label: 'Symbol & Direction', value: `${ev.symbol} ${ev.direction}` },
                          { label: 'Strategy Trigger', value: ev.strategy },
                          { label: 'Calibrated Win Probability', value: `${winProb}%` },
                          { label: 'Gate G21 Advisory Outcome', value: isVeto ? 'VETO (Signal Aborted)' : 'PASS (Order Dispatched)' },
                          { label: 'Veto Threshold', value: `${ev.veto_threshold || 0.42}` },
                          { label: 'Favorable Threshold', value: `${ev.favorable_threshold || 0.60}` },
                        ],
                        mathematicsOrRule: 'P(Win) = Sigmoid(w^T * X_norm + b / Temperature)',
                        codeSnippet: JSON.stringify(ev.features || {}, null, 2),
                      })
                    }
                    className="hover:bg-[#0E1726]/60 cursor-pointer transition"
                  >
                    <td className="py-2 px-2.5">
                      <div className="font-bold text-white">{ev.symbol}</div>
                      <div className="text-[8.5px] text-[#848e9c]">{ev.timestamp.split('T')[1]?.slice(0, 8) || ev.timestamp}</div>
                    </td>

                    <td className="py-2 px-2.5">
                      <Badge className="bg-[#121B2F] border-[#1A233A] text-cyan-300 text-[9px] font-mono">
                        {ev.strategy}
                      </Badge>
                      <span className={`ml-1.5 text-[9px] font-bold ${ev.direction === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {ev.direction}
                      </span>
                    </td>

                    <td className="py-2 px-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className={`font-black ${isFavorable ? 'text-emerald-400' : isVeto ? 'text-rose-400' : 'text-cyan-300'}`}>
                          {winProb}%
                        </span>
                        <div className="w-12 bg-[#121B2F] h-1.5 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${isFavorable ? 'bg-emerald-400' : isVeto ? 'bg-rose-400' : 'bg-cyan-400'}`}
                            style={{ width: `${Math.min(100, parseFloat(winProb))}%` }}
                          />
                        </div>
                      </div>
                    </td>

                    <td className="py-2 px-2.5">
                      <Badge
                        className={`text-[8.5px] font-mono ${
                          isVeto
                            ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                            : isFavorable
                            ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                            : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30'
                        }`}
                      >
                        {isVeto ? 'GATE G21 VETO' : isFavorable ? 'FAVORABLE' : 'NEUTRAL'}
                      </Badge>
                    </td>

                    <td className="py-2 px-2.5">
                      <div className="flex flex-wrap gap-1 text-[8.5px]">
                        {ev.contributions &&
                          Object.entries(ev.contributions)
                            .slice(0, 3)
                            .map(([key, val]) => (
                              <span
                                key={key}
                                className={`px-1 py-0.2 rounded ${
                                  (val as number) >= 0 ? 'bg-emerald-500/10 text-emerald-300' : 'bg-rose-500/10 text-rose-300'
                                }`}
                              >
                                {key.replace('_norm', '')}: {(val as number) > 0 ? `+${val}%` : `${val}%`}
                              </span>
                            ))}
                      </div>
                    </td>

                    <td className="py-2 px-2.5 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 text-[9px] font-mono text-cyan-400 hover:text-cyan-300 p-1"
                      >
                        Inspect Proof <ArrowRight className="w-2.5 h-2.5 ml-1" />
                      </Button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
