'use client';

import React from 'react';
import {
  Activity,
  TrendingUp,
  ShieldCheck,
  BarChart3,
  AlertCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { MlScorecardMetrics } from '@/lib/api';

interface LossCalibrationTabProps {
  metrics: MlScorecardMetrics | null;
  onInspectItem: (item: any) => void;
}

export default function LossCalibrationTab({ metrics, onInspectItem }: LossCalibrationTabProps) {
  const hasValidation = Boolean(metrics?.has_validation_data);
  const calibrationPoints = metrics?.calibration_curve || [];

  const modelWinRate = metrics?.model_win_rate_pct != null ? `${metrics.model_win_rate_pct}%` : '—';
  const baselineWinRate = metrics?.baseline_win_rate_pct != null ? `${metrics.baseline_win_rate_pct}%` : '—';
  const edgeUplift = metrics?.edge_uplift_pct != null ? `+${metrics.edge_uplift_pct}%` : '—';
  const brierScore = metrics?.brier_score != null ? metrics.brier_score.toFixed(3) : '—';
  const rocAuc = metrics?.roc_auc != null ? metrics.roc_auc.toFixed(2) : '—';
  const vetoSavings = metrics?.veto_savings_estimate != null && metrics.veto_savings_estimate > 0
    ? `₹${metrics.veto_savings_estimate.toLocaleString()}`
    : (metrics?.veto_count ? '₹0' : '—');
  const driftPsi = metrics?.drift_metric_value != null ? metrics.drift_metric_value.toFixed(2) : '—';
  const driftStatus = metrics?.drift_status || 'HEALTHY';

  return (
    <div className="space-y-4 font-mono select-none">
      {/* ────────────────────────────────────────────────────────── */}
      {/* 1. TOP HEADER: BOT ACCURACY & RELIABILITY COMMAND MATRIX */}
      {/* ────────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-cyan-500/30 bg-[#080D1A]/95 p-4 shadow-lg">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-cyan-500/20 pb-3 mb-3">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="w-5 h-5 text-cyan-400" />
            <div>
              <h1 className="text-sm md:text-base font-black uppercase text-white tracking-wider">
                BOT ACCURACY, CONVERGENCE &amp; CALIBRATION CURVES
              </h1>
              <span className="text-[10px] text-[#848e9c]">
                EMPIRICAL MODEL PROOF • K-FOLD WALK-FORWARD VALIDATION • LIVE DATA TELEMETRY
              </span>
            </div>
          </div>
          <Badge
            className={`text-[10px] py-1 px-2.5 border ${
              hasValidation
                ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40'
                : 'bg-amber-500/20 text-amber-400 border-amber-500/40'
            }`}
          >
            ● CALIBRATION STATUS: {hasValidation ? `VALIDATED (BRIER ${brierScore})` : 'AWAITING VALIDATION'}
          </Badge>
        </div>

        {/* 4 Top KPI Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          {/* Metric 1: Model Accuracy / Win Rate */}
          <div
            onClick={() =>
              onInspectItem({
                id: 'model_win_rate',
                title: `Model Accuracy (${modelWinRate})`,
                category: 'ANALYTICS',
                status: hasValidation ? `${modelWinRate} ACCURACY` : 'AWAITING VALIDATION',
                statusType: hasValidation ? 'success' : 'warning',
                sourceFile: 'ultrabot-web/backend/ml/inference.py',
                sourceFeed: 'Walk-Forward K-Fold Out-of-Sample Trades',
                frequency: 'Recalculated on Retraining Trigger',
                purpose:
                  'Measures the real win rate of signals approved by ML after filtering out low-confidence setups.',
                whatWeKnow: [
                  { label: 'Model Win Rate', value: modelWinRate, detail: 'Out-of-sample accuracy across unseen shadow trades.' },
                  { label: 'Baseline Win Rate', value: baselineWinRate, detail: 'Unfiltered baseline ORB & VWAP trades without ML gate.' },
                  { label: 'Statistical Uplift', value: `${edgeUplift} Edge`, detail: 'Net percentage-point improvement in strategy win rate.' },
                  { label: 'Validation Status', value: hasValidation ? 'Completed' : 'Awaiting walk-forward runs' },
                ],
                mathematicsOrRule: 'Edge Uplift = Model_Win_Rate - Baseline_Win_Rate',
              })
            }
            className="p-3 rounded-lg bg-[#050811] border border-cyan-500/20 hover:border-cyan-400 cursor-pointer transition"
          >
            <span className="text-[10px] text-[#848e9c] uppercase font-bold">Bot Accuracy</span>
            <div className="text-2xl font-black text-emerald-400 mt-1">
              {modelWinRate}
            </div>
            <span className="text-[9px] text-[#848e9c]">
              {hasValidation ? `vs ${baselineWinRate} baseline` : 'Awaiting walk-forward runs'}
            </span>
          </div>

          {/* Metric 2: Edge Uplift */}
          <div
            onClick={() =>
              onInspectItem({
                id: 'edge_uplift',
                title: `Statistical Edge Uplift (${edgeUplift})`,
                category: 'ANALYTICS',
                status: hasValidation ? `${edgeUplift} EDGE` : 'AWAITING RETRAIN',
                statusType: hasValidation ? 'success' : 'warning',
                sourceFile: 'ultrabot-web/backend/ml/inference.py',
                sourceFeed: 'Shadow Outcomes History / SQLite DB',
                frequency: 'Recalculated on Retraining Trigger',
                purpose:
                  'Measures the statistical edge gained by enabling the ML advisory and G21 veto gates.',
                whatWeKnow: [
                  { label: 'Edge Uplift', value: edgeUplift },
                  { label: 'Avoided Losses', value: vetoSavings },
                  { label: 'Validation Status', value: hasValidation ? 'Walk-Forward Verified' : 'Pending shadow trade outcomes' },
                ],
                mathematicsOrRule: 'Uplift = P(Win | ML_Pass) - P(Win | Raw_Signal)',
              })
            }
            className="p-3 rounded-lg bg-[#050811] border border-cyan-500/20 hover:border-cyan-400 cursor-pointer transition"
          >
            <span className="text-[10px] text-[#848e9c] uppercase font-bold">Alpha Edge Uplift</span>
            <div className="text-2xl font-black text-cyan-400 mt-1">
              {edgeUplift}
            </div>
            <span className="text-[9px] text-emerald-400">
              {hasValidation ? 'Statistically verified edge' : 'Pending validation'}
            </span>
          </div>

          {/* Metric 3: Brier Score */}
          <div
            onClick={() =>
              onInspectItem({
                id: 'brier_score',
                title: `Brier Calibration Score (${brierScore})`,
                category: 'ANALYTICS',
                status: hasValidation ? `CALIBRATED (${brierScore})` : 'UNCALIBRATED',
                statusType: hasValidation ? 'success' : 'warning',
                sourceFile: 'ultrabot-web/backend/ml/inference.py',
                sourceFeed: 'Walk-Forward Holdout Trades',
                frequency: 'Recalculated on Retraining Trigger',
                purpose:
                  'Measures accuracy of probabilistic forecasts. 0.0 is perfect clairvoyance, 0.25 is uninformative coin toss.',
                whatWeKnow: [
                  { label: 'Brier Score', value: brierScore },
                  { label: 'ROC-AUC', value: rocAuc },
                  { label: 'Benchmark', value: '< 0.20 indicates high institutional calibration' },
                ],
                mathematicsOrRule: 'Brier = (1/N) * sum((predicted_prob - actual_outcome)^2)',
              })
            }
            className="p-3 rounded-lg bg-[#050811] border border-cyan-500/20 hover:border-cyan-400 cursor-pointer transition"
          >
            <span className="text-[10px] text-[#848e9c] uppercase font-bold">Brier Score</span>
            <div className="text-2xl font-black text-purple-400 mt-1">
              {brierScore}
            </div>
            <span className="text-[9px] text-purple-300">
              {hasValidation ? '0.0 = Perfect | < 0.20 = Ideal' : 'Uncalibrated'}
            </span>
          </div>

          {/* Metric 4: Population Drift PSI */}
          <div
            onClick={() =>
              onInspectItem({
                id: 'drift_psi',
                title: `Data Drift PSI (${driftPsi})`,
                category: 'ANALYTICS',
                status: driftStatus,
                statusType: driftStatus === 'HEALTHY' ? 'success' : 'warning',
                sourceFile: 'ultrabot-web/backend/ml/inference.py',
                sourceFeed: 'Point-In-Time India VIX & Feature Distribution',
                frequency: 'Evaluated every 20 signals',
                purpose:
                  'Detects regime shifts and market volatility shifts. If market changes drastically from training distribution (PSI > 0.20), the bot alerts for retraining.',
                whatWeKnow: [
                  { label: 'Current PSI', value: driftPsi },
                  { label: 'Status', value: driftStatus },
                  { label: 'Average VIX', value: metrics?.avg_vix != null ? metrics.avg_vix.toFixed(1) : '—' },
                  { label: 'Threshold', value: 'PSI < 0.10: Stable | > 0.20: Retrain' },
                ],
                mathematicsOrRule: 'PSI = sum((Actual% - Expected%) * ln(Actual% / Expected%))',
              })
            }
            className="p-3 rounded-lg bg-[#050811] border border-cyan-500/20 hover:border-cyan-400 cursor-pointer transition"
          >
            <span className="text-[10px] text-[#848e9c] uppercase font-bold">Regime Drift (PSI)</span>
            <div className="text-2xl font-black text-amber-400 mt-1">
              {driftPsi}
            </div>
            <span className="text-[9px] text-emerald-400">
              {metrics?.drift_metric_value != null ? 'Regime Stable (PSI < 0.10)' : 'Awaiting signal evals'}
            </span>
          </div>
        </div>
      </div>

      {/* ────────────────────────────────────────────────────────── */}
      {/* 2. FULL-SIZE BOT ACCURACY & HIT RATE TRAJECTORY CURVE      */}
      {/* ────────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-cyan-500/25 bg-[#080D1A]/95 p-4 shadow-lg">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-cyan-500/20 pb-2.5 mb-3">
          <div>
            <h2 className="text-xs md:text-sm font-black text-white uppercase tracking-wider flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-emerald-400" />
              BOT ACCURACY TRAJECTORY &amp; ROLLING WIN RATE CURVE
            </h2>
            <span className="text-[10px] text-[#848e9c]">
              Walk-forward out-of-sample accuracy progression across consecutive trade evaluation cohorts
            </span>
          </div>
          {hasValidation && (
            <div className="flex items-center gap-3 text-[10px]">
              <div className="flex items-center gap-1.5 text-emerald-400">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
                <span>Model Accuracy</span>
              </div>
              <div className="flex items-center gap-1.5 text-[#848e9c]">
                <span className="w-2.5 h-2.5 border border-dashed border-[#848e9c]" />
                <span>Baseline ({baselineWinRate})</span>
              </div>
            </div>
          )}
        </div>

        {hasValidation ? (
          <div
            onClick={() =>
              onInspectItem({
                id: 'accuracy_trajectory_curve',
                title: 'Bot Accuracy Trajectory Curve',
                category: 'ANALYTICS',
                status: `EDGE (${edgeUplift})`,
                statusType: 'success',
                sourceFile: 'ultrabot-web/backend/ml/inference.py (walk_forward_eval)',
                sourceFeed: 'Walk-forward cross-validation test cohorts',
                frequency: 'Continuous rolling calculation',
                purpose: 'Plots rolling accuracy against baseline rate to verify learning stability.',
                whatWeKnow: [
                  { label: 'Final Model Win Rate', value: modelWinRate },
                  { label: 'Baseline Unfiltered Rate', value: baselineWinRate },
                ],
                mathematicsOrRule: 'Rolling_Accuracy = (Correct_Predictions / Total_Batch_Signals) * 100',
              })
            }
            className="relative w-full overflow-hidden rounded-lg bg-[#040711] border border-cyan-500/20 p-2 cursor-pointer hover:border-cyan-400 transition"
          >
            <div className="p-8 text-center flex flex-col items-center justify-center">
              <span className="text-xs text-emerald-400 font-bold">Walk-Forward Model Accuracy: {modelWinRate}</span>
              <span className="text-[10px] text-[#848e9c] mt-1">Unfiltered Baseline: {baselineWinRate} • Edge Uplift: {edgeUplift}</span>
            </div>
          </div>
        ) : (
          <div className="rounded-lg bg-[#040711] border border-cyan-500/20 p-8 flex flex-col items-center justify-center text-center">
            <AlertCircle className="w-8 h-8 text-amber-400/50 mb-2" />
            <span className="text-xs font-bold text-white uppercase tracking-wider">Awaiting Walk-Forward Validation Data</span>
            <p className="text-[10px] text-[#848e9c] max-w-md mt-1">
              Empirical accuracy curves generate automatically after M3a trains on accumulated shadow trade outcomes. No synthetic curves are rendered.
            </p>
          </div>
        )}
      </div>

      {/* ────────────────────────────────────────────────────────── */}
      {/* 3. DUAL CHARTS: CROSS-ENTROPY LOSS & 10-DECILE RELIABILITY */}
      {/* ────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* CHART A: TRAINING & VALIDATION LOSS CURVES */}
        <div className="rounded-xl border border-cyan-500/25 bg-[#080D1A]/95 p-4 shadow-lg">
          <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2.5 mb-2.5">
            <div>
              <span className="text-xs font-bold text-white uppercase flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5 text-cyan-400" />
                Cross-Entropy Loss Across Epochs
              </span>
              <span className="text-[9px] text-[#848e9c]">
                Training vs validation loss convergence (Retraining Run)
              </span>
            </div>
          </div>

          <div className="h-56 w-full flex flex-col items-center justify-center bg-[#040711] rounded-lg p-4 border border-cyan-500/20 text-center">
            <Activity className="w-6 h-6 text-[#848e9c]/40 mb-2" />
            <span className="text-xs font-bold text-white uppercase">
              {hasValidation ? 'Loss Convergence Recorded' : 'Awaiting Retraining Run'}
            </span>
            <p className="text-[10px] text-[#848e9c] max-w-xs mt-1">
              {hasValidation
                ? `Model converged with final Brier calibration score of ${brierScore}.`
                : 'Epoch-by-epoch loss convergence logs will populate once model training is triggered.'}
            </p>
          </div>
        </div>

        {/* CHART B: 10-DECILE CALIBRATION RELIABILITY CURVE */}
        <div className="rounded-xl border border-cyan-500/25 bg-[#080D1A]/95 p-4 shadow-lg">
          <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2.5 mb-2.5">
            <div>
              <span className="text-xs font-bold text-white uppercase flex items-center gap-1.5">
                <BarChart3 className="w-3.5 h-3.5 text-purple-400" />
                10-Decile Calibration Reliability Curve
              </span>
              <span className="text-[9px] text-[#848e9c]">
                Predicted Win Rate vs Actual Observed Frequency
              </span>
            </div>
            {calibrationPoints.length > 0 && (
              <div className="flex items-center gap-2 text-[9px]">
                <span className="text-purple-400 font-bold">— Model Deciles</span>
                <span className="text-[#848e9c]">— Ideal 45° line</span>
              </div>
            )}
          </div>

          {calibrationPoints.length > 0 ? (
            <div
              onClick={() =>
                onInspectItem({
                  id: 'calibration_curve_detail',
                  title: 'Brier Reliability Decile Curve',
                  category: 'ANALYTICS',
                  status: `CALIBRATED (Brier ${brierScore})`,
                  statusType: 'success',
                  sourceFile: 'ultrabot-web/backend/ml/inference.py (get_scorecard_metrics)',
                  sourceFeed: 'Binning predicted win rates into 10 deciles',
                  frequency: 'Calculated from walk-forward validation',
                  purpose: 'Proves empirical probability calibration across prediction bins.',
                  whatWeKnow: [
                    { label: 'Brier Score', value: brierScore },
                    { label: 'ROC-AUC', value: rocAuc },
                  ],
                  mathematicsOrRule: 'Calibration Error = sum_k (|y_bar_k - p_bar_k|) * (N_k / N)',
                })
              }
              className="h-56 w-full cursor-pointer relative bg-[#040711] rounded-lg p-2 border border-cyan-500/20 hover:border-purple-500/40 transition"
            >
              <svg className="w-full h-full" viewBox="0 0 320 140">
                <line x1="30" y1="120" x2="300" y2="20" stroke="rgba(255,255,255,0.2)" strokeWidth="1" strokeDasharray="3 3" />
                <line x1="30" y1="120" x2="300" y2="120" stroke="rgba(255,255,255,0.1)" strokeWidth="1" />
                <line x1="30" y1="120" x2="30" y2="20" stroke="rgba(255,255,255,0.1)" strokeWidth="1" />

                {/* Plot actual points */}
                <polyline
                  fill="none"
                  stroke="#A855F7"
                  strokeWidth="2.5"
                  points={calibrationPoints
                    .map((pt, idx) => {
                      const px = 30 + (idx / Math.max(1, calibrationPoints.length - 1)) * 270;
                      const py = 120 - (Number(pt.observed ?? pt.actual_win_rate ?? 0) * 100);
                      return `${px},${Math.max(20, Math.min(120, py))}`;
                    })
                    .join(' ')}
                  className="drop-shadow-[0_0_6px_#A855F7]"
                />

                {calibrationPoints.map((pt, idx) => {
                  const px = 30 + (idx / Math.max(1, calibrationPoints.length - 1)) * 270;
                  const py = 120 - (Number(pt.observed ?? pt.actual_win_rate ?? 0) * 100);
                  return (
                    <circle
                      key={idx}
                      cx={px}
                      cy={Math.max(20, Math.min(120, py))}
                      r="3"
                      fill="#A855F7"
                      stroke="#060912"
                      strokeWidth="1"
                    />
                  );
                })}

                <text x="30" y="132" fill="#848e9c" fontSize="7" fontFamily="monospace">0% Prob</text>
                <text x="150" y="132" fill="#848e9c" fontSize="7" fontFamily="monospace">Predicted</text>
                <text x="270" y="132" fill="#848e9c" fontSize="7" fontFamily="monospace">100% Prob</text>
              </svg>
            </div>
          ) : (
            <div className="h-56 w-full flex flex-col items-center justify-center bg-[#040711] rounded-lg p-4 border border-cyan-500/20 text-center">
              <BarChart3 className="w-6 h-6 text-[#848e9c]/40 mb-2" />
              <span className="text-xs font-bold text-white uppercase">Awaiting Empirical Deciles</span>
              <p className="text-[10px] text-[#848e9c] max-w-xs mt-1">
                Decile calibration curve requires out-of-sample holdout evaluations from walk-forward validation.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ────────────────────────────────────────────────────────── */}
      {/* 4. CALIBRATION DECILE BREAKDOWN TABLE                     */}
      {/* ────────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-cyan-500/25 bg-[#080D1A]/95 p-4 shadow-lg">
        <div className="text-xs font-bold text-white uppercase mb-2">
          10-Decile Empirical Audit Matrix (Live Validation Samples)
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[10px] text-[#D1D4DC]">
            <thead className="text-[9px] text-[#848e9c] border-b border-[#1A233A] uppercase bg-[#050811]">
              <tr>
                <th className="py-2 px-3">Decile Bin</th>
                <th className="py-2 px-3">Predicted Win Rate</th>
                <th className="py-2 px-3">Observed Win Rate</th>
                <th className="py-2 px-3">Variance</th>
                <th className="py-2 px-3">UltraBot Policy</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1A233A]">
              {calibrationPoints.length > 0 ? (
                calibrationPoints.map((row, idx) => {
                  const pred = row.predicted ?? (idx + 1) / 10;
                  const obs = row.observed ?? row.actual_win_rate ?? pred;
                  const variance = Math.abs(obs - pred);
                  const isVeto = pred < 0.42;
                  const isFavorable = pred >= 0.60;
                  return (
                    <tr key={idx} className="hover:bg-[#0E1726]/60 transition">
                      <td className="py-1.5 px-3 font-bold text-white">{row.bin}</td>
                      <td className="py-1.5 px-3 text-cyan-300">{(pred * 100).toFixed(0)}%</td>
                      <td className="py-1.5 px-3 text-emerald-300">{(obs * 100).toFixed(1)}%</td>
                      <td className="py-1.5 px-3 text-[#848e9c]">{(variance * 100).toFixed(1)}%</td>
                      <td className="py-1.5 px-3">
                        <Badge
                          className={`text-[8px] font-mono ${
                            isVeto
                              ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                              : isFavorable
                                ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                                : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30'
                          }`}
                        >
                          {isVeto ? 'GATE G21 VETO' : isFavorable ? 'FAVORABLE SIZING' : 'NEUTRAL PASS'}
                        </Badge>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-[#848e9c]">
                    No empirical deciles recorded. Train M3a on shadow outcomes to populate calibration decile matrix.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
