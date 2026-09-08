"""v0.4.13 M2 — periodic DB backup job (survive workspace wipes).

Live incident 2026-09-07: a platform container recycle destroyed the
workspace mid-session. Lost: the day's trade ledger, 17 realtime-resolved
shadow samples (M3 gate clock reset to 0) and Fyers credentials. The old
external snapshot cron had died silently on Sep 4 — system crons are not
observable from inside the app, so backups now run as an in-process
asyncio task with the same fail-safety policy as the Telegram loops
(never raise into the application, log + retry on failure).

Design:
- Snapshot every ``snapshot_interval_minutes`` (default 15) using the
  SQLite **online backup API** (``Connection.backup``) — a consistent
  copy while the engine keeps writing (WAL-safe, no engine pause).
- Snapshots land in ``persist/snapshots/`` (auto: <bot_analysis>/persist,
  i.e. OUTSIDE the repo — this directory verifiably survives platform
  workspace recycles; Sep-4 files were still present after the Sep-7
  midday wipe). Overridable via ``persistence.snapshot_dir``.
- EOD copy at ``eod_time`` (default 15:35 IST) with a stable filename
  (``ultrabot_eod_YYYYMMDD.db``) — the handoff/EOD artifact.
- Retention: keep the newest ``keep_snapshots`` (default 32) snapshot
  files, prune the rest.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_SNAPSHOT_PREFIX = "ultrabot_"
_EOD_PREFIX = "ultrabot_eod_"


def _auto_snapshot_dir() -> Path:
    """backend/core/db_backup.py → bot_analysis/persist/snapshots.

    parents: [0]=core, [1]=backend, [2]=ultrabot-web, [3]=<repo>,
    [4]=<repo parent> — the dir that also holds persist/ and download/
    and survives platform recycles. Created on demand; harmless if the
    repo is cloned elsewhere (e.g. the user's machine gets
    <parent>/persist/snapshots, which is still outside the repo).
    """
    return Path(__file__).resolve().parents[4] / "persist" / "snapshots"


def should_run_eod(
    now_ist: datetime,
    eod_hhmm: Optional[str],
    last_eod_date: Optional[str],
) -> bool:
    """Pure helper: has today's EOD backup become due (and not yet run)?"""
    if not eod_hhmm:
        return False
    try:
        hh, mm = str(eod_hhmm).split(":")
        due = now_ist.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, AttributeError):
        return False
    today = now_ist.date().isoformat()
    return now_ist >= due and last_eod_date != today


class DatabaseBackupJob:
    """In-process SQLite backup loop. Never raises into the caller."""

    def __init__(self, db_path: str, config: Optional[dict] = None) -> None:
        cfg = dict(config or {})
        self.db_path = str(db_path)
        self.interval_min = max(5.0, float(cfg.get("snapshot_interval_minutes", 15)))
        self.keep_snapshots = max(3, int(cfg.get("keep_snapshots", 32)))
        self.eod_hhmm: Optional[str] = str(cfg.get("eod_time") or "").strip() or None
        self.snapshot_dir = Path(cfg.get("snapshot_dir") or _auto_snapshot_dir())
        self.eod_dir = Path(cfg.get("eod_dir") or self.snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.eod_dir.mkdir(parents=True, exist_ok=True)
        self._stop = False
        self._last_eod_date: Optional[str] = None

    # -- snapshot core ------------------------------------------------

    def take_snapshot(self, label: str = "snap") -> Optional[Path]:
        """Consistent online copy of the live DB. Returns the new file path."""
        if not Path(self.db_path).exists():
            logger.warning("DB backup skipped: %s does not exist yet", self.db_path)
            return None
        ts = datetime.now(IST).strftime("%Y%m%d-%H%M%S%f")  # microseconds: boot+manual snapshots in one second must not collide
        target = self.snapshot_dir / f"{_SNAPSHOT_PREFIX}{label}_{ts}.db"
        try:
            src = sqlite3.connect(self.db_path)
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        except Exception as exc:
            logger.error("DB snapshot failed (%s): %s", target.name, exc, exc_info=True)
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        self._prune()
        logger.info(
            "DB snapshot written: %s (%.1f KB)",
            target, target.stat().st_size / 1024.0,
        )
        return target

    def _prune(self) -> int:
        """Keep the newest ``keep_snapshots`` snapshots; delete the rest."""
        try:
            snaps = sorted(
                (p for p in self.snapshot_dir.glob(f"{_SNAPSHOT_PREFIX}*.db")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            removed = 0
            for old in snaps[self.keep_snapshots:]:
                try:
                    old.unlink()
                    removed += 1
                except Exception:
                    pass
            if removed:
                logger.info("DB snapshot retention: pruned %d old snapshot(s)", removed)
            return removed
        except Exception:
            logger.warning("DB snapshot pruning failed", exc_info=True)
            return 0

    def write_eod_copy(self, now_ist: Optional[datetime] = None) -> Optional[Path]:
        """Fresh snapshot + stable-name EOD copy (the handoff artifact)."""
        now_ist = now_ist or datetime.now(IST)
        snap = self.take_snapshot(label="eod")
        if snap is None:
            return None
        eod_path = self.eod_dir / (
            f"{_EOD_PREFIX}{now_ist.date().strftime('%Y%m%d')}.db"
        )
        try:
            shutil.copyfile(snap, eod_path)
            logger.info("EOD DB backup written: %s (%.1f KB)",
                        eod_path, eod_path.stat().st_size / 1024.0)
            return eod_path
        except Exception as exc:
            logger.error("EOD DB copy failed: %s", exc, exc_info=True)
            return None

    # -- loop ----------------------------------------------------------

    async def run_forever(self) -> None:
        """Boot snapshot, then snapshot every ``interval_min`` minutes and
        the EOD copy once per day. Sleeps in 20s ticks; never raises."""
        logger.info(
            "DB backup job: every %.0f min -> %s (keep %d); EOD %s -> %s",
            self.interval_min, self.snapshot_dir, self.keep_snapshots,
            self.eod_hhmm or "disabled", self.eod_dir,
        )
        try:
            await asyncio.to_thread(self.take_snapshot, "boot")
        except Exception as exc:
            logger.warning("Boot DB snapshot failed: %s", exc)

        next_snap = datetime.now(IST).astimezone()  # eager first tick bookkeeping
        import time as _time
        next_snap = _time.monotonic() + self.interval_min * 60.0
        while not self._stop:
            try:
                await asyncio.sleep(20)
                if self._stop:
                    return
                now_ist = datetime.now(IST)
                if _time.monotonic() >= next_snap:
                    await asyncio.to_thread(self.take_snapshot, "snap")
                    next_snap = _time.monotonic() + self.interval_min * 60.0
                if should_run_eod(now_ist, self.eod_hhmm, self._last_eod_date):
                    await asyncio.to_thread(self.write_eod_copy, now_ist)
                    self._last_eod_date = now_ist.date().isoformat()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("DB backup cycle failed: %s", exc, exc_info=True)
                await asyncio.sleep(30)

    def stop(self) -> None:
        self._stop = True
