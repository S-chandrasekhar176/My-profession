# RELEASE NOTES — v0.4.17 (Restart Resilience + Telegram Flood Control)

**Branch:** `tue_night_v0.4.17` · **Date:** 2026-09-08 (after market) · **Base:** main @ 93efcef (PR #12 merged)

Source: the post-market wave. Motivated by (a) the silent-death incident cluster
(~10 backend deaths on Sep 8 + 4th sandbox wipe that destroyed the day's EOD DB),
(b) the Telegram 429 boot-queue flood (45 alerts), and (c) pending opportunity
cards dying silently on every engine restart.

---

## 1. Pending opportunities now survive engine restarts (fixed)

**Symptom:** every restart mid-day silently killed all pending opportunity
cards; the boot sweep then branded their DB signals
"Pending opportunity lost on engine restart" (3 such rows on 2026-09-08).
Human-in-the-loop approvals pending at restart time were unrecoverable.

**Fix (`core/engine.py`):**
- **Creation path** now snapshots the FULL opportunity card into the signal
  row: `signal_data["opportunity"] = opportunity` (JSON column — no schema
  change). The card is durable from the moment it exists.
- **New `_restore_pending_opportunities()`** runs at engine start BEFORE the
  orphan sweep: re-arms every still-valid pending card (created today, TTL
  not elapsed, market open) back into `pending_opportunities` and
  **re-broadcasts** it (`restored: true`) so the dashboard re-renders the
  card. Telegram inline buttons stay functional — the `opportunity_id` is
  unchanged.
- **Orphan sweep gains `skip_ids`**: rows re-armed by the restore are live
  cards again and are NOT branded expired; only genuine orphans
  (legacy rows without a snapshot, cross-day rows, TTL-elapsed rows) are
  expired with the honest reason as before.

**Tests (5):** restore re-arms + re-broadcasts · TTL-elapsed skipped ·
market-closed no-op · legacy row ignored · sweep skips restored ids and still
expires genuine orphans.

## 2. Telegram 429 flood — pacing + Retry-After (fixed)

**Symptom:** boot sequence fired ~45 alerts in one burst (orphan sweep, feed
recovery, risk rehydration, engine status…); Telegram's ~1 msg/sec per chat
limit responded with a 429 flood.

**Fix (`notifications/telegram_bot.py`, single choke point):**
- All sends serialized through an instance lock and **paced at
  `_MIN_SEND_INTERVAL = 1.2 s`** — a 45-alert boot burst now trickles over
  ~1 minute instead of slamming the API.
- **HTTP 429 handling**: parses `parameters.retry_after` from the error body
  (bounded by `_MAX_RETRY_WAIT = 30 s`), sleeps, retries exactly once; a
  persistent 429 drops the message with an error log — no infinite loops, no
  event-loop stalls (worst case bounded ≈ 2 × 30 s).

**Tests (3):** burst pacing (elapsed ≥ 2 intervals, all succeed) ·
429 → retry_after → recovery with exactly one retry · persistent 429 → drop.

## 3. Session watchdog v2 + independent canaries (ops tooling)

**Why:** silent deaths need evidence, not just restarts. The old watchdog was
a 60 s restart loop with no memory; it would also have fought the graceful
12:36 stop.

**`scripts/session_watchdog.py` (rewritten):**
- Poll 60 s → **15 s**; appends `persist/heartbeat.log` lines
  (`ts,UP|DOWN,latency_ms`) — death time now known within 15 s.
- On alive→down: writes a **forensic bundle** to
  `persist/death_reports/death-<ts>.json` — process census (/proc scan),
  canary freshness, backend-log tail (150 lines), meminfo/loadavg,
  best-effort `dmesg` — and **classifies** the death:
  `backend_process_death` (canaries alive) vs `workspace_suspension_or_recycle`
  (everything froze) vs `backend_hung` (process alive, health failing).
- **Independent canaries** (`scripts/canary_heartbeat.py`, two instances):
  dumb timestamp writers that make the classification possible; auto-respawned
  by the watchdog if missing.
- **Telegram alerts** (standalone sender reading config yaml) on unexpected
  death / restart-cap exhaustion / workspace freeze.
- **Restart policy**: per-day cap (5) + exponential backoff (15 s → 300 s);
  counter file `persist/watchdog_restarts.json`.
- **Intentional-stop marker**: `touch persist/BACKEND_STOPPED_INTENTIONALLY`
  before a graceful stop suppresses restart + alert for 2 h — the watchdog no
  longer fights planned shutdowns (marker auto-expires; next morning just
  works).
- 15-minute DB snapshot job unchanged.

## Sandbox-recovery note (4th wipe, Sep 8 evening)

The working tree lost `.git`, local config, and the day's EOD DB
(3 paper positions). Rebuilt from GitHub main; v0.4.16 verified (954/0/0)
before this wave. Backlog impact: P1 realtime-sample counter reset again —
the platform ticket (`download/platform_ticket_sandbox_silent_deaths.md`)
requests an idle-suspension exemption.

---

**Verification:** backend suite **962 passed / 0 failed / 0 skipped**
(954 + 8 new) in 34.8 s. No frontend changes.
