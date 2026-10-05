# VEB — Volatility-Expansion Breakout (Shadow Strategy Spec v1)

**Author:** drafted 2026-10-05 (post-#19, honest shadow pipeline available)
**Status:** SPEC — awaiting user approval before implementation
**Pipeline:** straight to shadow cohort (never trades until verdict PROMOTE + user decision)

---

## 1. Problem this strategy solves

Every observed losing streak in the paper ledger (Sep-17: −₹1,342, Sep-18: −₹1,565)
and the G14/G15 gate blockade happened in **Sideways regime** — the existing
strategy set is mean-reversion-heavy and long-biased. The system has:

- No strategy that *profits from* volatility expansion (Volatile regime pauses
  MRF/VC/PTC today — the roster goes defensive, never offensive).
- No strategy that reliably trades the SHORT side of a bearish trend day.

VEB fills both gaps with one mechanism: volatility expansion is
direction-agnostic — it trades whichever side the range breaks.

## 2. Core thesis

When India VIX rises sharply intraday (vol expansion) AND a stock's range
breaks with ATR-expanded candles, the move continues more often than it
fades (inertia of vol regimes). Mean-reversion strategies are disabled by
the same conditions — VEB is their complement, not their competitor.

## 3. Entry logic

All conditions must hold at evaluation time (5-min candles):

1. **Vol expansion (index level):** VIX > `vix_floor` (default 16.0)
   AND VIX 30-min change > `vix_slope_min` (default +3%).
2. **Range definition:** prior 45 minutes of 5-min candles define
   `range_high` / `range_low` (9 candles, excludes the breakout candle).
3. **Breakout:** close beyond range boundary by ≥ `breakout_buffer_atr_mult`
   × ATR(14) (default 0.25 ATR — avoids wick-fakes).
4. **Expanded candle:** breakout candle range (H−L) ≥ `expansion_atr_mult`
   × ATR(14) (default 1.2 — the candle itself must be volatile).
5. **Volume confirmation:** breakout candle volume ≥ `volume_mult`
   × 20-candle average (default 1.5×).
6. **Regime gate:** engine regime ∈ {Volatile, Bear} OR conditions 1–4 all
   met (VEB may fire in any regime if vol expansion is real — regime map
   gives a weight bonus in Volatile/Bear, weight 0.5 elsewhere).

Direction: LONG on upside break, SHORT on downside break.

## 4. Risk management

| Parameter | Default | Rationale |
|---|---|---|
| Stop-loss | `sl_atr_mult` × ATR(14), default **1.5** | Vol-expanded candles need wider stops; fixed ₹ stops get wicked |
| Target | `target_rr` × SL distance, default **2.0** | Breakout continuation asymmetry; also satisfies G14-style economics |
| Time stop | `time_stop_minutes` default **60** | Breakouts that don't run in an hour are dead |
| Breakeven move | after 1.0R → SL to entry (`breakeven_at_r` 1.0) | Standard trend-following protection |
| Max trades/symbol/day | 2 (G22 re-entry guard applies automatically) | Churn protection inherited from R3 |
| Entry window | 09:45–14:30 IST | Skip opening noise; no fresh breakouts into close |

Position sizing: standard engine sizer (risk-per-trade % of capital ÷ SL
distance). **No size boost in high VIX** — G3 cap still applies; the wider
ATR stop already reduces share count.

## 5. Fees / microstructure

- G0 penny gate applies automatically (sub-₹50 rejected) — volatile movers
  under ₹50 are exactly the slippage trap the gate exists for.
- Min target must clear 2.5× round-trip fees (economic-viability floor,
  backlog issue-9) — verified in cost pre-check G17.
- Fee-adjusted breakeven for verdicts: measured after n≥100, provisional
  breakeven = 100/(1+2.0) = **33.3% WR** (target RR 2.0).

## 6. Shadow validation plan

- Register as `VEB` in the shadow cohort (`strategy_shadow_mode`), weight 0.
- Cohort accumulates honest samples (effective_n post-#19).
- Verdict gate: MIN_SAMPLE=100 effective setups, ±3pp margin vs breakeven.
- **Regime attribution matters most:** `get_regime_attribution()` splits VEB's
  record by regime — success criterion is Volatile/Bear PF ≥ 1.25; Sideways
  performance is irrelevant (it should barely fire there).
- Success = PROMOTE_CANDIDATE verdict with Volatile/Bear sub-sample PF ≥ 1.25.

## 7. Implementation plan (one commit each, protocol compliant)

1. `strategies/v2/veb.py` — pure signal logic + unit tests with synthetic
   candles (breakout-long, breakout-short, wick-fake rejection, low-vol
   rejection, volume-fail rejection).
2. Config: `strategy_activation` entries (Volatile: active+weight 1.2,
   Bear: active+weight 1.0, others weight 0.5/0.0), `strategy_shadow_mode`
   append VEB, risk params via `config.get` fallbacks — **no defaults.yaml
   schema change** beyond list entries.
3. Register in strategy registry + strategies UI config (name, description,
   tags: advanced/shadow).

Estimated effort: 1 session (strategy ~150 lines + tests ~200 lines).

## 8. Explicit non-goals

- No options underwriting (P7 later — this is the equity-side vol play).
- No overnight holds (intraday only, square-off enforced engine-side).
- No pyramiding into the move (single entry per setup; re-entry governed
  by G22).

## 9. Open questions for the user

1. VIX floor 16 vs 18? (16 = more samples, earlier validation; 18 = stricter,
   cleaner "hostile tape" definition)
2. Should VEB be allowed in Bull regime when vol expands (earnings-day
   breakouts)? Default spec says yes at weight 0.5.
3. Priority vs bearish trend-continuation strategy (second candidate in the
   approved plan) — build VEB first, or both specs in parallel?
