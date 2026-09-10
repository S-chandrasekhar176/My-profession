# v0.4.20 — Expired-opportunities list + external-review Phase-1 execution fixes

Two streams: (A) the user-reported Expired-tab bug, (B) verification of the
Sep-10 external AI code review (CP-01…CP-09) against the REAL code base, with
the confirmed CRITICAL items fixed in this release.

## A. Expired opportunities not showing in the Expired list (user report)

**Root cause chain (3 layers, all verified):**
1. `useWebSocket.ts`: on the engine's `opportunity_invalidated` broadcast the
   handler ONLY removed the card from the pending store — the card vanished
   instantly and was never marked expired client-side.
2. `/api/opportunities/invalidated` served ONLY
   `engine.invalidated_opportunities` — an **in-memory dict wiped on every
   engine `start()`** and capped at 50. Any restart (the engine restarts
   often) made the entire expiry history unrecoverable → empty Expired tab
   after reload.
3. Yet the data WAS durable all along: the TTL sweep writes
   `signals.status='EXPIRED'` + `rejection_reason` per expiry (v0.4.11+).

**Fix:**
- Backend `GET /api/opportunities/invalidated` now MERGES engine memory ∪
  today's DB signals (`status='EXPIRED'`), maps DB rows to the UI opportunity
  shape (LONG→BUY, reason, invalidated_at, signal_id), dedupes, newest-first,
  cap 100. **Restart-durable.**
- Frontend WS handler now ALSO `saveStoredExpiredOppId(id, reason)` — instant
  Expired-tab marking + reload survival.

## B. External review verified against the real code base

| ID | Claim | Verdict on current main (post-v0.4.19) | Action |
|---|---|---|---|
| CP-02 | Exit order passes ORIGINAL quantity → reverse position | **CONFIRMED — but subtler**: DB was shrunk (v0.4.18) while the in-memory object kept the original qty; `partial_complete` and same-cycle closes exited the full original qty | **FIXED**: in-memory `quantity`/`remaining_qty` synced at booking; `_close_position(close_qty=…)`; qty-0 closes place NO order |
| CP-04 | Double-counted partial P&L (`quantity × move` + partial gross) | **CONFIRMED** (same root as CP-02 — stale qty + `_partial_gross` on top) | **FIXED**: final leg computed on effective close qty; qty-0 close → round trip = partial legs only, no phantom ₹40 brokerage |
| CP-05 | Backtester fabricates 20-MA fallback trades; partial booking never shrinks qty | **CONFIRMED** (both sub-claims) | **FIXED**: MA fallback removed (honest NO_TRADE); partial legs now banked (`PARTIAL_L*` rows, `remaining_qty` shrinks, per-leg fees) |
| CP-01 | Software-only stop-loss (no broker-side SL) | **CONFIRMED** — real live-money gap; moot in paper mode today | DEFERRED (design-heavy: bracket/OCO per broker; P6/P7 gate) |
| CP-03 | 60–180s polling loop too slow for scalping | **CONFIRMED as designed** — 5m-bar strategies; manage cadence tied to scan loop | DEFERRED (event-driven core = Phase-2 roadmap; not a patch) |
| CP-06 | Angel One static ~50-token map | **CONFIRMED** (brokers/angel_one.py) — but Angel One is NOT the active broker | DEFERRED (scrip-master resolver before any Angel use) |
| CP-07 | PaperBroker SELL credits full notional (no margin lock) | **CONFIRMED** — cash-flow model, self-consistent at close but inflates interim availability | DEFERRED (needs margin-lock redesign + rehydration mirror; do NOT touch accounting mid-session) |
| CP-08 | Yahoo 15-min delayed fallback data | **PARTIALLY ADDRESSED** by v0.4.18 (Fyers-first realtime quotes + source labels) for banners/charts; scan-feed fallback risk remains labelled | MONITORED |
| CP-09 | ML engine is a stub (no trained models) | **CONFIRMED** — known; Kronos heuristics are labelled, shadow corpus is being collected for Phase-4 training | ROADMAP |

The review's Phase-1 recommendation ("fix over-exit + double P&L first") matched
exactly what this release does.

## Verification

- Backend: **984 passed / 0 failed / 0 skipped** (974 baseline + 10 new in
  `tests/test_v0420_fixes.py` — close-qty semantics, invalidated-API merge,
  backtest honesty, WS contract)
- Frontend: `bunx tsc --noEmit` → exit 0
- No DB schema changes; no config changes

## Notes

- CP-01/CP-03/CP-06/CP-07/CP-09 are documented, not hidden — they need
  design-level work (bracket orders, event-driven core, scrip master,
  margin-lock accounting, ML training) and will be sequenced after user
  sign-off. CP-07 especially should NOT be patched ad hoc: PaperBroker
  capital movement is mirrored by v0.4.13 rehydration; both sides must move
  together.
- Deploy at a flat window per the standing rule.
