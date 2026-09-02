# Scalping Design — FINALIZED (implementation deferred to v0.5.0)

**Status:** Design-locked. No code in this document ships in v0.4.6.
**Base:** v0.4.6 (443 files, 713/713 tests passing)
**Scope of this document:** closes every open item from the scalping design
thread — the 1m research track promotion gates and the first scalp-family
prototypes — so v0.5.0 implementation can start with zero design questions
outstanding.

---

## 1. Decisions already locked (prior sessions — recorded here for one-source-of-truth)

| # | Decision | Lock rationale |
|---|----------|----------------|
| L1 | **No global intraday/scalp mode switch.** The system never flips a single "mode" bit. Instead a 3-layer horizon architecture (Section 2) lets scalp and intraday strategies coexist in one engine. | A mode switch is a false dichotomy here: the engine, feed, risk gates and paper broker are shared infrastructure; only *which strategies may fire* and *how long they hold* differ. A switch would fork config, gates and UI for no benefit and would make failover semantics ambiguous. |
| L2 | **5m intraday stays the trading core; 1m scalping is a research track.** The seven live v2 strategies (ORB, MB, PTC, VC, SIC, MRF, TRS) keep the 5m frame and all capital. Scalping earns capital only through the promotion ladder (Section 3). | Measured fee-adjusted breakevens (ORB 36.8 · MB 36.1 · PTC 39.4 · SIC 37.8 · VC 39.8 · MRF 50.2 · TRS 31.0) exist for the 5m core only. Nothing is known about 1m economics yet — capital follows evidence, never enthusiasm. |
| L3 | **Scalp/intraday boundary is TRADE HOLD TIME, not chart timeframe:** hold < 15 min → scalp class; 15 min → time-stop → intraday class; everything force-flattened at 15:15 (`auto_squareoff_time`). The boundary is config-tunable, not a law. | A 1m-chart strategy holding 40 minutes is an intraday trade with extra noise; a 5m-chart strategy holding 8 minutes is a scalp. Classification belongs to realized/estimated hold, which the P0.5 duration engine already computes. |
| L4 | **Scalping preconditions (all already shipped):** 1m data pipeline (Fyers `FyersCandleFeed`, 1m passthrough + `aggregate_1m_to_5m`, 60s realtime cadence), realistic friction (PaperBroker ~5bps half-spread + size impact; backtest 0.05% slippage + real NSE fees), 1m backtest history (`fetch_fyers_history_candles` — months of 1m bars), shadow ledger + verdict engine (MIN_SAMPLE 100, ±3pp margins). | Established in the planning sessions: scalping dies without 1m data — 5m bars hide intra-bar sequence and deliver entries 3–5 min late, which is the entire scalp horizon. The path was fixed as: 1m pipeline → friction model → shadow-track scalpers → only then capital. P1/P2 shipped exactly this. |

**Layer-2 note (locked with L1):** scalp strategies — live *and shadow* — require the
realtime feed (`is_realtime` true, i.e. Fyers 1m active). On failover to the Yahoo
5m backup they auto-suspend. Section 7 gives the full policy.

---

## 2. The 3-layer horizon architecture (as locked, with v0.5.0 implementation slots)

```
Layer 1  STRATEGY-DECLARED HORIZON          (new class attribute in v0.5.0)
         BaseStrategy subclasses declare:   horizon = 'scalp' | 'intraday'
         Default when absent: 'intraday' — all 21 existing strategies
         inherit it with zero code changes.

Layer 2  FEED-CAPABILITY GATING             (engine scan filter in v0.5.0)
         Scan loop asks: strategy.horizon == 'scalp' AND feed.is_realtime?
         If the active feed is the Yahoo 5m backup, scalp strategies are
         skipped that cycle (live and shadow alike) and a suspension event
         is logged + surfaced on the existing data-source badge.

Layer 3  PER-TRADE CLASSIFICATION           (already shipped — P0.5)
         The duration engine estimates hold time as a range; <15 min (config
         key: execution class boundary, default 15) → scalp class label.
         12–18 min straddlers label by range midpoint, re-labeled by
         realized hold at close. Feeds per-class analytics, verdict stats
         and the per-class risk budgets of Section 5.
```

