# BTC — Bearish Trend Continuation (Shadow Strategy Spec v1 — DRAFT)

**Drafted:** 2026-10-06 (parallel to VEB per approved sequencing)
**Status:** DRAFT SPEC — implementation gated on VEB's first ~2 weeks of shadow behavior
**Pipeline:** shadow cohort (never trades until verdict PROMOTE + user decision)

---

## 1. Problem

VEB covers *volatility expansion* (shock days). BTC covers the other bearish
mode: **grinding trend-down days** — lower highs, lower lows, VIX elevated
but NOT spiking, where breakouts quickly fail but the path of least
resistance stays down. The current roster is structurally long-biased; on
these days it either sits out or fades the trend (VR/BBR losing patterns).

## 2. Core thesis

On a day when the index (Nifty) is trending down and a stock prints a
lower-high structure on 15m/60m, pullbacks to the descending structure are
continuation entries more often than reversals. Sell the pullback, not the
low.

## 3. Entry logic (5-min scan, 15m structure)

1. **Index filter:** Nifty day-change <= -0.3% OR Nifty below VWAP with
   negative slope (engine provides regime + index context).
2. **Structure:** last 3 swing highs on 15m strictly descending
   (lower-high), last swing low broken (lower-low) — classic downtrend
   continuation structure.
3. **Pullback entry:** price retraces up to the 15m 20-EMA / broken-support
   retest zone (top third of last impulse leg) and prints a 5-min
   rejection candle (close back below the zone, bearish close).
4. **Momentum confirm:** 5-min RSI(14) in 35–60 band (not oversold — don't
   chase exhaustion) and falling.
5. **Volume:** rejection candle volume >= 1.2x 20-avg (lighter than VEB —
   continuation, not shock).
6. **Regime:** Bear full weight; Sideways 0.5 (index-down days classify
   sideways sometimes); Volatile 0 (that's VEB's tape — no overlap).

Direction: SHORT only. No long side.

## 4. Risk

| Parameter | Default | Notes |
|---|---|---|
| SL | above the pullback swing high + 0.25 ATR buffer | structure invalidation, not ATR-fixed |
| Target | 2.0R (prior swing low first, then measured move) | G17 fee floor applies |
| Time stop | 75 min | continuation decays fast |
| Breakeven | at 1.0R | standard |
| Max entries | 2/symbol/day (G22 applies) | churn protection inherited |

## 5. Shadow validation

- Cohort `BTC` in strategy_shadow_mode; verdict at effective_n >= 100.
- **Regime-attributed success: Bear-regime PF >= 1.25.** Sideways samples
  are secondary evidence; Volatile samples should be ~zero (regime weight 0).
- Overlap watch with VR (both can short): BTC requires the 15m structure;
  VR is band-based. If their cohorts correlate > 0.8 on losing days,
  retire one — verdict layer handles it.

## 6. Implementation sketch (when gated in)

- `strategies/v2/btc.py` (~180 lines): swing detection on 15m resampled
  frame + 5m pullback trigger; `utils/indicators.py` has EMA/RSI already;
  swing detection is the only new primitive (~30 lines, unit-testable).
- Tests: structure detection (descending/ascending/flat rejection),
  pullback-entry trigger, zone-rejection candle, regime weight 0 in
  Volatile, geometry sanity.
- Registration identical to VEB (registry + UI config + shadow list +
  snapshot refresh).

## 7. Gate to implementation (per approved sequencing)

Do NOT implement until BOTH:
1. VEB has >= 2 weeks of shadow samples and no deployment surprises, AND
2. VR/BBR verdicts (due this week) are acted on — BTC's short-side overlap
   with VR must be judged against a *surviving* VR, not a stale one.
