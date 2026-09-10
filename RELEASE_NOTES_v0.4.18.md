# UltraBot v0.4.18 — wed_v0.4.18

**Branch:** `wed_v0.4.18` (base: `main` @ `aca19e0`)
**Date:** 2026-09-10
**Test status:** backend 974 passed / 0 failed (34.2s) · frontend `tsc --noEmit` clean

## What this wave fixes (11 issues)

### P0 — money-visible bugs

1. **Partial-booking P&L leak (DB write side)** — `engine._execute_partial_booking`
   read `position.extra` and only accumulated `partial_realized_pnl` /
   `partial_fees` when it was already a **dict**. ORM-backed positions store
   extra as a JSON **string**, so every partial leg's P&L was silently dropped
   from the DB and the close-time merge (v0.4.8 HF-9) merged zeros. Live
   Sep-9 impact: HDFCLIFE leaked +₹48–59 net, ADANIENT +₹41–74; recorded day
   net ₹322.03 vs true ~₹411–455. Now parsed with `_position_extra_dict()`
   (handles both shapes) and the shrunken `remaining_qty` is persisted too
   (it was left stale after stage fires — HDFCLIFE showed qty 57 vs
   remaining 76 in the ledger).

2. **Trades page fabricated `MANUAL` exit reason** — open rows (no
   `exit_reason`) rendered as `MANUAL`, reading as if a human closed them
   (the Sep-9 HDFCLIFE confusion — it was an engine TIME_EXIT at 10:18:34).
   Now: open rows show `OPEN`, closed-without-reason rows show `UNKNOWN`.

### Issues 10 + 11 — realtime broker data (this wave's new asks)

3. **Banner indices now tick from the broker's realtime quotes endpoint**
   (`GET /api/live-quotes`). Previously: NIFTY/BANKNIFTY/VIX came from the
   engine's scan-cadence attributes (60–180 s stale) and SENSEX /
   MIDCPNIFTY / FINNIFTY fell through to **Yahoo 15-minute bars** (up to
   15 min delayed) — that is why the banner felt simulated. Now a shared
   `FyersBroker.get_quotes()` bulk call (price + change + changePct +
   prev_close for all 5 indices + VIX in ONE request, 2.5 s micro-cache,
   rate-limiter protected) is the primary source whenever Fyers credentials
   are stored; engine attributes and Yahoo remain as labelled fallbacks.
   A fresh daily re-login in Settings is picked up without a backend restart.

4. **Chart candles follow the tape** — the chart modal's forming candle was
   extended from the same stale quote source and completed bars only
   appeared on manual refresh. With (3) the 3 s live tick is broker-realtime,
   and a 30 s full candle refetch rolls completed bars in automatically.

### User request

5. **Max concurrent positions 6 → 10** (`risk.max_open_positions`; G1 reads
   it live).

### P1 — observability

6. **Loop-liveness beat + stall alarm** — the engine now stamps
   `_loop_last_beat` every main-loop iteration and exposes
   `loop_stalled_seconds` via `/api/engine/status` AND (unauthenticated) via
   `/api/health`. The header engine dot turns **amber** with the stall age
   in the tooltip when a "running" engine stops iterating (>180 s) — the
   invisible Sep-9 13:35 failure mode (process alive, loop dead, UI said
   "scanning"). session_watchdog v2 alerts on Telegram (>300 s, edge-
   triggered, no restart — a stall is not a death).

7. **Win-rate scope label corrected** — the dashboard Win Rate card is fed
   by the engine's **daily** risk snapshot but was labelled "(All-Time)"
   (Sep-9: banner said 6/13 while the Trades tab showed 7/15). Now "(Today)".

### P2 — persistence & gate semantics

8. **G4 counts entries, not just closes** — the gate context now feeds
   `max(closed_trades, trades_executed_today)`; `_trades_executed` is
   restored from the ledger on same-day resume. Closes the Sep-9 overshoot
   (gate saw 8 closed at 11:41 while 13 entries were allowed vs cap 10).

9. **risk_events finally has a writer** — the table existed since v0.3 with
   zero rows ever written. Gate blocks and daily-risk halts are now
   persisted (halt events deduped per distinct block_reason).

10. **EOD daily_summary catch-up** — the 15:30 cron never backfills, so the
    Sep-8/9 pre-close backend deaths left `daily_summary` empty despite a
    complete ledger. On boot after 15:30 IST with no row for today, the
    summary is backfilled from the ledger (`run_eod_summary_catchup`).

11. **`sessions.end_time` is stamped** by `close_session` (was always NULL).

### Security (found during this wave — needs YOUR action)

- **Your Telegram bot token + chat id were committed to this PUBLIC repo**
  in `aca19e0` (`defaults.yaml`). They are scrubbed back to empty here
  (secrets belong in the gitignored `defaults.local.yaml`), **but the token
  remains in git history — please rotate it via @BotFather**.

### Test-infra note

- `tests/test_requirements_consistency.py` re-scoped: `aca19e0` deliberately
  raised the aiohttp pin to 3.10.11 (diverging from the fyers SDK's own
  3.9.3 pin — safe because the SDK is installed `--no-deps` and never
  co-resolved). The test now enforces an exact, conscious pin
  (`VERIFIED_AIOHTTP = "3.10.11"`) instead of equality with the SDK pin.
- Config drift-guard: `tests/pristine_config_snapshot.yaml` refreshed to
  match the intentional config changes (max positions 10, credential scrub).

## New tests

`tests/test_v0418_fixes.py` — 12 tests: partial-extra accumulation from a
JSON-string extra (the leak), `_position_extra_dict` shapes, old-bug
documentation, G4 entry-count blocking, `FyersBroker.get_quotes` parsing /
derived change fields / bad payload, live-quotes realtime-path priority +
fallback, `close_session` end_time, config default 10.

## Files touched

Backend: `core/engine.py`, `core/scheduler.py`, `core/session_manager.py`,
`app.py`, `api/routes/candles.py`, `brokers/fyers.py`,
`feeds/fyers_candles.py`, `config/defaults.yaml`, `scripts/session_watchdog.py`
Frontend: `src/app/trades/page.tsx`, `src/app/page.tsx`,
`src/components/layout/Header.tsx`, `src/components/chart/TradingViewChartModal.tsx`
Tests: `tests/test_v0418_fixes.py` (new), `tests/test_requirements_consistency.py`,
`tests/pristine_config_snapshot.yaml`
