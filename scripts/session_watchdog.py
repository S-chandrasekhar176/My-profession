#!/usr/bin/env python3
"""Session watchdog v2 (v0.4.17): keep the backend alive, and when it CAN'T,
leave behind enough evidence to answer "what killed it, and when".

Why: across 2026-09-07/08 the sandbox backend died silently ~10× during
market hours (no traceback, no log tail — the process was simply gone).
Evidence so far points to TWO distinct phenomena:

  L1/L2 — backend-process death (workspace survives): Sep 7 the watcher kept
          logging "Connection refused" 13:08→15:30 IST after the backend died.
  L3    — workspace suspension / recycle (EVERYTHING dies, even a trivial
          bash canary; filesystem later re-provisioned): Sep 7 19:17 IST and
          the 4th wipe on Sep 8 evening.

What this watchdog does about it:
  * polls /api/health every POLL_INTERVAL seconds (was 60s, now 15s)
  * appends a heartbeat line to persist/heartbeat.log (UP/DOWN + latency)
  * spawns TWO independent canary processes (scripts/canary_heartbeat.py)
  * on alive→down transition: writes a forensic bundle to
    persist/death_reports/death-<ts>.json — process census, canary freshness,
    backend-log tail, meminfo/loadavg, best-effort dmesg — and CLASSIFIES
    the death (backend_process_death vs workspace_suspension)
  * alerts Telegram (best-effort, creds read from config yaml) EXCEPT when
    the stop was intentional (see marker below) or the workspace itself froze
  * auto-restarts the backend with a per-day cap and exponential backoff
    (position rehydration is proven; restart storms are the real risk)
  * honors an INTENTIONAL-STOP marker: `touch persist/BACKEND_STOPPED_
    INTENTIONALLY` before a graceful stop suppresses restart+alert for
    STOP_MARKER_TTL (2h) — so the watchdog stops fighting planned shutdowns
  * keeps the v0.4.13 15-minute DB snapshot job unchanged

Usage: python3 session_watchdog.py  (run via scripts/daemonize.py)
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, date
from pathlib import Path

import sys

BASE = "http://127.0.0.1:8000"
PERSIST = Path("/home/z/my-project/bot_analysis/persist")
SNAP = PERSIST / "snapshots"
LOGS = Path("/home/z/my-project/bot_analysis/logs")
DEATH_REPORTS = PERSIST / "death_reports"
STOP_MARKER = PERSIST / "BACKEND_STOPPED_INTENTIONALLY"
RESTART_COUNTER = PERSIST / "watchdog_restarts.json"
DAEMONIZE = "/home/z/my-project/bot_analysis/Awesome_DE/scripts/daemonize.py"
WATCHDOG_DIR = "/home/z/my-project/bot_analysis/Awesome_DE/scripts"
VENV_PY = "/home/z/my-project/bot_analysis/venv/bin/python"
APP_DIR = "/home/z/my-project/bot_analysis/Awesome_DE/ultrabot-web/backend"
REPORTS = APP_DIR + "/reports"
BACKEND_LOG = LOGS / "backend.log"

# v0.4.21: shared loop-health policy (pure, zero-dep) — the backend imports
# the same module for /api/health + /api/engine/status, so the watchdog, the
# health endpoint and the engine can never disagree about stall semantics.
sys.path.insert(0, APP_DIR)
try:
    from core.loop_health import (
        NEVER_BEAT_SENTINEL,
        STALL_RESTART_GUARD_SECONDS,
        STALL_RESTART_MAX_PER_DAY,
        STALL_RESTART_THRESHOLD_SECONDS,
        market_open_ist,
        should_stall_restart,
    )
except Exception:  # pragma: no cover — host may run an older checkout
    NEVER_BEAT_SENTINEL = -1.0
    STALL_RESTART_GUARD_SECONDS = 180.0
    STALL_RESTART_MAX_PER_DAY = 2
    STALL_RESTART_THRESHOLD_SECONDS = 900.0

    def market_open_ist(now=None):
        from datetime import timezone, timedelta as _td

        local = (now or datetime.now()).astimezone(timezone(_td(hours=5, minutes=30)))
        if local.weekday() >= 5:
            return False
        m = local.hour * 60 + local.minute
        return 9 * 60 + 15 <= m <= 15 * 60 + 30

    def should_stall_restart(stalled_seconds, stall_restarts_today, market_open,
                             stop_marker_fresh, seconds_since_last_restart,
                             threshold=900.0, max_per_day=2, guard_seconds=180.0):
        if stalled_seconds is None:
            return False, "no stall data"
        if stalled_seconds >= 0 and stalled_seconds < threshold:
            return False, "below threshold"
        if stall_restarts_today >= max_per_day:
            return False, "cap reached"
        if not market_open:
            return False, "market closed"
        if stop_marker_fresh:
            return False, "stop marker fresh"
        if seconds_since_last_restart is not None and seconds_since_last_restart < guard_seconds:
            return False, "restart guard"
        return True, "stall sustained"


TG_POLL_STALE_BASE_S = 120.0  # v0.4.21: alert floor for telegram poll staleness

POLL_INTERVAL = 15
SNAPSHOT_INTERVAL = 900
MAX_SNAPS = 12
MAX_RESTARTS_PER_DAY = 5
BACKOFF_BASE = 15.0
BACKOFF_CAP = 300.0
STOP_MARKER_TTL = 2 * 3600.0
CANARY_STALE_SECONDS = 90
HEARTBEAT_MAX_BYTES = 5 * 1024 * 1024
CANARY_NAMES = ("a", "b")


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)


# ────────────────────────────────────────────
# Heartbeat
# ────────────────────────────────────────────

def heartbeat(state: str, latency_ms: int = -1) -> None:
    try:
        PERSIST.mkdir(parents=True, exist_ok=True)
        hb = PERSIST / "heartbeat.log"
        if hb.exists() and hb.stat().st_size > HEARTBEAT_MAX_BYTES:
            shutil.copy2(hb, PERSIST / "heartbeat.log.1")
            hb.write_text("")
        with open(PERSIST / "heartbeat.log", "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')},{state},{latency_ms}\n")
    except Exception:
        pass  # heartbeat must never kill the watchdog


# ────────────────────────────────────────────
# Health
# ────────────────────────────────────────────

def health_ok() -> tuple[bool, int]:
    start = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + "/api/health", timeout=5) as r:
            return r.status == 200, int((time.monotonic() - start) * 1000)
    except Exception:
        return False, -1


# v0.4.18: loop-liveness stall detection. A backend can answer /api/health
# (process alive) while the engine's main loop is wedged — the invisible
# Sep-9 13:35 failure: UI said "scanning" for 2 hours while nothing scanned.
# /api/engine/status now exposes loop_stalled_seconds; alert (but do NOT
# restart — a stall is not a death) when it exceeds the threshold.
LOOP_STALL_ALERT_SECONDS = 300.0
STALL_RESTART_COUNTER = PERSIST / "watchdog_stall_restarts.json"
_last_restart_ts = 0.0  # epoch of the most recent restart (any path)


def fetch_health() -> dict | None:
    """Parsed /api/health payload (single fetch per watchdog poll)."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + "/api/health", timeout=5) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def loop_stalled_seconds(health: dict | None = None) -> float | None:
    """loop_stalled_seconds from the unauthenticated /api/health payload
    (the engine/status endpoint requires API credentials the watchdog does
    not hold), or None if unavailable."""
    try:
        data = health if health is not None else fetch_health()
        if data is None:
            return None
        raw = data.get("loop_stalled_seconds")
        return float(raw) if raw is not None else None
    except Exception:
        return None


