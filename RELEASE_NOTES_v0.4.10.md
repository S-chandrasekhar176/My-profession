# Release Notes — v0.4.10

**Date:** Friday night, 2026-09-04 (built + live-tested same night)
**Base:** v0.4.9 final consolidation (wave-4 fee-truth + holiday-calendar hotfix + repo hygiene)
**Theme:** Reliability + human-in-the-loop upgrade — the bot can now be *driven* from Telegram.

---

## 1. Interactive Telegram (two-way) — headline feature

New module `ultrabot-web/backend/notifications/telegram_interactive.py`
(`InteractiveTelegramBot`), wired into the FastAPI lifespan in `app.py` (started
after engine creation, stopped gracefully on shutdown).

- **Opportunity cards with inline buttons**: every gate-passed pending
  opportunity is pushed to the owner's chat as a card (symbol, direction,
  strategy, entry/SL/target, R:R, qty, risk, capital, VIX/regime/confidence)
  with `✅ Approve / ❌ Reject / ⏭ Skip / ℹ️ Why` buttons.
- **Decisions use the SAME engine path as the web dashboard**
  (`engine.confirm_opportunity` / `engine.skip_opportunity`) — no new trading
  logic, no auth bypass. First tap wins; later taps answer "Already decided".
- **Card auto-edit on outcome**: FILLED (price/qty/SL/TGT), Rejected, Skipped,
  "Not available (expired)" — the card itself becomes the audit trail, plus an
  in-message stamp `[HH:MM:SS · approved via telegram]`.
- **ℹ️ Why** replies with the per-gate breakdown (pass/fail + reasons) and
  Kronos score / win-rate / trend context.
- **Commands**: `/status` (engine state, session, scan/signal/trade counters),
  `/positions`, `/pnl`, `/pause`, `/resume`, `/help`.
- **Push loop** polls `engine.pending_opportunities` every 5s with an
  optimistic-claim guard (no duplicate cards under concurrency).
- **Poll loop** uses `getUpdates` long-polling (25s) — no webhook/public URL
  needed; runs inside the app process; never raises into the app (bounded
  retry/backoff on network errors).

## 2. Canary alerting

`canary_loop` (2-min cadence, market hours only, rate-limited to once/45min):
fires a Telegram `🚨 CANARY` when the market is OPEN but the engine is not in
`running` state — the "bot must know when it's blind" layer born from the
2026-09-04 holiday-bug incident (30 silent minutes lost).

## 3. Security: private bot migration (important)

- Discovery: the previously configured bot token
  (`8864941949:AAE_…`) belongs to a **third-party tool** ("Ultrabot" /
  A_ToolsX channel) whose server was still consuming `getUpdates`. Live test
  evidence: user button taps and commands were answered by that server
  ("join our channel" gate), and 0 callbacks reached our code.
- Fix: migrated to a **private BotFather bot** owned by the user
  (`@chandu_ultrabot_bot`, chat `5284252833`). The third-party token is
  removed from `config/defaults.yaml` and the pristine snapshot fixture.
- Interactive layer enforces a strict **chat_id whitelist** — any other
  sender's taps/commands are ignored and logged.

## 4. Ops / sandbox resilience learnings (documented in worklog)

- Plain `nohup` background processes in this sandbox get reaped when the tool
  shell session cycles (both first-run harnesses died silently). All long-lived
  processes must launch via `scripts/daemonize.py` (double-fork + setsid) —
  this is now validated for backend and test harnesses alike.

## 5. Test evidence (2026-09-04 night, on the user's real phone)

Test harness `scripts/test_telegram_interactive.py` (FakeEngine + FakeRepo,
evidence JSON after every event):

| Test | Result |
|---|---|
| Card push (single, no dupe) | ✅ |
| ℹ️ Why → gate breakdown | ✅ |
| ✅ Approve → card flips FILLED, `engine.confirm_opportunity` called | ✅ |
| ❌ Reject → `engine.skip_opportunity("Rejected via Telegram")` | ✅ |
| First-tap-wins guard | ✅ (later taps answered "Already decided") |
| /status, /pnl, /positions, /help | ✅ |
| Production round-trip: real backend, real session `8496b58a…` answered /status | ✅ |

Test suite: **794 passed** (2 pre-existing failures in
`test_universe_hygiene.py`: TMCV/Tata-CV successor missing from universe data —
unrelated to this release, filed for v0.4.11).

## 6. Config additions (`notifications:`)

```yaml
telegram_interactive_enabled: true
telegram_canary_enabled: true
telegram_poll_timeout: 25
```

## Upgrade notes

- Restart backend via `scripts/daemonize.py` as usual; the interactive layer
  starts automatically when enabled.
- Only ONE `getUpdates` consumer may run per bot token — do not run the test
  harness while the backend is running (409 conflict).
- Known follow-ups (v0.4.11): universe data TMCV fix, sector-map completion
  (G2 "Unknown" bucket), shadow-outcome recorder (learning clock start).