No new "mode" appears anywhere: config, UI or API. The dashboard gains at
most a class column in trade analytics (v0.5.0 or later — presentation is
open, mechanics are not).

---

## 3. The promotion ladder — FINALIZED

Four sequential gates. A candidate climbs them one at a time and can be
demoted from any rung. **Nothing promotes on a schedule; only on evidence.**

### Gate 0 — Structural (registration, v0.5.0)
- Strategy declares `horizon = 'scalp'`.
- Time stop ≤ 15 min, declared in `risk.time_stop_minutes` (per-strategy map).
- Signal TTL ≤ 120 s in `risk.strategy_ttl_seconds` (scalp entries are worthless stale).
- Registered **shadow-only**: name added to `strategy_shadow_mode`, absent from every
  regime activation map — the existing P2 mechanism. Zero capital possible at Gate 0.

### Gate 1 — Backtest evidence (offline, v0.5.x)
Run on Fyers 1m history (`fetch_fyers_history_candles`) with the existing
friction model (real NSE fees + 0.05% slippage):
- **≥ 300 resolved trades** across ≥ 3 months of 1m bars;
- **cost-adjusted win-rate ≥ candidate breakeven + 5pp** — breakeven measured
  by this same backtest's own economics (Section 6), never borrowed from 5m numbers;
- **Robustness:** backtest window split in halves — both halves ≥ breakeven;
  no single symbol contributes > 30% of trades (top-20 watchlist concentration guard);
- **Regime honesty:** profitable in ≥ 2 of {Bull, Bear, Sideways} as detected by the
  existing regime detector, OR explicitly regime-gated in its spec (e.g. SVR trades
  Sideways only) and profitable in that regime.

Fail → the candidate dies here. Cost: zero capital, one backtest run.