# ────────────────────────────────────────────
# Canary management + freshness
# ────────────────────────────────────────────

def _proc_census() -> list:
    """Snapshot of interesting live processes: [(pid, state, cmdline-prefix)]."""
    out = []
    try:
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                cmd = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore").strip()
                state = (pid_dir / "stat").read_text().split()[2] if (pid_dir / "stat").exists() else "?"
            except Exception:
                continue
            if cmd and any(k in cmd for k in ("uvicorn", "canary_heartbeat", "session_watchdog", "app:app")):
                out.append({"pid": int(pid_dir.name), "state": state, "cmd": cmd[:160]})
    except Exception:
        pass
    return out


def canary_age(name: str) -> float:
    """Seconds since the canary last wrote its timestamp file; -1 if unreadable."""
    path = PERSIST / f"canary_{name}.heartbeat"
    try:
        raw = path.read_text().strip()
        ts = datetime.fromisoformat(raw)
        return (datetime.now(ts.tzinfo) - ts).total_seconds() if ts.tzinfo else (datetime.now() - ts).total_seconds()
    except Exception:
        return -1.0


def spawn_canaries() -> None:
    fresh = {n for n in CANARY_NAMES if 0 <= canary_age(n) <= CANARY_STALE_SECONDS}
    alive = {p.get("cmd", "") for p in _proc_census() if "canary_heartbeat" in p.get("cmd", "")}
    for name in CANARY_NAMES:
        if name in fresh:
            continue
        # no fresh heartbeat — is a canary process even running for it?
        running = any(f"canary_heartbeat.py {name}" in cmd for cmd in alive)
        if not running:
            try:
                subprocess.run(
                    ["python3", DAEMONIZE, str(LOGS / f"canary_{name}.log"),
                     "python3", str(Path(WATCHDOG_DIR) / "canary_heartbeat.py"), name],
                    cwd=WATCHDOG_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                log(f"canary '{name}' spawned")
            except Exception as exc:
                log(f"canary '{name}' spawn failed: {exc}")


# ────────────────────────────────────────────
# Forensics
# ────────────────────────────────────────────

def capture_forensics(last_up_iso: str) -> dict:
    report = {
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "last_seen_up": last_up_iso,
        "census": _proc_census(),
        "canaries": {n: canary_age(n) for n in CANARY_NAMES},
        "mem": {},
        "loadavg": "",
        "backend_log_tail": [],
        "dmesg_tail": [],
    }
    try:
        for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith(key):
                    report["mem"][key] = line.split(":", 1)[1].strip()
                    break
        report["loadavg"] = Path("/proc/loadavg").read_text().strip()
    except Exception:
        pass
    try:
        if BACKEND_LOG.exists():
            lines = BACKEND_LOG.read_text(errors="ignore").splitlines()
            report["backend_log_tail"] = lines[-150:]
    except Exception:
        pass
    try:
        r = subprocess.run(["dmesg", "-T", "--time-format", "iso"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            report["dmesg_tail"] = r.stdout.splitlines()[-40:]
    except Exception:
        pass  # containerized: usually permission-denied — absence is itself data

    ages = report["canaries"]
    stale = [n for n, a in ages.items() if a < 0 or a > CANARY_STALE_SECONDS]
    backend_down_but_alive = any(
        "uvicorn" in p.get("cmd", "") or "app:app" in p.get("cmd", "") for p in report["census"]
    )
    if backend_down_but_alive:
        # process exists but health fails — hang, not death
        report["classification"] = "backend_hung (process alive, health failing)"
    elif len(stale) == len(CANARY_NAMES):
        report["classification"] = "workspace_suspension_or_recycle (L3 — everything froze)"
    else:
        report["classification"] = "backend_process_death (L1/L2 — canaries alive)"
    return report


def write_death_report(last_up_iso: str) -> dict:
    try:
        DEATH_REPORTS.mkdir(parents=True, exist_ok=True)
        report = capture_forensics(last_up_iso)
        path = DEATH_REPORTS / f"death-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        log(f"DEATH REPORT -> {path} [{report['classification']}]")
        return report
    except Exception as exc:
        log(f"death report failed: {exc}")
        return {"classification": "report_failed"}


# ────────────────────────────────────────────
# Telegram (best-effort, standalone)
# ────────────────────────────────────────────

def _telegram_creds() -> tuple:
    for name in ("defaults.local.yaml", "defaults.yaml"):
        path = Path(APP_DIR) / "config" / name
        try:
            import yaml
            cfg = yaml.safe_load(path.read_text()) or {}
            notif = (cfg.get("notifications") or {}) if isinstance(cfg, dict) else {}
            token = str(notif.get("telegram_bot_token") or "").strip()
            chat = str(notif.get("telegram_chat_id") or "").strip()
            if token and chat:
                return token, chat
        except Exception:
            continue
    return "", ""


def telegram_alert(text: str) -> None:
    try:
        token, chat = _telegram_creds()
        if not token or not chat:
            log("telegram alert skipped (no creds in config)")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat, "text": text[:3900]}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as r:
            r.read()
        log("telegram alert sent")
    except Exception as exc:
        log(f"telegram alert failed: {exc}")


# ────────────────────────────────────────────
# Restart policy
# ────────────────────────────────────────────

def restarts_today() -> int:
    try:
        data = json.loads(RESTART_COUNTER.read_text())
        if data.get("date") == date.today().isoformat():
            return int(data.get("count", 0))
    except Exception:
        pass
    return 0


def bump_restart_counter() -> int:
    count = restarts_today() + 1
    try:
        data = {}
        try:
            data = json.loads(RESTART_COUNTER.read_text())
        except Exception:
            data = {}
        data["date"] = date.today().isoformat()
        data["count"] = count
        RESTART_COUNTER.write_text(json.dumps(data))
    except Exception:
        pass
    return count


def stall_restarts_today() -> int:
    try:
        data = json.loads(STALL_RESTART_COUNTER.read_text())
        if data.get("date") == date.today().isoformat():
            return int(data.get("stall_count", 0))
    except Exception:
        pass
    return 0


def bump_stall_restart_counter() -> int:
    count = stall_restarts_today() + 1
    try:
        STALL_RESTART_COUNTER.write_text(
            json.dumps({"date": date.today().isoformat(), "stall_count": count})
        )
    except Exception:
        pass
    return count


def seconds_since_last_restart() -> float:
    """Seconds since the most recent restart of ANY path (death or stall).
    Drives the shared anti-storm guard so the two recovery paths cannot
    restart on top of each other."""
    global _last_restart_ts
    if _last_restart_ts <= 0.0:
        return None
    return max(time.time() - _last_restart_ts, 0.0)


def stop_marker_fresh() -> bool:
    try:
        age = time.time() - STOP_MARKER.stat().st_mtime
        return age < STOP_MARKER_TTL
    except Exception:
        return False


def start_backend() -> None:
    env = {
        **os.environ,
        "DB_PATH": str(PERSIST / "ultrabot.db"),
        "ENCRYPTION_KEY": (PERSIST / ".encryption_key").read_text().strip(),
    }
    subprocess.run(
        ["python3", DAEMONIZE, str(BACKEND_LOG),
         VENV_PY, "-m", "uvicorn", "app:app",
         "--host", "127.0.0.1", "--port", "8000", "--app-dir", APP_DIR],
        cwd=APP_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    global _last_restart_ts
    _last_restart_ts = time.time()
    log("backend (re)start issued")


def snapshot() -> None:
    SNAP.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    db = PERSIST / "ultrabot.db"
    if db.exists():
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, SNAP / f"ultrabot-{ts}{suffix or '.db'}")
        log(f"db snapshot {ts}")
    rep = Path(REPORTS)
    if rep.is_dir():
        for f in rep.glob("*.pdf"):
            dst = SNAP / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                log(f"report backed up: {f.name}")
    # prune old snapshots (keep MAX_SNAPS by mtime, per extension family)
    snaps = sorted(SNAP.glob("ultrabot-*-wal"), key=lambda p: p.stat().st_mtime)
    for extra in snaps[:-MAX_SNAPS]:
        stem = extra.name.replace("-wal", "")
        for suffix in ("-wal", "-shm", ".db"):
            old = SNAP / (stem + suffix)
            if old.exists():
                old.unlink()


# ────────────────────────────────────────────
# Main loop
# ────────────────────────────────────────────

def main() -> None:
    log(f"watchdog v2 started (pid={os.getpid()}, poll={POLL_INTERVAL}s)")
    LOGS.mkdir(parents=True, exist_ok=True)
    spawn_canaries()
    last_snap = 0.0
    last_up_iso = datetime.now().isoformat(timespec="seconds")
    was_up = True
    backoff_n = 0
    loop_stall_alerted = False
    stall_streak = 0  # v0.4.21: consecutive above-threshold stall readings
    tg_poll_alerted = False  # v0.4.21: telegram-poll deaf alert (edge-triggered)

    while True:
        try:
            up, latency = health_ok()
            heartbeat("UP" if up else "DOWN", latency if up else -1)

            # v0.4.21: one health fetch per poll, shared by the stall check
            # and the telegram-poll liveness check.
            health = fetch_health() if up else None

            if up:
                if not was_up:
                    log(f"backend RECOVERED (latency {latency}ms)")
                was_up = True
                last_up_iso = datetime.now().isoformat(timespec="seconds")

                # v0.4.18: loop-stall alert (edge-triggered, no restart).
                # v0.4.21: a never-beat loop (-1 sentinel from /api/health,
                # the post-restart dead-loop case) also alerts, and a
                # SUSTAINED stall now escalates to an automatic
                # stall-restart — rails live in core/loop_health.
                # should_stall_restart (threshold, daily cap, market-open
                # gate, intentional-stop marker, shared restart guard).
                try:
                    stalled = loop_stalled_seconds(health)
                    stalled_bad = (
                        stalled is not None
                        and (stalled >= LOOP_STALL_ALERT_SECONDS or stalled == NEVER_BEAT_SENTINEL)
                    )
                    if stalled_bad and not loop_stall_alerted:
                        loop_stall_alerted = True
                        stall_label = (
                            "never beat (post-restart dead loop)"
                            if stalled == NEVER_BEAT_SENTINEL
                            else f"{stalled:.0f}s"
                        )
                        log(f"ENGINE LOOP STALLED {stall_label} (process alive) — alerting")
                        telegram_alert(
                            "🟠 UltraBot: ENGINE LOOP STALLED — backend answers but the "
                            f"main loop has not iterated for {stall_label} "
                            f"({datetime.now().strftime('%H:%M:%S')} IST). "
                            "Positions/exits are NOT being managed. Manual restart recommended."
                        )
                    elif stalled is not None and 0 <= stalled < LOOP_STALL_ALERT_SECONDS:
                        loop_stall_alerted = False

                    # v0.4.21 escalation — alerting alone does not manage
                    # positions; a wedged loop during market hours is worse
                    # than a restart with the proven rehydration path.
                    if stalled is not None and (
                        stalled >= STALL_RESTART_THRESHOLD_SECONDS or stalled == NEVER_BEAT_SENTINEL
                    ):
                        stall_streak += 1
                    else:
                        stall_streak = 0
                    if stall_streak >= STALL_STREAK_NEEDED:
                        decision, reason = should_stall_restart(
                            stalled,
                            stall_restarts_today(),
                            market_open_ist(),
                            stop_marker_fresh(),
                            seconds_since_last_restart(),
                        )
                        if decision:
                            stall_count = bump_stall_restart_counter()
                            stall_label = (
                                "never beat (post-restart dead loop)"
                                if stalled == NEVER_BEAT_SENTINEL
                                else f"{stalled:.0f}s"
                            )
                            log(
                                f"STALL-RESTART #{stall_count}/{STALL_RESTART_MAX_PER_DAY} "
                                f"issued ({reason})"
                            )
                            telegram_alert(
                                "🟠 UltraBot: ENGINE LOOP STALL-RESTART — loop "
                                f"{stall_label} without iterating while the process "
                                f"answers. Auto-restarting "
                                f"({stall_count}/{STALL_RESTART_MAX_PER_DAY} today, "
                                f"{datetime.now().strftime('%H:%M:%S')} IST). "
                                "Position rehydration will restore management."
                            )
                            start_backend()
                            stall_streak = 0
                            loop_stall_alerted = False
                        else:
                            log(f"stall escalation withheld: {reason}")
                except Exception:
                    pass

                # v0.4.21: telegram-poll liveness — the interactive bot can go
                # deaf (dead/hung poll task) while the backend and engine are
                # otherwise fine (the 11:16-IST report). /api/health exposes
                # the poll heartbeat; alert when stale beyond the long-poll
                # floor. Not a restart trigger: the backend respawns the loop
                # in-process (cap 5/day); this is the escalation beacon.
                try:
                    if health is not None and "telegram_poll_stalled_seconds" in health:
                        tg_stalled = health.get("telegram_poll_stalled_seconds")
                        tg_timeout = float(health.get("telegram_poll_timeout") or 0)
                        tg_limit = max(TG_POLL_STALE_BASE_S, tg_timeout + 60.0)
                        tg_respawns = int(health.get("telegram_poll_respawns") or 0)
                        if tg_stalled is not None and float(tg_stalled) > tg_limit:
                            if not tg_poll_alerted:
                                tg_poll_alerted = True
                                log(
                                    "TELEGRAM POLL stale %.0fs (> %.0fs floor, respawns=%d) — bot deaf on Telegram"
                                    % (float(tg_stalled), tg_limit, tg_respawns)
                                )
                                telegram_alert(
                                    "🟠 UltraBot: TELEGRAM POLL LOOP not responding for "
                                    f"{float(tg_stalled):.0f}s (respawns today: {tg_respawns}) "
                                    f"({datetime.now().strftime('%H:%M:%S')} IST). "
                                    "Bot is deaf on Telegram — engine may still be trading. "
                                    "Manual backend restart recommended if this persists."
                                )
                        elif tg_stalled is not None:
                            tg_poll_alerted = False
                except Exception:
                    pass
            else:
                if was_up:
                    # alive→down transition: forensics FIRST (evidence decays)
                    report = write_death_report(last_up_iso)
                    if stop_marker_fresh():
                        log("backend DOWN but intentional-stop marker is fresh — no restart, no alert")
                    elif "workspace_suspension" in report.get("classification", ""):
                        log("workspace-level freeze detected — in-workspace restart is futile")
                        telegram_alert(
                            "🟣 UltraBot: WORKSPACE-LEVEL FREEZE detected "
                            f"({datetime.now().strftime('%H:%M:%S')} IST). "
                            "Backend + canaries all stopped. Needs platform-level attention."
                        )
                    else:
                        bump = restarts_today()
                        since = seconds_since_last_restart()
                        if bump >= MAX_RESTARTS_PER_DAY:
                            log(f"restart cap reached ({bump}/{MAX_RESTARTS_PER_DAY}) — NOT restarting")
                            telegram_alert(
                                "🔴 UltraBot: backend DOWN and restart cap reached "
                                f"({bump} today). Manual attention needed."
                            )
                        elif since is not None and since < STALL_RESTART_GUARD_SECONDS:
                            # v0.4.21: a stall-restart may have just bounced the
                            # backend — do not race it with a death-restart.
                            log(
                                "backend DOWN but a restart was issued "
                                f"{since:.0f}s ago (< {STALL_RESTART_GUARD_SECONDS:.0f}s guard) — waiting"
                            )
                        else:
                            wait = min(BACKOFF_BASE * (2 ** backoff_n), BACKOFF_CAP)
                            log(f"backend DOWN — restarting in {wait:.0f}s "
                                f"(restart {bump + 1}/{MAX_RESTARTS_PER_DAY})")
                            telegram_alert(
                                "🔴 UltraBot: backend died unexpectedly at "
                                f"{datetime.now().strftime('%H:%M:%S')} IST "
                                f"(last seen up {last_up_iso}). Auto-restarting in {wait:.0f}s."
                            )
                            time.sleep(wait)
                            start_backend()
                            bump_restart_counter()
                            backoff_n += 1
                    was_up = False
                else:
                    # still down across polls — try recovery if cap allows
                    if not stop_marker_fresh():
                        bump = restarts_today()
                        since = seconds_since_last_restart()
                        if bump < MAX_RESTARTS_PER_DAY and backoff_n < 4 and not (
                            since is not None and since < STALL_RESTART_GUARD_SECONDS
                        ):
                            wait = min(BACKOFF_BASE * (2 ** backoff_n), BACKOFF_CAP)
                            log(f"backend still DOWN — retry in {wait:.0f}s")
                            time.sleep(wait)
                            start_backend()
                            bump_restart_counter()
                            backoff_n += 1

            if up:
                backoff_n = 0

            spawn_canaries()
            if time.time() - last_snap > SNAPSHOT_INTERVAL:
                snapshot()
                last_snap = time.time()
        except Exception as exc:
            log(f"watchdog error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
