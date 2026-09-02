# Awesome_DE / GLM UltraBot — Release v0.4.8

**Release date:** 2026-09-01 · **Base:** v0.4.7 · **Proven on:** full live paper-trading session (09:15–15:30 IST, broker=Fyers, 1m realtime feed)

v0.4.8 contains 7 product hotfixes and 1 session-tool fix, every one discovered,
fixed and regression-tested during a complete live trading day (8 executed
paper trades, 410+ signals, 0 engine errors, feed 100% healthy).

---

## Hotfix ledger

| # | Severity | File(s) | What broke | Fix |
|---|----------|---------|-----------|-----|
| #2 | CRITICAL | `brokers/fyers.py` | `get_candles("NSE:SBIN-EQ")` double-prefixed to `NSE:NSE:SBIN-EQ-EQ` → Fyers -300 → realtime feed AND backtests silently degraded to Yahoo | Idempotent symbol formatting (already-formatted symbols used as-is); 6 contract tests in `tests/test_fyers_symbol_roundtrip.py` |
| #3 | CRITICAL | `api/routes/candles.py` | `/api/candles?broker=fyers` ignored the parameter, always served Yahoo but responded `"broker": "fyers"` — data-provenance lie | Broker-aware feed with honest `actual_broker` + `requested_broker` in every response |
| #4 | HIGH | `brokers/fyers.py` | Missing SDK crashed with cryptic `'NoneType' object has no attribute 'SessionModel'` | `_require_fyers_sdk()` guard raising an actionable error with install instructions |
| #5a | HIGH | `brokers/fyers.py:61-71` | Live 429s during 08:45 watchlist build (2/51 symbols) despite respecting the documented 10 req/s | Per-second caps 10 → 8 (20% headroom), per-minute/day unchanged |
| #5b | HIGH | `core/rate_limiter.py` | TRUE ROOT CAUSE of the 429s: the "wait for oldest window event" rule admitted requests in millisecond bursts (8-10 at once, then ~1s silence) — burst signature tripped Fyers' server-side burst tolerance | **Pace-car spacing**: every admission ≥ `1/per_second + 5ms` after the previous one (~130ms at 8/s). Verified: 51 concurrent requests spread perfectly over 6.5s; worst sliding 1s window = 8; worst-case wait drops 1s → 130ms |
| #7 | MEDIUM | `risk/daily_risk_manager.py`, `core/engine.py` | `DailyRiskStatus` hardcoded `open_positions=0`, `capital_in_use=0.0`, `capital_usage_pct=0.0` — status banner blind to live positions (risk GATES were never affected; engine passes real count to G1) | Pass-through params with live wiring in engine main loop + `_build_risk_context`; backwards-compatible defaults |
| #8 | HIGH | `core/auto_resume.py`, `app.py` | SIGKILL crash left same-day session `status="running"` and the engine DOWN (SLs unenforced) until a human noticed — drill-proven 25s+ exposure | Crash-aware auto-resume: on boot, a same-day session still `running` restarts the engine with its recorded mode/broker. Graceful stops (`stopped`) and completed days are never auto-resumed (user intent preserved). 6 unit tests |
| W1 | (tooling) | `scripts/confirm_opportunity_watcher.py` | Session confirmation daemon crashed on first confirmation (`CONFIRM_COUNT` mutated without `global`) — one EICHERMOT opportunity expired unconfirmed | `global` statement; watcher ran clean for the rest of the day (5/6 subsequent confirmations filled) |

**New tests:** `test_fyers_symbol_roundtrip.py` (6), `test_rate_limiter_headroom.py` (3), `test_daily_risk_status_live_positions.py` (4), `test_auto_resume.py` (6) — **19 new regression tests. Suite: 733 passed / 2 pre-existing failures** (see Known issues).

## Live-session validation (2026-09-01, paper, ₹5,00,000)

