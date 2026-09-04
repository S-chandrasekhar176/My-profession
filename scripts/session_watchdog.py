#!/usr/bin/env python3
"""Session watchdog: keep the backend alive and snapshot the DB.

Why: the sandbox periodically sweeps /tmp, backend/data, backend/reports and
reaps processes (incident 2026-09-03 ~11:39 IST). Mitigation:
- Backend runs with DB_PATH + ENCRYPTION_KEY in bot_analysis/persist/ (swept area avoided)
- This watchdog restarts the backend if health fails, using the same env vars
- Every 15 min the SQLite DB + EOD reports are snapshotted into persist/snapshots/

Usage: python3 session_watchdog.py  (run via scripts/daemonize.py)
"""
import os
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "http://127.0.0.1:8000"
PERSIST = Path("/home/z/my-project/bot_analysis/persist")
SNAP = PERSIST / "snapshots"
LOGS = Path("/home/z/my-project/bot_analysis/logs")
DAEMONIZE = "/home/z/my-project/bot_analysis/Awesome_DE/scripts/daemonize.py"
VENV_PY = "/home/z/my-project/bot_analysis/venv/bin/python"
APP_DIR = "/home/z/my-project/bot_analysis/Awesome_DE/ultrabot-web/backend"
REPORTS = APP_DIR + "/reports"

SNAP_DIR = SNAP
MAX_SNAPS = 12


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)


def health_ok() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + "/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def start_backend() -> None:
    env = {
        **os.environ,
        "DB_PATH": str(PERSIST / "ultrabot.db"),
        "ENCRYPTION_KEY": (PERSIST / ".encryption_key").read_text().strip(),
    }
    subprocess.run(
        ["python3", DAEMONIZE, str(LOGS / "backend.log"),
         VENV_PY, "-m", "uvicorn", "app:app",
         "--host", "127.0.0.1", "--port", "8000", "--app-dir", APP_DIR],
        cwd=APP_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log("backend (re)start issued")


def snapshot() -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    db = PERSIST / "ultrabot.db"
    if db.exists():
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, SNAP_DIR / f"ultrabot-{ts}{suffix or '.db'}")
        log(f"db snapshot {ts}")
    rep = Path(REPORTS)
    if rep.is_dir():
        for f in rep.glob("*.pdf"):
            dst = SNAP_DIR / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                log(f"report backed up: {f.name}")
    # prune old snapshots (keep MAX_SNAPS by mtime, per extension family)
    snaps = sorted(SNAP_DIR.glob("ultrabot-*-wal"), key=lambda p: p.stat().st_mtime)
    for extra in snaps[:-MAX_SNAPS]:
        stem = extra.name.replace("-wal", "")
        for suffix in ("-wal", "-shm", ".db"):
            old = SNAP_DIR / (stem + suffix)
            if old.exists():
                old.unlink()


def main() -> None:
    log(f"watchdog started (pid={os.getpid()})")
    last_snap = 0.0
    while True:
        try:
            if not health_ok():
                log("health DOWN — restarting backend")
                start_backend()
                time.sleep(15)
                log("post-restart health: " + ("OK" if health_ok() else "STILL DOWN"))
            if time.time() - last_snap > 900:
                snapshot()
                last_snap = time.time()
        except Exception as exc:
            log(f"watchdog error: {exc}")
        time.sleep(60)


if __name__ == "__main__":
    main()
