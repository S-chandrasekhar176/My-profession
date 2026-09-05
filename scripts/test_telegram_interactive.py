"""v0.4.10 acceptance test: interactive Telegram round-trip on a real phone.

Runs InteractiveTelegramBot against a FakeEngine with a synthetic TEST
opportunity. The user taps Approve/Reject/Skip on Telegram; every callback
is executed through the same decision path used by the dashboard and
logged to an evidence JSON file (incremental, after every event).

Run (sandbox-safe, background):
  nohup venv/bin/python scripts/test_telegram_interactive.py \
      > /home/z/my-project/bot_analysis/persist/tg_test.log 2>&1 &

Evidence:
  /home/z/my-project/bot_analysis/persist/telegram_interactive_test_evidence.json
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = "/home/z/my-project/bot_analysis/Awesome_DE/ultrabot-web/backend"
sys.path.insert(0, BACKEND)

import yaml  # noqa: E402

from notifications.telegram_interactive import InteractiveTelegramBot  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
EVIDENCE_PATH = "/home/z/my-project/bot_analysis/persist/telegram_interactive_test_evidence.json"
CONFIG_PATH = os.path.join(BACKEND, "config", "defaults.yaml")
DURATION_S = int(os.environ.get("TEST_DURATION_S", "1200"))


class Evidence:
    def __init__(self, path):
        self.path = path
        self.events = []

    def add(self, kind, detail):
        self.events.append(
            {"ts": datetime.now(IST).isoformat(timespec="seconds"), "kind": kind, "detail": detail}
        )
        self.dump()
        print(f"[EVIDENCE] {kind}: {detail}", flush=True)

    def dump(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump({"test_started": TEST_STARTED, "events": self.events}, fh, indent=2)


TEST_STARTED = datetime.now(IST).isoformat(timespec="seconds")
EV = Evidence(EVIDENCE_PATH)


class FakeEngine:
    """Engine stand-in exposing the exact surface InteractiveTelegramBot uses."""

    def __init__(self):
        from types import SimpleNamespace

        self.state = SimpleNamespace(value="running")
        self.session_id = "test-session-0000"
        self._scan_count = 42
        self._signals_generated = 7
        self._trades_executed = 2
        self._opportunities_lock = asyncio.Lock()
        ts = datetime.now(IST).isoformat(timespec="seconds")
        rid = os.environ.get("TEST_OPP_ID", f"TEST-{datetime.now(IST).strftime('%H%M%S')}-A")
        self.pending_opportunities = {
            # Card 1 – user should REJECT this one
            rid: {
                "id": rid,
                "signal_id": "SIG-TEST-1",
                "created_at": ts,
                "symbol": "COLPAL",
                "name": "Colgate Palmolive",
                "direction": "SELL",
                "strategy": "SIC",
                "confidence": 0.71,
                "entry_price": 1831.10,
                "stop_loss": 1837.30,
                "target": 1818.50,
                "risk_reward": 2.0,
                "sl_distance_pct": 0.34,
                "target_pct": -0.69,
                "quantity": 40,
                "segment": "EQ",
                "capital_required": 73244.0,
                "risk_amount": 248.0,
                "vix": 12.4,
                "regime": "BULL",
                "market_trend": "BULL",
                "kronos_score": 7.6,
                "win_rate": 0.58,
                "risk_gates": {
                    "trade_window_gate": {"passed": True, "reason": "inside 09:20-15:10"},
                    "sector_concentration_gate": {"passed": True, "reason": "1/3 in FMCG"},
                    "cost_precheck_gate": {"passed": True, "reason": "fees 8% of risk"},
                },
                "_test_mode": True,
            },
            # Card 2 – user should SKIP this one
            rid + "B": {
                "id": rid + "B",
                "signal_id": "SIG-TEST-2",
                "created_at": ts,
                "symbol": "CIPLA",
                "name": "Cipla Ltd",
                "direction": "BUY",
                "strategy": "SIC",
                "confidence": 0.69,
                "entry_price": 1384.30,
                "stop_loss": 1380.20,
                "target": 1392.50,
                "risk_reward": 2.0,
                "sl_distance_pct": -0.30,
                "target_pct": 0.59,
                "quantity": 70,
                "segment": "EQ",
                "capital_required": 96901.0,
                "risk_amount": 287.0,
                "vix": 12.4,
                "regime": "BULL",
                "market_trend": "BULL",
                "kronos_score": 7.1,
                "win_rate": 0.55,
                "risk_gates": {
                    "trade_window_gate": {"passed": True, "reason": "inside 09:20-15:10"},
                    "volume_gate": {"passed": True, "reason": "1.4x avg volume"},
                },
                "_test_mode": True,
            }
        }

    async def confirm_opportunity(self, opportunity_id, segment="EQ"):
        EV.add("engine_confirm_called", {"opportunity_id": opportunity_id, "segment": segment})
        opp = self.pending_opportunities.pop(opportunity_id, None)
        if opp is None:
            return {"status": "not_found", "error": "not in pending list"}
        return {
            "status": "success",
            "trade": {
                "symbol": opp["symbol"],
                "direction": opp["direction"],
                "entry_price": opp["entry_price"],
                "quantity": opp["quantity"],
                "stop_loss": opp["stop_loss"],
                "target": opp["target"],
            },
        }

    async def skip_opportunity(self, opportunity_id, reason=None):
        EV.add("engine_skip_called", {"opportunity_id": opportunity_id, "reason": reason})
        opp = self.pending_opportunities.pop(opportunity_id, None)
        if opp is None:
            return {"status": "not_found", "error": "not in pending list"}
        return {"status": "skipped", "opportunity_id": opportunity_id, "reason": reason}

    async def pause(self):
        EV.add("engine_pause_called", {})
        self.state.value = "paused"
        return {"status": "success", "state": "paused"}

    async def resume(self):
        EV.add("engine_resume_called", {})
        self.state.value = "running"
        return {"status": "success", "state": "running"}


class FakeRepo:
    async def get_open_positions(self):
        return []

    async def get_todays_pnl(self):
        return {"realized_pnl": -284.73, "unrealized_pnl": 3.64, "total_pnl": -281.09}


async def repo_getter():
    return FakeRepo()


async def main():
    with open(CONFIG_PATH) as fh:
        raw = yaml.safe_load(fh)
    notif = raw.get("notifications", {})
    notif["telegram_interactive_enabled"] = True
    notif["telegram_canary_enabled"] = False  # market closed – canary off in test
    # Allow instant re-test with a fresh PRIVATE bot token (BotFather) without
    # touching the repo config. The stock token is third-party-owned (see
    # evidence: A_ToolsX gate bot answering commands) and must be replaced.
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_token:
        notif["telegram_bot_token"] = env_token
        print(f"Using TELEGRAM_BOT_TOKEN override ({env_token[:12]}...)", flush=True)

    engine = FakeEngine()

    class _OneWay:
        """Satisfies the duck-type; the interactive module uses its own _tg."""

        def __init__(self):
            self.bot_token = notif.get("telegram_bot_token", "")
            self.chat_id = str(notif.get("telegram_chat_id", ""))

    bot = InteractiveTelegramBot(
        telegram_bot=_OneWay(),
        engine=engine,
        repo_getter=repo_getter,
        notif_config=notif,
    )

    print(f"Interactive test starting — chat={bot._chat_id} duration={DURATION_S}s", flush=True)

    intro = (
        "🧪 <b>v0.4.10 Interactive Telegram — TEST MODE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "A TEST opportunity card arrives next.\n"
        "Tap any button — ✅ Approve, ❌ Reject, ⏭ Skip — or ℹ️ Why.\n"
        "Commands also live in TEST mode: try /status, /pnl, /positions, /help.\n"
        "Nothing touches the real engine or broker (sandbox test)."
    )
    ok = await bot._tg(
        "sendMessage", chat_id=bot._chat_id, text=intro, parse_mode="HTML"
    )
    EV.add("intro_sent", {"ok": bool(ok and ok.get("ok"))})

    bot.start()
    if not bot._tasks:
        print("Bot did not start — check credentials/enabled flag", flush=True)
        return

    opp = list(engine.pending_opportunities.values())[0]
    sent = await bot.send_opportunity_card(opp)
    EV.add("card_sent", {"ok": bool(sent), "opportunity_id": opp["id"]})

    try:
        await asyncio.sleep(DURATION_S)
    except asyncio.CancelledError:
        pass
    finally:
        EV.add("final_snapshot", bot.evidence_snapshot())
        EV.dump()
        await bot.stop()
        remaining = list(engine.pending_opportunities.keys())
        print(f"Test window over. Decisions executed: {len(bot._decided)}; "
              f"still pending: {remaining}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
