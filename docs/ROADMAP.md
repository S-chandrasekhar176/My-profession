# UltraBot Roadmap — v1.0 (2026-09-08)

Guiding principle: **each phase introduces exactly one new class of risk, and must prove it
controlled before the next unlocks.** Evidence over confidence; calendar (sample accumulation
and observation windows) is the constraint, not code.

**Current phase: P1 — M2.5 Stabilize & Accumulate** (v0.4.13 merged, first split-run day today)

---

## Phase summary

| # | Phase | Window | Exit gate |
|---|---|---|---|
| P0 | v0.4.13 first paper day + split-run | 2026-09-08 | clean handoff ×1 |
| P1 | M2.5 Stabilize & accumulate | 2–4 sessions | ≥100 realtime-resolved samples, zero data loss, 2 clean handoffs |
| P2 | F&O data foundation (parallel) | start anytime | 2+ wks clean chain history; our Greeks ≈ broker's |
| P3 | M3a — model build, offline | 1–2 evenings after P1 | walk-forward + calibration + uplift pass |
| P4 | M3b — model as shadow advisor | 2–4 wks | no production drift + realized uplift |
| P5 | M3c — model earns veto | 1–2 wks | P&L flat-or-better, fewer losers → paper-complete |
| P6 | M4 pre-flight — trading API switch | few sessions | live mechanics verified with pocket-change orders |
| P7 | M4 — live pilot (equity cash, small) | 2–3 wks | 10+ clean sessions, live ≈ paper within slippage |
| P8 | F&O paper → live | after P7 | 100+ resolved options samples, gates behaving |
| P9 | M5 — scale & operate | ongoing | 2 green weeks per capital step |

---

## Phase details

### P0 — v0.4.13 first production paper day (today)
Split-run protocol: sandbox Part 1 (09:15–12:30), user machine Part 2 (~12:45–15:30).
12:30 handoff = sanitized DB via exporter (`broker_credentials` deleted, auto-verified) +
handoff note; sandbox backend fully stopped before user backend starts (Telegram 409 / Fyers
conflict). 15:35 EOD backup auto-copies.

### P1 — M2.5 Stabilize & Accumulate
Daily split-run + EOD ritual (`/pnl` vs DB reconciliation, backup verification, handoff).
Production-soaks the v0.4.13 fixes: 15-min DB backups, rehydration on restart, canary silence,
G20 sizing pre-check, counter restore.
**Exit:** ≥100 realtime-resolved shadow samples · zero data-loss incidents · 2 clean handoffs ·
5+ clean sessions.

### P2 — F&O Data Foundation (parallel track, no dependency on P1)
- Option chain poller via Fyers API (`optionchain`, `strikecount` ≤50, **`greeks=1`** →
  delta/gamma/theta/vega/IV — broker values are the primary source).
- Thin Black-Scholes module as **verifier** (sanity-check broker Greeks/IV) and **scenario
  tool** (what-if spot/IV shifts for the theta-budget gate) — not a data source.
- Tiered snapshot recorder: tradable strikes (ATM±3) ~3s, full chain ~60s → SQLite, rides the
  15-min backup job. Read-only ingestion: zero trading risk.
- Why now: option chain state (IV percentile, term structure, IV-vs-realized) is high-value ML
  feature material even for the equity strategy, and data pipelines need soak time.
**Exit:** 2+ weeks clean chain history, Greeks verified vs broker, poller survives recycles.

### P3 — M3a Model Build (offline)
Dataset from `shadow_outcomes` + point-in-time features (leakage-proof by M1 design). Simple
model (logistic regression / gradient boosting) predicting P(win) and expected R:R. **Walk-
forward** validation only (train past → test future). Calibration check + uplift vs rules-only.
**Exit:** offline precision holds across windows; calibration sane; positive uplift.

### P4 — M3b Shadow Advisor
Model wired as `G21_ML` in advisory mode: scores every signal, shown on Telegram cards,
blocks nothing. Paired decisions accumulate (rules-only vs rules+ML) with trivial rollback.
**Exit:** production precision matches offline validation (no drift) + realized uplift.

### P5 — M3c Model Earns Veto
Enforce conservative threshold (block only the clearly-bad tail, e.g. score < 0.35). Every
blocked signal gets hindsight review (what would it have done?) — false blocks cost money too.
**Exit:** N sessions flat-or-better P&L with fewer losers → paper-complete with ML.

### P6 — M4 Pre-flight: Trading API Switch
The non-trading API is replaced by the **trading API** (order execution + order-placement
permission granted). Scope: trading-app credentials/token, **webhooks for order & trade
events**, tiny-qty live place/modify/cancel tests, partial fills, order rejections, charges
model (brokerage/STT/slippage) wired into P&L truth, risk envelope config (per-trade cap, max
open positions, **daily max-loss kill switch**, max trades/day), broker↔DB reconciliation.
**Exit:** all live-path mechanics verified with pocket-change orders.

### P7 — M4 Live Pilot
Equity cash, smallest size, 1–2 trades/day, ML gate on, 10+ observed sessions. The pilot
validates *mechanics*, not profitability. Daily broker-statement reconciliation.
**Exit:** 10+ clean sessions; live ≈ paper within slippage expectations.

### P8 — F&O Paper → Live
Strike Selector Engine: **delta band 0.50–0.65 (slight ITM–ATM)**, liquidity gates (OI,
spread %, volume), weekly expiries, **no expiry-day entries in v1**, **theta-budget gate**
(expected move capture > theta + spread + charges, checked before the card). Long-only v1
(defined risk = premium); index options first (cash-settled — stock options are physically
settled in India). Greeks-aware exits; own shadow ledger → own ML gate later.
**Exit:** 100+ resolved options samples; selector and gates behaving within spec.

### P9 — M5 Scale & Operate
Capital steps ~2× with minimum two green weeks per step. Infra migration off the sandbox to a
stable host (VPS / user machine + supervisor — sandbox recycles are unacceptable for real
money). Weekly reports, alert tuning, DB retention. Shorts and second strategy only after the
first is boring-stable.

---

## Decision points (user gates)
1. **P6 go** — trading API enablement (real-money infrastructure).
2. **P7 go** — first live capital, size choice.
3. **Every P9 scale step.**

## Timeline honesty
Live pilot ≈ **4–7 weeks out** from 2026-09-08. The binding constraint is calendar: sample
accumulation (P1) and observation windows (P4/P5/P7) cannot be rushed; the code is not the
bottleneck.
