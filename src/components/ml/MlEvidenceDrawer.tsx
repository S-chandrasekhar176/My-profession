'use client';

import React from 'react';
import {
  X,
  FileCode2,
  Database,
  Cpu,
  ShieldCheck,
  Zap,
  Activity,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Code2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

export interface EvidenceItem {
  id: string;
  title: string;
  category: 'INGESTION' | 'FEATURE' | 'NEURAL_PRIOR' | 'RISK_GATE' | 'EXECUTION' | 'TELEMETRY' | 'ANALYTICS';
  status: string;
  statusType?: 'success' | 'warning' | 'info' | 'purple';
  sourceFile: string;
  sourceFeed: string;
  frequency: string;
  purpose: string;
  whatWeKnow: {
    label: string;
    value: string;
    detail?: string;
  }[];
  mathematicsOrRule?: string;
  codeSnippet?: string;
  deepDiveTab?: string;
}

interface MlEvidenceDrawerProps {
  item: EvidenceItem | null;
  onClose: () => void;
  onSelectTab?: (tab: string) => void;
}

export default function MlEvidenceDrawer({ item, onClose, onSelectTab }: MlEvidenceDrawerProps) {
  if (!item) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-all duration-300">
      <div className="relative w-full max-w-lg bg-[#060912] border-l border-cyan-500/30 h-full shadow-[-10px_0_40px_rgba(0,240,255,0.15)] flex flex-col z-50 overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-[#1A233A] bg-[#090D1A]/90 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
              <Zap className="w-4 h-4 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-black font-mono tracking-wider text-white uppercase">{item.title}</h2>
                <Badge
                  className={`text-[9px] font-mono px-1.5 py-0.2 ${
                    item.statusType === 'success'
                      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                      : item.statusType === 'warning'
                      ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                      : item.statusType === 'purple'
                      ? 'bg-purple-500/20 text-purple-300 border-purple-500/40'
                      : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                  }`}
                >
                  {item.status}
                </Badge>
              </div>
              <span className="text-[10px] font-mono text-[#848e9c]">CATEGORY: {item.category}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#848e9c] hover:text-white hover:bg-[#1A233A] transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 font-mono text-xs text-[#D1D4DC]">
          {/* 1. WHERE IT IS COMING FROM (PROVENANCE & ORIGIN) */}
          <div className="rounded-xl border border-cyan-500/20 bg-[#0A0F1E] p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-cyan-400 text-[11px] font-bold uppercase tracking-wider">
              <FileCode2 className="w-3.5 h-3.5" />
              <span>Where It Comes From (Data Provenance)</span>
            </div>
            <div className="grid grid-cols-1 gap-1.5 text-[10px]">
              <div className="flex items-center justify-between p-1.5 rounded bg-[#060912] border border-[#1A233A]">
                <span className="text-[#848e9c]">Source File / Module:</span>
                <span className="text-white font-mono font-semibold">{item.sourceFile}</span>
              </div>
              <div className="flex items-center justify-between p-1.5 rounded bg-[#060912] border border-[#1A233A]">
                <span className="text-[#848e9c]">Feed / Transport:</span>
                <span className="text-cyan-300">{item.sourceFeed}</span>
              </div>
              <div className="flex items-center justify-between p-1.5 rounded bg-[#060912] border border-[#1A233A]">
                <span className="text-[#848e9c]">Update Cadence:</span>
                <span className="text-emerald-400">{item.frequency}</span>
              </div>
            </div>
          </div>

          {/* 2. WHAT IT IS FOR (PURPOSE & TRADING LOGIC) */}
          <div className="rounded-xl border border-cyan-500/20 bg-[#0A0F1E] p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-emerald-400 text-[11px] font-bold uppercase tracking-wider">
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>What It Is For (Trading Workflow Role)</span>
            </div>
            <p className="text-[11px] text-[#A6B2C8] leading-relaxed">{item.purpose}</p>
          </div>

          {/* 3. WHAT WE KNOW ABOUT THIS (LIVE EVIDENCE & REAL METRICS) */}
          <div className="rounded-xl border border-cyan-500/20 bg-[#0A0F1E] p-3 space-y-2.5">
            <div className="flex items-center gap-1.5 text-amber-400 text-[11px] font-bold uppercase tracking-wider">
              <Activity className="w-3.5 h-3.5" />
              <span>What We Know About This (Live Proof & Evidence)</span>
            </div>
            <div className="space-y-1.5">
              {item.whatWeKnow.map((row, idx) => (
                <div key={idx} className="p-2 rounded bg-[#060912] border border-[#1A233A]">
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] text-[#848e9c] uppercase font-bold">{row.label}</span>
                    <span className="text-[11px] text-white font-mono font-bold text-cyan-200">{row.value}</span>
                  </div>
                  {row.detail && (
                    <p className="text-[9.5px] text-[#848e9c] mt-1 leading-snug border-t border-[#12192A] pt-1">
                      {row.detail}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* 4. MATHEMATICS / CODE SNIPPET (IF APPLICABLE) */}
          {(item.mathematicsOrRule || item.codeSnippet) && (
            <div className="rounded-xl border border-purple-500/20 bg-[#0A0F1E] p-3 space-y-2">
              <div className="flex items-center gap-1.5 text-purple-400 text-[11px] font-bold uppercase tracking-wider">
                <Code2 className="w-3.5 h-3.5" />
                <span>Underlying Math & Rule Definition</span>
              </div>
              {item.mathematicsOrRule && (
                <div className="p-2 rounded bg-[#060912] border border-[#1A233A] text-[10px] text-purple-200 font-mono">
                  {item.mathematicsOrRule}
                </div>
              )}
              {item.codeSnippet && (
                <pre className="p-2.5 rounded bg-[#04060C] border border-[#1A233A] text-[9.5px] text-cyan-300 font-mono overflow-x-auto">
                  {item.codeSnippet}
                </pre>
              )}
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="p-3 border-t border-[#1A233A] bg-[#090D1A] flex items-center justify-between gap-2">
          {item.deepDiveTab && onSelectTab && (
            <Button
              size="sm"
              onClick={() => {
                onSelectTab(item.deepDiveTab!);
                onClose();
              }}
              className="w-full h-8 text-[10px] font-mono bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-500/30"
            >
              Open Dedicated Details Tab
              <ArrowRight className="w-3 h-3 ml-1.5" />
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={onClose}
            className="h-8 text-[10px] font-mono border-[#1A233A] bg-[#0E1524] text-[#848e9c] hover:text-white px-3"
          >
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
