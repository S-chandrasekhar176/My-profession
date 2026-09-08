"""v0.4.13 M2 — DB backup job + handoff exporter tests.

Covers:
- DatabaseBackupJob: snapshot copies live data, missing DB -> None,
  retention pruning, EOD due-helper semantics, stable EOD filename
- export_handoff: secret tables scrubbed (broker_credentials), trade
  data survives, integrity ok, include_credentials escape hatch
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.db_backup import DatabaseBackupJob, should_run_eod
from core.handoff import SECRET_TABLES, export_handoff, verify_handoff

IST = ZoneInfo("Asia/Kolkata")


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.execute("INSERT INTO trades (symbol) VALUES ('SUPREMEIND')")
    conn.execute(
        "CREATE TABLE broker_credentials (id INTEGER PRIMARY KEY, broker TEXT, secret TEXT)"
    )
    conn.execute(
        "INSERT INTO broker_credentials (broker, secret) VALUES ('fyers', 'topsecret')"
    )
    conn.commit()
    conn.close()


# ── DatabaseBackupJob ────────────────────────────────────────────────

def test_snapshot_copies_live_data(tmp_path: Path):
    db = tmp_path / "ultrabot.db"
    _make_db(db)
    job = DatabaseBackupJob(str(db), {"snapshot_dir": str(tmp_path / "snaps")})
    snap = job.take_snapshot()
    assert snap is not None and snap.exists()
    conn = sqlite3.connect(str(snap))
    row = conn.execute("SELECT symbol FROM trades").fetchone()
    conn.close()
    assert row == ("SUPREMEIND",)


def test_snapshot_missing_db_returns_none(tmp_path: Path):
    job = DatabaseBackupJob(str(tmp_path / "nope.db"), {"snapshot_dir": str(tmp_path / "snaps")})
    assert job.take_snapshot() is None


def test_retention_prunes_old_snapshots(tmp_path: Path):
    db = tmp_path / "ultrabot.db"
    _make_db(db)
    job = DatabaseBackupJob(
        str(db), {"snapshot_dir": str(tmp_path / "snaps"), "keep_snapshots": 3}
    )
    made = []
    for i in range(5):
        snap = job.take_snapshot()
        assert snap is not None
        # force distinct mtimes so "newest" is deterministic
        os.utime(snap, (1000000 + i * 100, 1000000 + i * 100))
        made.append(snap)
    remaining = sorted((tmp_path / "snaps").glob("ultrabot_*.db"))
    assert len(remaining) == 3
    # the three NEWEST survive (i = 2, 3, 4)
    assert made[4] in remaining and made[3] in remaining and made[2] in remaining
    assert made[0] not in remaining and made[1] not in remaining


def test_should_run_eod_semantics():
    now = datetime(2026, 9, 8, 15, 40, tzinfo=IST)
    # due today, not yet run -> fire
    assert should_run_eod(now, "15:35", None) is True
    assert should_run_eod(now, "15:35", "2026-09-07") is True
    # already run today -> no
    assert should_run_eod(now, "15:35", "2026-09-08") is False
    # before due time -> no
    assert should_run_eod(now.replace(hour=10, minute=0), "15:35", None) is False
    # disabled -> never
    assert should_run_eod(now, "", None) is False
    assert should_run_eod(now, None, None) is False


def test_eod_copy_stable_filename(tmp_path: Path):
    db = tmp_path / "ultrabot.db"
    _make_db(db)
    eod_dir = tmp_path / "eod"
    job = DatabaseBackupJob(
        str(db), {"snapshot_dir": str(tmp_path / "snaps"), "eod_dir": str(eod_dir)}
    )
    now = datetime(2026, 9, 8, 15, 35, tzinfo=IST)
    out = job.write_eod_copy(now_ist=now)
    assert out is not None
    assert out.name == "ultrabot_eod_20260908.db"
    assert out.exists()


def test_defaults_are_sane(tmp_path: Path):
    job = DatabaseBackupJob(str(tmp_path / "x.db"), {})
    assert job.interval_min >= 5
    assert job.keep_snapshots >= 3
    assert "persist" in str(job.snapshot_dir)


# ── export_handoff ───────────────────────────────────────────────────

def test_export_scrubs_credentials_keeps_trades(tmp_path: Path):
    db = tmp_path / "ultrabot.db"
    _make_db(db)
    out = tmp_path / "handoff.db"
    stats = export_handoff(str(db), out_path=str(out))
    assert Path(stats["out_path"]).exists()
    assert "broker_credentials" in stats["scrubbed"]

    check = verify_handoff(str(out))
    assert check["integrity"] == "ok"
    assert check["secret_rows_remaining"]["broker_credentials"] == 0

    conn = sqlite3.connect(str(out))
    trades = conn.execute("SELECT symbol FROM trades").fetchall()
    conn.close()
    assert trades == [("SUPREMEIND",)]


def test_export_include_credentials_escape_hatch(tmp_path: Path):
    db = tmp_path / "ultrabot.db"
    _make_db(db)
    out = tmp_path / "handoff_full.db"
    stats = export_handoff(str(db), out_path=str(out), include_credentials=True)
    assert stats["scrubbed"] == []
    check = verify_handoff(str(out))
    assert check["secret_rows_remaining"]["broker_credentials"] == 1


def test_export_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        export_handoff(str(tmp_path / "ghost.db"), out_path=str(tmp_path / "o.db"))


def test_secret_tables_list_is_explicit():
    # the sanitizer must never "discover" tables dynamically — an
    # allowlist of secrets only, trade data always travels
    assert SECRET_TABLES == ["broker_credentials"]