**Result:** 8 closed trades, 2W/6L, win_rate 25%, **gross +₹52.26, fees −₹649.02, net −₹436.76 (−0.087%)** · 0 engine errors · Fyers 1m feed 100% healthy · 3/3 positions recovered across 3 restarts.

Exit paths proven live: `time_stop` (×4, per-strategy budgets), `stop_loss` (×2, with volatility-scaled adverse slippage 0.20/1.96 pts), partial booking L2 (+7 shares @ 1362.22), trailing SL with breakeven lock (3 positions), plus 15:15/15:20 square-off scheduler fired on schedule.

Signal/gate telemetry: 410+ signals post-restart alone; live gate stack observed blocking: G15 volume ×43+, G17 cost-pre-check ×72+, G2 sector concentration ×161+, G18 per-strategy guard ×87+, G1 max positions ×47, G8 time-of-day ×8 (pre-restart), G16 multi-timeframe ×2 — hundreds of low-quality setups rejected in a choppy Sideways regime (VIX 10.9–11.4).

Crash resilience drill: SIGTERM → graceful stop (no resume — correct); **SIGKILL → auto-resume in ~25s with same session_id, 3/3 positions, P&L history intact** (hotfix #8).

## Known issues (pre-existing, documented — not fixed in 0.4.8)

1. **F&O universe stale (51 symbols vs ~200 real)** — 2 failing tests (`test_universe_hygiene.py`: TMCV Tata Motors CV successor missing, extra-picks overlap). Refresh from NSE monthly F&O file.
2. **Fee-drag on small time-stop exits** — 2 of 8 trades were gross-positive but net-negative (EICHERMOT +35.05→−26.66; BPCL +3.75→−57.94). Recommendation: fee-aware minimum-move filter per instrument (round-trip fee % + buffer) and/or extend time-stop while a position is gross-positive and trending.
3. **G15 morning bias** — 20-period volume baseline includes the opening-auction spike; rel-volume reads systematically low before ~09:45. Recommendation: time-of-day-adjusted RVOL.
4. **360s opportunity TTL is tight for human-in-the-loop confirmation** (`strategy_ttl_seconds` configurable) — recommend 600–900s default for EQ intraday or a re-arm mechanism.
5. Deferred (from v0.4.7 audit): Fyers history pagination (~1500 bar/request cap); broker-driven feed factory (Angel One/Shoonya feeds unwired — Yahoo fallback applies); 15m/1h/1d chart resolutions served as 5m aggregation; backtest fidelity defect group (hardcoded regime/VIX, MA20 fallback, qty floor, iid Monte Carlo); `engine.py` decomposition.

## Upgrading from v0.4.7

No schema or config changes. Copy over your existing install (or fresh clone) and run `start.sh`. The Fyers access token flow is unchanged (daily 2FA login per SEBI; refresh-token flow remains discontinued).

---

## Addendum — audit wave 2 (HF-6…HF-10 + Telegram overhaul + EOD PDF)

Shipped after the 2026-09-01 Telegram-log audit, same release (v0.4.8 final).
All items regression-tested; **suite now 794 passed / 2 pre-existing failures**
(733 baseline + 61 new tests).

### P0 — money truthfulness

| ID | Severity | File(s) | What broke | Fix |
|----|----------|---------|-----------|-----|
| HF-9 | CRITICAL | `core/engine.py`, `core/scheduler.py` | EOD "Total Fees: ₹0" (scheduler's `daily_summary` carried only net_pnl); partial-booking leg (+₹90.58 gross HCLTECH) leaked out of every trade row and EOD aggregation (true day P&L ≈ −₹346, not −₹437); Telegram reported the REQUESTED exit price while the DB stored the FILL price | Close path merges `position.extra.partial_realized_pnl` / `partial_fees` into the trade row (full round trip); daily-risk recorder receives only the final leg (partials were recorded when they happened — no double count); scheduler EOD alert carries gross/fees/best/worst; close payload reports the effective (fill) price + keeps requested price for audit |
| HF-8 | HIGH | `utils/formatters.py` | `₹39,900.0.70` — the decimal FRACTION was formatted then spliced back with another "."; corrupted every amount carrying paise | Format the whole value once and split on "."; `format_currency(39900.70) == "₹39,900.70"` pinned by tests |
| HF-7 | HIGH | `utils/exit_taxonomy.py` (new), `core/engine.py`, `notifications/alert_manager.py` | `"stop" in reason` dispatch labeled every time_stop / fail_fast / profit-locking trailing exit as "STOP LOSS HIT" (5 of 7 live exits); `trades.exit_reason` column existed but was never written | Shared taxonomy classifies exits (TARGET / SL / TRAILING_SL / TIME_EXIT / FAIL_FAST / SQUARE_OFF / PARTIAL_BOOK / MANUAL); engine writes `exit_reason` at close; both engine and alert-manager route templates through one classifier |

### P1 — Telegram overhaul (HF-10 + session-log findings)

- **HF-10 routing**: `feed_alert` (feed_frozen / feed_unresponsive / feed_recovered) had NO dispatch branch → operators received raw `str(dict)` dumps. New `send_feed_alert` template + dispatch.
- **VIX payloads rendered as raw dicts**: `vix_recovered` / `vix_stale_warning` / `vix_critically_stale` carry type/severity/action but no message → `send_risk_alert` now renders structured, human-readable alerts.
- **Hardcoded 🔴 in SL template**: P&L emoji now follows the sign; trailing-stop exits render "🔒 TRAILING STOP EXIT — PROFIT LOCKED".
- **Symbol duplication**: "Symbol: HCLTECH (HCLTECH)" fixed — label only shown when an option identity exists and differs.
- **Noise control**: repeating failure subtypes (feed_unresponsive/frozen, vix_stale_*) cool down (5/15 min); recovery subtypes NEVER cooled; non-critical INFO chatter suppressed outside Mon–Fri 09:00–15:35 IST (money events, errors, engine status, CRITICAL severity always delivered); "15:20 PM IST" → "15:20 IST".
- **Opportunity lifecycle pings**: `opportunity_created` / `opportunity_expired` now reach Telegram — execution is confirm-only, and a pending opportunity used to be invisible outside the dashboard (live session lost EICHERMOT to TTL expiry unobserved).

### P2 — G15 morning baseline + EOD PDF (Option A)

- **HF-6**: the 20-bar volume baseline swallowed the opening-auction spike, so rel-volume in 09:15–09:50 read structurally low (live: 40 G15 rejections at 0.21x–0.69x vs 1.00x). Baseline is now spike-TRIMMED (top 20% of window bars dropped); genuine morning spikes surface, afternoon behaviour unchanged, low volume still blocked.
- **EOD PDF**: new `notifications/eod_pdf.py` (reportlab). Scheduler job at **15:35 IST** renders the EOD aggregation to a one-page PDF, archives it under `backend/reports/EOD_<date>.pdf`, and pushes it to Telegram via the new `TelegramBot.send_document`. `reportlab` added to `requirements.txt`.
- Config additions (`config/defaults.yaml` → notifications): `alert_feed_health`, `alert_opportunities`, `eod_pdf_enabled`, `eod_pdf_time` (defaults true / '15:35').

### New tests (wave 2, 61)

`tests/test_v048_p0_fixes.py` (HF-8 formatter, taxonomy, close-path accounting incl. the synthetic partial-leg round trip), `tests/test_v048_notifications.py` (routing, templates, noise control, quiet hours, documents), `tests/test_v048_g15_eodpdf.py` (morning baseline, PDF validity, EOD reconciliation).

### Packaging note (wave 2)

The zip no longer ships `backend/data/` — the database schema is created on
first boot (`init_db()` create-all) and the Fernet key auto-generates, so a
fresh extract boots clean. If you are upgrading over an existing install,
keep your existing `backend/data/` folder (it holds broker credentials,
watchlists and paper trades).
