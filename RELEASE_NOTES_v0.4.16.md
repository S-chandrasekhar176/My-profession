# RELEASE NOTES — v0.4.16 (User-Testing Feedback Fixes)

**Branch:** `fix/ui-pnl-feedback-2026-09-08` · **Date:** 2026-09-08 · **Base:** main @ 25e3042 (PR #11 merged)

Source: the user's 2026-09-08 local Part-2 test session (Fyers data, `{mode: paper, broker: fyers}`, session `d5738c6f`). Seven feedback items; six fixed here, one (Telegram 429 flood) already resolved and confirmed non-reproducible locally.

---

## 1. Opportunity chart: live price movement (fixed)

**Symptom:** the modal showed ENTRY / SL / TP1 lines but no visible price movement — the candle fetch ran once at modal open and the LTP display was a static snapshot.

**Fix (`src/components/chart/TradingViewChartModal.tsx`):**
- NEW live-tick loop: while the modal is open, `/api/live-quotes` is polled every 3 s; the FORMING candle is extended (high/low/close) and a dotted **LTP price line** moves with it — price walking toward SL/target is now visible. No chart rebuild per tick.
- `fyers` added to the data-source dropdown (backend `broker=fyers` for 1m/5m existed since v0.4.8-HF#3 but the UI never offered it).
- Explicit **error state with retry** when the candle feed returns nothing (previously the modal silently showed only level lines).
- **EXIT price line** (violet) for closed trades; the trades-history button now passes the real persisted `stop_loss` instead of a fabricated ±1.5 % guess, plus `exitPrice`.

## 2. Accepted opportunity also appearing in invalidated/expired list (fixed)

**Symptom:** RADICO SELL MRF appeared in BOTH the confirmed list and the invalidated/expired list ("Risk-Reward Invalidate").

**Root causes (two independent):**
- **Backend:** `confirm_opportunity` never resolved the DB signal row — it stayed `pending` after a successful fill, so on the next engine restart the orphan sweep branded EXECUTED trades `"Pending opportunity lost on engine restart"` (3 such rows in the sandbox on 2026-09-08). The same rot hit every confirm-rejection path (TTL / target-hit / SL-breach / price-mismatch / gates re-check), each of which pops the opportunity and returned early.
- **Frontend:** the expired-tab filter (`isOppExpired`) ignored card status — a confirmed card whose TTL elapsed (e.g. approved from Telegram, which never reached that browser's localStorage) got re-branded `expired` and listed in both tabs.

**Fix:** `confirm_opportunity` now marks the signal **`filled`** after the trade row exists and **`expired` (with reason)** at every rejection exit; the `trade_fill` broadcast carries **`opportunity_id`** so a Telegram-approved fill marks the card confirmed in every open browser. Frontend: terminal statuses (confirmed/skipped) win everywhere — tab filter, expired-tab composition, localStorage expired-store (`saveStoredExpiredOppId` refuses to brand a confirmed/skipped id; `removeStoredExpiredOppId` purges on confirm/skip). Tests: `tests/test_v0416_signal_lifecycle.py` (3 lifecycle cases).

## 3. ALL_GATES_PASSED but no opportunity card (made deterministic)

**Symptom:** SHREECEM MRF BUY logged ALL_GATES_PASSED at 14:04:06 but no card appeared.

**Explanation:** after the ALL_GATES_PASSED telemetry, a signal can still be diverted (shadow mode), rejected (G20 sizing / G17 actual-size cost / G19 enforce), or die in a swallowed exception — and the old event text promised "opportunity created", which made the timeline misleading.

**Fix:** the ALL_GATES_PASSED reason is now the honest "Passed all risk gates", and a terminal **`OPPORTUNITY_CREATED`** telemetry event (gate `OPPORTUNITY`) fires the moment the card actually exists and is broadcast. Every "gates passed but no card" case now has a deterministic answer in the scan timeline: SHADOW_PASSED / REJECTED / ERROR / OPPORTUNITY_CREATED. The telemetry card renders the new status in accent green.

## 4. Approval channels are combinable now (fixed)

**Symptom:** felt like desktop OR Telegram, only one working.

**Reality:** the backend always fanned out to both the dashboard and Telegram; what was missing was a true desktop popup (the `desktop_enabled` settings field was dead config — nothing consumed it).

**Fix:** real browser desktop notifications (Web Notification API) via `src/lib/desktopNotifications.ts` — popups for new opportunities and fills, clickable through to the right page, OS-level tag dedupe, fired from the existing WS events so they run **alongside** Telegram (independent channels). Settings → Notifications gained a **Desktop Notifications** card: enabling requests browser permission (user-gesture compliant), the preference persists locally and is mirrored to the backend `desktop_enabled` field. Permission denied → honest toast; Telegram and in-dashboard cards unaffected.

## 5. PnL numbers inconsistent between surfaces (fixed)

**Symptom:** BPCL −₹61.65 vs dashboard/history/aggregates disagreeing by ₹20–40.

**Root causes found & fixed:**
- `get_todays_pnl` summed `fees + brokerage` — since v0.4.8-HF the close path writes the FULL round-trip fee (both brokerage legs) into `fees`, so `brokerage` double-counted and broke `gross − fees = net` on the dashboard.
- The trades-history table did the same double-count client-side.
- The manual-close fallback (engine down) wrote `net = pnl − entry-estimate` while leaving the `fees` column at a different figure — the exact mixed-write that produced the BPCL row. It now computes the canonical NSE round-trip fee and persists `pnl`, `fees` AND `net` from one source.
- The read-path self-heal (`_reconcile_closed_trade_pnl` in trades.py, from the earlier session) stays as defense-in-depth: any CLOSED row violating `net = gross − fees` is corrected and warning-logged.
- Telegram TRAILING_SL template no longer claims "PROFIT LOCKED" when net ≤ 0 (the BPCL "PROFIT LOCKED −₹61.65" message).

## 6. Top banner index value/direction mismatch (fixed)

**Root causes found & fixed:**
- The cold-start indices were **hardcoded** from an old session (NIFTY 24361.90 …) — replaced with honest "—" placeholders.
- The Yahoo fallback pulled **daily bars** and reported yesterday's close as "realtime" — now 15-minute intraday bars (price = latest intraday close, change vs previous session close, source labelled "Yahoo (15m delayed)").
- NIFTY `change` carried the PERCENT value (both fields got −0.14 while the real move was ~−33 pts) — points are now derived from the engine percent. Tested.
- BANKNIFTY/VIX have no change source; 0 now renders as a neutral gray "—" instead of implying direction. VIX hard-fallback 11.36 removed.

## 7. Telegram notifications (no change needed)

Confirmed healthy in the user's local session; the sandbox-side 429 flood is a boot-queue artifact already tracked for the post-market backoff fix.

---

**Verification:** backend suite **954 passed / 0 failed** (949 + 5 new), `tsc --noEmit` clean. No engine restart needed to adopt (merged code takes effect on next boot; signal-lifecycle fixes apply to new fills).
