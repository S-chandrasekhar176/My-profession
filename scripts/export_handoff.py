#!/usr/bin/env python3
"""v0.4.13 handoff exporter CLI — sanitized DB copy for the midday handoff.

Usage:
    python scripts/export_handoff.py                     # auto path: download/ultrabot_handoff_YYYYMMDD-HHMM.db
    python scripts/export_handoff.py --out /path/to.db   # explicit output
    python scripts/export_handoff.py --verify            # only verify an existing handoff file

The copy carries the full day's data (trades, positions, signals,
shadow_outcomes, sessions) with broker_credentials DELETED — secrets
never travel (repo is public; git must never carry the DB).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from core.handoff import export_handoff, verify_handoff  # noqa: E402


def _resolve_db_path() -> str:
    import os

    env = os.getenv("DB_PATH")
    if env:
        return env
    from db.database import DB_PATH  # default resolution (DATA_DIR / ultrabot.db)

    return str(DB_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export sanitized handoff DB")
    ap.add_argument("--db", default=None, help="source DB (default: DB_PATH env or backend default)")
    ap.add_argument("--out", default=None, help="output path (default: download/ultrabot_handoff_<ts>.db)")
    ap.add_argument(
        "--include-credentials",
        action="store_true",
        help="DANGER: keep broker_credentials in the copy (same-owner only; never push to git)",
    )
    ap.add_argument("--verify", metavar="PATH", default=None, help="verify an existing handoff file only")
    args = ap.parse_args()

    if args.verify:
        report = verify_handoff(args.verify)
        print(json.dumps(report, indent=2))
        return 0 if report.get("integrity") == "ok" else 1

    db_path = args.db or _resolve_db_path()
    stats = export_handoff(
        db_path,
        out_path=args.out,
        include_credentials=args.include_credentials,
    )
    check = verify_handoff(str(stats["out_path"]))
    print(json.dumps({**stats, "verify": check}, indent=2, default=str))
    ok = check.get("integrity") == "ok" and all(
        v <= 0 for v in check.get("secret_rows_remaining", {}).values()
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