### Gate 2 — Shadow evidence (live data, paper-only, v0.5.x)
The candidate already scans every cycle (P2 shadow ledger). To pass:
- **≥ 100 resolved shadow signals** — the verdict engine's existing MIN_SAMPLE;
- verdict = **PROMOTE_CANDIDATE** (`core/strategy_verdict.py`: win-rate ≥
  breakeven + 3pp on the candidate's measured scalp breakeven);
- shadow record collected **while Fyers 1m was the active feed** (Layer 2 suspends
  shadow scalps on backup feed, so sample integrity is automatic);
- no unresolved data-quality incidents (stale-candle trips, feed flaps) affecting
  > 10% of the sample window.

RETIRE_CANDIDATE (< breakeven − 3pp at ≥ 100 resolved) → candidate retired
permanently; the slot stays empty. KEEP_COLLECTING → wait, max 6 months, then
retire-by-timeout (a scalp strategy that can't assemble 100 signals in 6 months
has no tradeable frequency).

### Gate 3 — Quarter-size live pilot (conditional, earliest v0.6.0)
- **0.25% per-trade risk** — a quarter of the intraday hard floor (`hard_risk_pct`
  1.0, restored in v0.4.6); implemented as a `per_strategy_daily_loss_pct`-style
  override feeding the existing sizer a smaller budget (Section 5).
- **≤ 2 scalp trades/day, ≤ 1 concurrent scalp position**, inside the existing
  `max_open_positions: 3` and `max_daily_trades: 10` budgets.
- **≥ 30 live pilot trades** before any full-size review.
- Demotion triggers: pilot loses > 1% of capital cumulative, or live WR < breakeven
  − 3pp at ≥ 30 trades → back to shadow. Full size after pilot = **0.5%** per trade
  (Section 5) — still half the intraday floor.

---

## 4. First scalp-family prototypes — FINALIZED (3 candidates)

Chosen so each rides a lineage the system already has measured economics or
shadow history for. All three: `horizon='scalp'`, Gate-0 shadow-only in v0.5.0,
F&O universe from the existing top-20 watchlist, entries only while Fyers 1m
is live.

### 4.1 SORB — Scalp Opening-Range Breakout (lineage: ORB, BE 36.8%)
- **Frame:** 1m bars. Opening range = 09:15–09:30 IST high/low (first 15 one-minute bars).
- **Entry:** 1m close beyond the opening range + volume ≥ 1.5× the 20-bar 1m average;
  5m trend gate (price on correct side of the 5m 20-EMA — the higher-timeframe
  confirmation ORB already uses).
- **Window:** 09:30–10:30 only — the duration engine's opening-drive window
  (time-of-day velocity 1.8×) is exactly where short-horizon breakouts have
  the best odds of resolving fast.
- **Stop:** 1.0 × ATR(14) on 1m. **Target:** 1.0R, trailing after 0.6R.
- **Time stop:** 10 min. **TTL:** 90 s. One trade per symbol per morning.

### 4.2 SVR — Scalp VWAP Reversion (lineage: VWAPReversion (dormant, shadow-tracked); economics caution from MRF's 50.2% BE)
- **Frame:** 1m bars against session VWAP.
- **Entry:** price ≥ 2σ above/below session VWAP (σ = rolling 60-bar 1m stdev of
  the VWAP gap) AND reversion tick confirmed (close back inside 2σ).
- **Regime gate:** Sideways only — the existing regime detector must read
  Sideways. Reversion against a trending tape is the fastest way to donate
  money; MRF's 50.2% breakeven shows how expensive reversion economics get.
- **Window:** 10:00–14:30 (never the opening drive, never close chop).
- **Stop:** 1.2 × ATR(14) on 1m. **Target:** 0.8R (VWAP touch), no trail.
- **Time stop:** 8 min. **TTL:** 90 s. **Spread gate:** quote spread ≤ 5bps at entry.

### 4.3 SMB — Scalp Momentum Burst (lineage: MB, BE 36.1 × VC volume confirmation, BE 39.8)
- **Frame:** 1m bars, 5m alignment gate.
- **Entry:** two consecutive 1m bars same direction, each body > 0.25% of price,
  volume ≥ 2× 20-bar 1m average on the second bar; 5m 20-EMA slope aligned.
- **Window:** 09:30–14:30.
- **Stop:** 1.0 × ATR(14) on 1m. **Target:** 1.2R, trailing after 0.7R.
- **Time stop:** 12 min. **TTL:** 120 s.

**Family rules shared by all three (Gate-0 config in v0.5.0):**
```yaml
# risk.time_stop_minutes additions:      SORB: 10, SVR: 8, SMB: 12
# risk.strategy_ttl_seconds additions:   SORB: 90, SVR: 90, SMB: 120
# strategy_shadow_mode additions:        SORB, SVR, SMB        (shadow-only)
```
All existing gates apply unchanged — G7 VIX panic block (`vix_threshold` 20 /
`vix_extreme_threshold` 35), 3-consecutive-loss circuit breaker, per-strategy
daily loss caps, 15:15 square-off, G17 sizing mirror, `min_signal_confidence` 0.6.

---

## 5. Per-class risk budgets — FINALIZED

One capital pool, class-scoped per-trade budgets, shared daily circuit breakers:

| Budget | Intraday (unchanged) | Scalp pilot (Gate 3) | Scalp full (post-pilot) |
|---|---|---|---|
| Per-trade risk | 1.0% (`hard_risk_pct`) | **0.25%** | **0.5%** |
| Max trades/day | shared `max_daily_trades: 10` | +2 of those, max 2 | ≤ 4 of those |
| Concurrent | shared `max_open_positions: 3` | max 1 scalp slot | max 1 scalp slot |
| Daily loss | shared `max_daily_loss_pct: 3` + ₹-cap | same pool | same pool |
| Consec-loss breaker | shared (`max_consecutive_losses: 5`, 30-min cooldown) | counts across classes | same |

Implementation shape (v0.5.0, structure only — `enabled` stays `false` until a
candidate clears Gate 3):
```yaml
execution_classes:
  scalp:
    enabled: false            # flips true only at Gate 3
    pilot_per_trade_risk_pct: 0.25
    full_per_trade_risk_pct: 0.5
    max_trades_per_day: 2
    max_concurrent_positions: 1
    require_realtime_feed: true   # Layer-2 switch
    hold_boundary_minutes: 15     # Layer-3 label boundary (tunable, L3)
  intraday:
    per_trade_risk_pct: 1.0       # mirrors hard_risk_pct — single source, G17 reads the same
```
The sizer is untouched: `PositionSizer` already computes quantity from a given
risk budget — the class budget simply feeds it a smaller number. Kelly chain,
90% usage cap and 25% per-position cap apply identically to both classes.

---

## 6. Scalp economics & breakeven methodology — FINALIZED

- Each candidate's breakeven is **measured, never assumed**: the Gate-1 backtest
  reports its own fee-adjusted breakeven using the same method as the v0.4.1
  economics audit (round-trip NSE fees + slippage at the candidate's actual
  average risk:reward), then that number feeds `core/strategy_verdict.py`'s
  `_breakeven_for` (per-strategy map entry — the engine already supports this).
- **Expectation setting (deliberately honest):** at 0.8–1.2R targets with
  ~5–8bps round-trip friction, scalp breakevens will likely land in the 45–55%
  band — *higher* than most intraday BEs (31–50%). The ladder does **not**
  relax for scalps. If BE+5pp is unreachable, the candidate dies at Gate 1 —
  that is the design working, not failing. Most candidates are expected to die;
  the ladder exists to make dying cheap (one backtest, zero capital).
- The 5m breakeven table (FEE_ADJUSTED_BREAKEVEN) remains intraday-only. Scalp
  entries get their own keys; no cross-contamination in either direction.

---

## 7. Feed failover policy — FINALIZED

| State | Scalp strategies | Intraday core |
|---|---|---|
| Fyers 1m active (`is_realtime` true) | scan live + shadow per Gate status | scan as today (60s cadence) |
| Failed over to Yahoo 5m backup | **auto-suspend** — skipped in scan loop, live *and* shadow; suspension logged; data-source badge shows degraded | continue on 5m aggregate (180s cadence) |
| Open scalp positions at failover moment | exit management continues on whatever data exists — exits never need 1m precision, entries do | unchanged |
| Feed restored | auto-resume next scan cycle; shadow sample integrity preserved (Gate-2 rule: sample counts only realtime-feed signals) | unchanged |

Rationale (locked in the planning sessions): a 1m entry signal arriving 3–5
minutes late on 5m bars is not a delayed scalp — it is a different, worse
trade. Suspending is cheaper than pretending.

---

## 8. Rollout sequencing

| Version | Contents | Live scalping? |
|---|---|---|
| **v0.4.6** (shipped) | Drift remediation only. This document rides *outside* the zip (design-only). | No |
| **v0.5.0** (next) | Layer-1 horizon attr + Layer-2 scan gating + SORB/SVR/SMB registered shadow-only + `execution_classes` config structure + scalp 1m backtest harness + tests for all of it. This doc moves into the repo at `docs/`. | No — impossible (`enabled: false` hard-wired) |
| **v0.5.x** | Evidence accumulation: Gate-1 backtests, Gate-2 shadow samples, verdict reviews on the dashboard. | No |
| **v0.6.0** (conditional) | First promotion review. Any candidate clearing Gates 1+2 → Gate-3 quarter-size pilot. | Only if evidence says so |

## 9. Explicitly out of scope for v0.5.0 (scope guard)
- No live scalping under any circumstances (`execution_classes.scalp.enabled: false`).
- No new capital allocation, no changes to intraday sizing (`hard_risk_pct` stays 1.0).
- No mode-switch UI, no per-class dashboard beyond a class label in trade rows.
- No new data vendors, no WebSocket feed changes.
- No changes to the 7 live v2 strategies or their activation maps.

## 10. Intentionally open (presentation-only, decided at implementation)
- Exact UI placement of the class label / per-class stats tiles.
- Whether `execution_classes` surfaces in the Settings page or config-file only
  (default: config-file only while `enabled: false`).

---

*Design closed. Implementation of Sections 2–5 begins at v0.5.0.*
