#!/usr/bin/env python3
"""Opportunity auto-confirmer for the 2026-09-01 paper E2E session.

Polls GET /api/opportunities and immediately confirms each new pending
opportunity via POST /api/opportunities/{id}/confirm (segment=EQ) so the
full trade pipeline (sizing -> paper fill w/ slippage+fees -> SL/target
management -> 15:15 square-off) gets exercised today.

Auth (401 fix, persist/backlog_2026-09-22.md — client-side only, backend
auth untouched): all token/credential handling lives in ub_auth_client.
  - Credentials: env UB_USER/UB_PASS or ~/.ub_monitor/credentials.json
    (chmod 600). NEVER hardcoded, NEVER logged.
  - One poll = at most ONE protected call; the cached TTL decides — no
    protected call is ever spent just to validate the token.
  - Liveness uses unauthenticated GET /api/health (no token).
  - 401 -> re-login once, retry once, then fail loudly (no unbounded loop).

Guardrails:
- Only acts 09:20-15:10 IST (G8 time gate also blocks late entries upstream)
- User standing approval 10:05 IST: confirm ALL opportunities (cap effectively removed)
- Never re-confirms the same opportunity id
- Exits after 15:30 IST (market closed)
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta

from ub_auth_client import authed_json, health_json, _log

IST = timezone(timedelta(hours=5, minutes=30))

POLL_SECONDS = 15
MAX_CONFIRMS = 999  # user granted standing approval: confirm ALL opportunities (engine risk limits remain authoritative)
CONFIRMED_IDS = set()
CONFIRM_COUNT = 0


def ist_now() -> datetime:
    return datetime.now(IST)


def confirm_path(oid: str) -> str:
    return f"/api/opportunities/{oid}/confirm"


def main() -> int:
    global CONFIRM_COUNT
    _log("Watcher started: poll=%ss max_confirms=%d window=09:20-15:10 IST" % (POLL_SECONDS, MAX_CONFIRMS))
    while True:
        now = ist_now()
        hhmm = now.hour * 100 + now.minute
        if hhmm >= 1530:
            _log("Market closed (>=15:30 IST) — watcher exiting")
            return 0
        active_window = 920 <= hhmm <= 1510  # G8 time gate blocks late entries upstream; 15:10 hard stop before 15:15 square-off
        try:
            # Liveness check (unauthenticated): engine loop + broker name.
            # Costs NO token and NO protected call (requirement #6).
            hstatus, health = health_json()
            if hstatus != 200:
                _log(f"health check returned http {hstatus} — skipping cycle")
                time.sleep(POLL_SECONDS)
                continue
            loop_alive = not health.get("loop_never_beat", False)
            broker = health.get("broker", "?")
            if not loop_alive:
                _log(f"Liveness: loop NEVER BEAT (broker={broker}) — alert; skipping protected call this cycle")
                time.sleep(POLL_SECONDS)
                continue

            # Deep data (pending opportunities) — the ONE protected call of
            # this cycle, TTL-gated by ub_auth_client (requirement #7).
            status, opps = authed_json("GET", "/api/opportunities")
            if status != 200 or not isinstance(opps, list):
                _log(f"opportunities fetch returned http {status} — skipping cycle")
                time.sleep(POLL_SECONDS)
                continue
            for opp in opps:
                oid = opp.get("id")
                if not oid or oid in CONFIRMED_IDS:
                    continue
                sym = opp.get("symbol", "?")
                strat = opp.get("strategy", "?")
                direction = opp.get("direction", "?")
                entry = opp.get("entry_price", 0)
                conf = opp.get("confidence", 0)
                if not active_window:
                    _log(f"SKIP {sym} {direction} ({strat}) — outside confirm window {hhmm}")
                    CONFIRMED_IDS.add(oid)
                    continue
                if CONFIRM_COUNT >= MAX_CONFIRMS:
                    _log(f"SKIP {sym} {direction} ({strat}) — daily confirm cap {MAX_CONFIRMS} reached")
                    CONFIRMED_IDS.add(oid)
                    continue
                _log(f"OPPORTUNITY DETECTED: {sym} {direction} @ {entry} ({strat}, conf={conf}) — confirming...")
                try:
                    cstatus, cresp = authed_json(
                        "POST", confirm_path(oid), body={"segment": "EQ"}
                    )
                    CONFIRMED_IDS.add(oid)
                    if cstatus == 200:
                        CONFIRM_COUNT += 1
                        _log(f"CONFIRMED #{CONFIRM_COUNT}: {sym} {direction} @ {entry} ({strat}) -> {json.dumps(cresp)[:400]}")
                    else:
                        _log(f"CONFIRM REJECTED (http {cstatus}): {json.dumps(cresp)[:300]}")
                except Exception as confirm_exc:
                    # Bounded auth path already re-logged-in once; anything
                    # left is a real failure — log loudly, do NOT retry-loop.
                    CONFIRMED_IDS.add(oid)
                    _log(f"CONFIRM ERROR: {type(confirm_exc).__name__}: {confirm_exc}")
        except Exception as exc:
            # Fail loudly (visible log line); the 401 path inside
            # ub_auth_client already did its single re-login + retry.
            _log(f"poll error: {type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())

