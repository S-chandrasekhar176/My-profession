"""v0.4.13 M2 — handoff DB exporter (sanitized).

The midday handoff protocol (sandbox → user's local machine) needs the
day's DATA to travel, but never through git: the live DB contains broker
credentials (``broker_credentials`` table — Fyers app secret encrypted
at rest) and the repo is public. Pushing the raw DB to a branch would
effectively publish the credentials (the .encryption_key file sits next
to the DB on disk).

This module produces a COPY of the DB with all secret tables scrubbed:

    trades / positions / signals / shadow_outcomes / sessions / ...
    → travel.  broker_credentials → DELETED from the copy.

The receiving side re-authenticates the broker normally (one browser
flow); the trade ledger, open positions and shadow/ML samples continue
exactly where Part 1 left off.

CLI: scripts/export_handoff.py (thin wrapper around export_handoff()).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

#: Tables whose contents must NEVER leave the machine that owns them.
#: (broker_credentials holds the encrypted Fyers app token; the
#: encryption key file next to the DB would make the ciphertext
#: reversible — so the table, not just the key, is scrubbed here.)
SECRET_TABLES: List[str] = ["broker_credentials"]

_EOD_PREFIX = "ultrabot_eod_"
_HANDOFF_PREFIX = "ultrabot_handoff_"


def default_handoff_path(eod_dir_hint: Optional[str] = None) -> Path:
    """download/ultrabot_handoff_YYYYMMDD-HHMM.db (timestamped)."""
    ts = datetime.now(IST).strftime("%Y%m%d-%H%M")
    name = f"{_HANDOFF_PREFIX}{ts}.db"
    if eod_dir_hint:
        return Path(eod_dir_hint) / name
    # backend/core/handoff.py → parents[4] = <repo parent> → download/
    return Path(__file__).resolve().parents[4] / "download" / name


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    return [r[0] for r in cur.fetchall()]


def export_handoff(
    db_path: str,
    out_path: Optional[str] = None,
    include_credentials: bool = False,
) -> Dict[str, object]:
    """Copy the live DB (online backup API, WAL-safe) and scrub secrets.

    Returns a stats dict: out_path, size_bytes, tables, scrubbed,
    rows_deleted. Raises only on hard I/O failures (caller decides).
    """
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")

    dest = Path(out_path) if out_path else default_handoff_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    # 1) consistent online copy
    sconn = sqlite3.connect(str(src))
    dconn = sqlite3.connect(str(dest))
    try:
        sconn.backup(dconn)
    finally:
        sconn.close()

    # 2) scrub secret tables in the copy
    rows_deleted: Dict[str, int] = {}
    scrubbed: List[str] = []
    try:
        tables = _list_tables(dconn)
        for table in SECRET_TABLES:
            if include_credentials:
                continue
            if table not in tables:
                continue
            cur = dconn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed name
            rows_deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            scrubbed.append(table)
        dconn.commit()
        # 3) reclaim space + honest integrity verdict
        dconn.execute("VACUUM")
        integrity = dconn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"handoff copy failed integrity_check: {integrity}")
    finally:
        dconn.close()

    stats: Dict[str, object] = {
        "out_path": str(dest),
        "size_bytes": dest.stat().st_size,
        "tables": tables,
        "scrubbed": scrubbed,
        "rows_deleted": rows_deleted,
        "generated_at": datetime.now(IST).isoformat(),
    }
    logger.info(
        "Handoff DB exported: %s (%.1f KB, scrubbed: %s)",
        dest, dest.stat().st_size / 1024.0, scrubbed or "none",
    )
    return stats


def verify_handoff(out_path: str) -> Dict[str, object]:
    """Post-export sanity: integrity ok + no secret rows remain."""
    conn = sqlite3.connect(out_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        remaining: Dict[str, int] = {}
        for table in SECRET_TABLES:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                remaining[table] = int(n)
            except sqlite3.OperationalError:
                remaining[table] = -1  # table absent = clean
        return {"integrity": integrity, "secret_rows_remaining": remaining}
    finally:
        conn.close()
