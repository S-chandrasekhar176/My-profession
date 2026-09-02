#!/usr/bin/env python3
"""Opportunity auto-confirmer for the 2026-09-01 paper E2E session.

Polls GET /api/opportunities and immediately confirms each new pending
opportunity via POST /api/opportunities/{id}/confirm (segment=EQ) so the
full trade pipeline (sizing -> paper fill w/ slippage+fees -> SL/target
management -> 15:15 square-off) gets exercised today.

Guardrails:
- Only acts 09:20-15:10 IST (G8 time gate also blocks late entries upstream)
- User standing approval 10:05 IST: confirm ALL opportunities (cap effectively removed)
- Never re-confirms the same opportunity id
- Exits after 15:30 IST (market closed)
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "http://127.0.0.1:8000"
JWT_FILE = "/home/z/my-project/bot_analysis/jwt.txt"
IST = timezone(timedelta(hours=5, minutes=30))

POLL_SECONDS = 15
MAX_CONFIRMS = 999  # user granted standing approval: confirm ALL opportunities (engine risk limits remain authoritative)
CONFIRMED_IDS = set()
CONFIRM_COUNT = 0


def log(msg: str) -> None:
    ts = datetime.now(IST).strftime("%H:%M:%S")
    line = f"[{ts} IST] {msg}"
    print(line, flush=True)


def ist_now() -> datetime:
    return datetime.now(IST)


def http(method: str, path: str, token: str = None, body: dict = None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    # sandbox proxy: bypass for localhost
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def get_token() -> str:
    try:
        with open(JWT_FILE) as f:
            tok = f.read().strip()
        if tok:
            status, _ = http("GET", "/api/engine/status", token=tok)
            if status == 200:
                return tok
    except Exception:
        pass
    # re-login (form encoded)
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=b"username=admin&password=admin",
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        tok = json.loads(resp.read().decode())["access_token"]
    with open(JWT_FILE, "w") as f:
        f.write(tok)
    log("JWT refreshed via re-login")
    return tok


def main() -> None:
    global CONFIRM_COUNT
    log("Watcher started: poll=%ss max_confirms=%d window=09:20-15:10 IST" % (POLL_SECONDS, MAX_CONFIRMS))
    while True:
        now = ist_now()
        hhmm = now.hour * 100 + now.minute
        if hhmm >= 1530:
            log("Market closed (>=15:30 IST) — watcher exiting")
            return
        active_window = 920 <= hhmm <= 1510  # G8 time gate blocks late entries upstream; 15:10 hard stop before 15:15 square-off
        try:
            tok = get_token()
            status, opps = http("GET", "/api/opportunities", token=tok)
            if status == 401:
                log("401 — forcing JWT re-login next cycle")
                try:
                    with open(JWT_FILE, "w") as f:
                        f.write("")
                except Exception:
                    pass
            elif status == 200 and isinstance(opps, list):
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
                        log(f"SKIP {sym} {direction} ({strat}) — outside confirm window {hhmm}")
                        CONFIRMED_IDS.add(oid)
                        continue
                    if CONFIRM_COUNT >= MAX_CONFIRMS:
                        log(f"SKIP {sym} {direction} ({strat}) — daily confirm cap {MAX_CONFIRMS} reached")
                        CONFIRMED_IDS.add(oid)
                        continue
                    log(f"OPPORTUNITY DETECTED: {sym} {direction} @ {entry} ({strat}, conf={conf}) — confirming...")
                    try:
                        cstatus, cresp = http(
                            "POST", f"/api/opportunities/{oid}/confirm", token=tok, body={"segment": "EQ"}
                        )
                        CONFIRMED_IDS.add(oid)
                        if cstatus == 200:
                            CONFIRM_COUNT += 1
                            log(f"CONFIRMED #{CONFIRM_COUNT}: {sym} {direction} @ {entry} ({strat}) -> {json.dumps(cresp)[:400]}")
                        else:
                            log(f"CONFIRM REJECTED (http {cstatus}): {json.dumps(cresp)[:300]}")
                    except urllib.error.HTTPError as e:
                        body = e.read().decode()[:300]
                        CONFIRMED_IDS.add(oid)
                        log(f"CONFIRM ERROR http {e.code}: {body}")
        except Exception as exc:
            log(f"poll error: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
