#!/usr/bin/env python3
"""Independent liveness canary for the UltraBot sandbox.

Writes a tiny timestamp file every CANARY_INTERVAL seconds. The watchdog
compares canary freshness against backend health to CLASSIFY deaths:

  * backend down + canaries fresh  -> backend-targeted process death (L1/L2)
  * backend down + canaries stale  -> workspace suspension / recycle (L3)

A canary must be as dumb as possible: no imports beyond stdlib, no network,
no logging — if IT freezes, the whole workspace froze.

Usage: python3 canary_heartbeat.py <name>
Run detached: python3 scripts/daemonize.py <log> python3 scripts/canary_heartbeat.py a
"""
import sys
import time
from datetime import datetime
from pathlib import Path

CANARY_INTERVAL = 15
HEARTBEAT_DIR = Path("/home/z/my-project/bot_analysis/persist")


def main() -> None:
    name = (sys.argv[1] if len(sys.argv) > 1 else "x").strip().lower()
    path = HEARTBEAT_DIR / f"canary_{name}.heartbeat"
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            path.write_text(datetime.now().isoformat(timespec="seconds"))
        except Exception:
            pass  # never die on a write error
        time.sleep(CANARY_INTERVAL)


if __name__ == "__main__":
    main()
