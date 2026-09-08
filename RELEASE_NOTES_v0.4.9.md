# UltraBot v0.4.9 — Release Notes (FINAL, consolidated)

Branch: `fri_2026-09-04_v0.4.9` · Base: v0.4.8 (`431a5eb`) · Status: **awaiting user review & merge**

This release consolidates every change from the Sep 3 evening session and the Sep 4 live-trading day
into one reviewable branch. `main` (v0.4.8) was never touched.

---

## 1. Fee display truth (wave-4, Sep 3 evening)

**Bug:** opportunity/entry-time fee display used a hand-rolled single-leg formula (one ₹20 brokerage,
one-leg turnover, intraday STT on the buy leg only) → showed ~₹38.4x while the true NSE round-trip
cost was ~₹61-62 (~60% underestimate). EOD math was already correct (close path records the exact
full round trip); this was display-only, but it misinformed every Telegram opportunity alert.

**Fix:** `core.engine._estimate_entry_round_trip_fees()` now uses the canonical `NSEFeeCalculator`
full round trip at the actual fill price (same model as close path / G17 / EOD). Honest ₹47.20
both-legs floor if the calculator is unavailable; zero-qty → 0.

**Evidence:** `ultrabot-web/backend/evidence/probe_v049_fee_truth.py` — E1 ASIANPAINT old=38.08
(bit-exact repro) vs new=61.34 vs true 61.33; E2-E4 replay of the 2026-09-03 real trades: old
band 38.36-38.49 (the observed 38.4x) vs new 61.61-61.74 (exactly the implied actuals).

## 2. EOD summary fee double-count (wave-4, Sep 3 evening)

**Bug:** `eod_report.py` `_compute_pnl_summary` and `_compute_strategy_breakdown` added the per-order
brokerage column (₹20) on top of `trade.fees`, which already includes both legs' brokerage → phantom
cost inflation in EOD summaries.

**Fix:** both aggregators now use `trade.fees` alone.

**Live proof (Sep 4):** the v0.4.8 EOD PDF showed Total Fees ₹402.85 while the true per-trade fees
sum to ₹242.85 — exactly the ₹160 phantom (₹40 × 4 trades) this fix removes. The PDF renders the
report dict from `eod_report.py`, so this fix cures both the text report and the PDF.

## 3. EOD blotter quantity always 0 (Sep 4, live-found)

**Bug:** `eod_report.py` trade details used `getattr(t, "qty", 0)` but the Trade model column is
`quantity` → Qty showed 0 in the EOD text report and PDF blotter for every trade.

**Fix:** read `quantity` (with `qty` fallback for any legacy rows).

## 4. NSE holiday calendar wrong date (Sep 4, live incident)

**Bug:** `NSE_HOLIDAYS_2026` listed 2026-09-04 as Milad-un-Nabi. Wrong — Milad-un-Nabi 2026 falls
~Aug 26, and NSE traded normally that Friday (live-verified via Fyers 1-minute bars mid-session).
Impact: `on_market_open` skipped at 09:15 IST → watchlist never built → 0 scans 09:15-09:44 (~30 min
of opportunity flow lost, incl. the ORB window).

**Fix:** date removed with an evidence note; recovered mid-session via controlled restart +
`scripts/rebuild_watchlist_live.py` (scanning restored by 09:47). 13/13 market-hours tests pass.

## 5. G19 Min-Move gate (wave-4, Sep 3 evening) — log-only by default

19th risk gate: blocks entries whose expected move doesn't justify round-trip costs
(`expected_move < fee_multiple × round_trip_cost` → reject). **Default mode is `log_only`**:
it shadow-logs `[G19 SHADOW] would-block` verdicts and never blocks. Modes: `log_only` (default) /
`enforce` / `off`; unknown values fall back to `log_only`. Re-checks at actual sized quantity via
the engine. Shadow data accumulates from the first day this build runs; flip to `enforce` only
after a shadow-data review (user decision, suggested Monday EOD).

## 6. Tooling & hygiene

- `requirements.txt`: added missing `fyers-apiv3` (wave-3).
- `scripts/session_watchdog.py`: fixed snapshot-prune crash (`p.mtime` → `p.stat().st_mtime`;
  PosixPath has no `.mtime`). Found live Sep 4 10:28 — snapshots were fine, rotation wasn't.
- Repo neatness: P2-P5 era evidence (screenshots + repro scripts) moved from `evidence/` to
  `archive/evidence-p2-p5/`. The active `ultrabot-web/backend/evidence/probe_v049_fee_truth.py`
  stays (referenced by tests/worklog as wave-4 evidence).

## 7. Known issues NOT in this release (wave-5 candidates)

- **Sector map gaps:** COLPAL/TRENT/etc. missing from the static sector map → G2 groups them as one
  "Unknown" bucket, capping concurrent entries (blocked 36 signals on Sep 4). Safe-conservative.
- `docs/SCALPING_DESIGN_v0.5.0.md` remains design-only (Phase 3, starts after stability gates).

## Test & evidence summary

- 27 new tests in `ultrabot-web/backend/tests/test_v049_fee_truth_g19.py` (fee truth incl. bit-exact
  38.08 repro, EOD double-count identity, G19 all modes/edges/boundary, engine re-check).
- Full suite: 821 passed / 2 pre-existing failures on clean `14314ab` (universe hygiene: TMPV
  Tata-demerger + extra-picks overlap — verified via git stash, unrelated to v0.4.9 changes).
- Live validation Sep 4: first complete EOD PDF (15:35, Telegram push OK), three-way reconciliation
  (PDF blotter ↔ DB ↔ Telegram alerts) exact on all 4 trades; G15/G16/G17/G18/G2 all validated live.

## Files changed (vs v0.4.8)

```
ultrabot-web/backend/core/engine.py                fee-truth entry estimate + G19 re-check
ultrabot-web/backend/core/market_hours.py          2026-09-04 false holiday removed
ultrabot-web/backend/notifications/eod_report.py   fee double-count fix + qty field fix
ultrabot-web/backend/risk/gates/g19_min_move.py    NEW — 19th gate (log-only default)
ultrabot-web/backend/risk/risk_engine.py           G19 registration
ultrabot-web/backend/config/defaults.yaml          g19_mode / min_move_fee_multiple
ultrabot-web/backend/tests/test_v049_fee_truth_g19.py  NEW — 27 tests
ultrabot-web/backend/tests/conftest.py             pristine snapshot refresh
ultrabot-web/backend/evidence/probe_v049_fee_truth.py  NEW — fee-truth evidence probe
requirements.txt                                   fyers-apiv3 added
scripts/session_watchdog.py                        snapshot-prune fix
archive/evidence-p2-p5/*                           moved from evidence/
RELEASE_NOTES_v0.4.9.md                            NEW — this file
```

## Merge guidance

Merge `fri_2026-09-04_v0.4.9` → `main` when satisfied. Monday: run the merged build live (G19
shadow data starts accumulating automatically), review shadow verdicts at Monday EOD, then decide
on flipping `g19_mode: log_only → enforce` for Tuesday.
