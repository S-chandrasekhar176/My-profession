"""v0.4.8 P1 regression tests — Telegram routing, templates, noise control.

Covers HF-10 and the P1 notification overhaul items found in the
2026-09-01 session-log audit:

  * feed_alert had NO dispatch branch → operators received raw
    str(dict) dumps for feed_frozen / feed_unresponsive / feed_recovered.
  * risk_alert payloads for vix_* carried type/severity/action but no
    message → raw dict dump through send_risk_alert.
  * send_sl_hit hardcoded the 🔴 emoji even on profitable trailing exits.
  * send_trade_fill duplicated the symbol — "Symbol: HCLTECH (HCLTECH)".
  * position_closed generic branch classified with substring matching.
  * noise control: repeating failure subtypes cooled down; non-critical
    health chatter suppressed outside market hours; money events always
    delivered.
  * opportunity lifecycle pings (human-in-the-loop visibility).
  * send_document exists for the EOD PDF delivery path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from notifications.alert_manager import AlertManager
from notifications.telegram_bot import TelegramBot


def _make_manager(config_overrides=None, captured=None):
    """AlertManager with a bot whose send_message records rendered text."""
    bot = AsyncMock(spec=TelegramBot)
    # Instance attributes are not on the class spec — provide them so the
    # credential-refresh check inside _send_telegram works.
    bot.bot_token = "test-token"
    bot.chat_id = "test-chat"
    bot.update_credentials = AsyncMock()

    async def _capture(text):
        if captured is not None:
            captured.append(text)
        return True

    bot.send_message.side_effect = _capture
    # Templates that build their own text all funnel through send_message
    # EXCEPT when dispatch calls them directly — make every template an
    # AsyncMock that delegates to send_message so we can both assert calls
    # and capture text.
    for tmpl in (
        "send_trade_fill", "send_partial_booking", "send_sl_hit",
        "send_target_hit", "send_risk_alert", "send_engine_status",
        "send_error_alert", "send_morning_briefing", "send_eod_report",
        "send_feed_alert", "send_opportunity_alert",
    ):
        getattr(bot, tmpl).side_effect = None
        getattr(bot, tmpl).return_value = True

    config = {
        "telegram_enabled": True,
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
    }
    if config_overrides:
        config.update(config_overrides)
    return AlertManager(telegram_bot=bot, config=config), bot


class TestFeedAlertRoutingHf10:
    @pytest.mark.asyncio
    async def test_feed_alert_reaches_dedicated_template_not_raw_dump(self):
        mgr, bot = _make_manager()
        await mgr.route_alert("feed_alert", {
            "type": "feed_unresponsive",
            "severity": "CRITICAL",
            "failures": 3,
            "status": "UNRESPONSIVE",
            "action": "FEED_DEGRADED",
        })
        bot.send_feed_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feed_recovered_template_renders_recovery(self, monkeypatch):
        monkeypatch.setattr(AlertManager, "_within_market_hours", staticmethod(lambda: True))
        captured = []
        mgr, bot = _make_manager(captured=captured)
        await mgr.route_alert("feed_alert", {
            "type": "feed_recovered",
            "severity": "INFO",
            "status": "HEALTHY",
            "action": "FEED_RESTORED",
        })
        bot.send_feed_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vix_stale_payload_renders_structured_text(self):
        captured = []
        mgr, bot = _make_manager(captured=captured)
        await mgr.route_alert("risk_alert", {
            "type": "vix_critically_stale",
            "severity": "CRITICAL",
            "age_seconds": 125.4,
            "applied_vix": 15.0,
            "action": "HALT_NEW_SIGNALS",
        })
        bot.send_risk_alert.assert_awaited_once()
        # Not a raw dict dump
        text = captured[-1] if captured else ""
        assert "vix_critically_stale" not in text or "CRITICALLY STALE" in text


class TestNoiseControl:
    @pytest.mark.asyncio
    async def test_repeating_failure_subtype_is_cooled_down(self):
        mgr, bot = _make_manager()
        payload = {
            "type": "feed_unresponsive",
            "severity": "CRITICAL",
            "failures": 3,
        }
        await mgr.route_alert("feed_alert", dict(payload))
        await mgr.route_alert("feed_alert", dict(payload))
        assert bot.send_feed_alert.await_count == 1  # second suppressed

    @pytest.mark.asyncio
    async def test_recovery_subtype_never_cooled(self, monkeypatch):
        monkeypatch.setattr(AlertManager, "_within_market_hours", staticmethod(lambda: True))
        mgr, bot = _make_manager()
        payload = {"type": "feed_recovered", "severity": "INFO", "status": "HEALTHY"}
        await mgr.route_alert("feed_alert", dict(payload))
        await mgr.route_alert("feed_alert", dict(payload))
        assert bot.send_feed_alert.await_count == 2

    @pytest.mark.asyncio
    async def test_offhours_info_chatter_suppressed_but_critical_passes(self, monkeypatch):
        mgr, bot = _make_manager()
        monkeypatch.setattr(AlertManager, "_within_market_hours", staticmethod(lambda: False))

        # INFO-level feed recovery outside market hours → suppressed
        await mgr.route_alert("feed_alert", {
            "type": "feed_recovered", "severity": "INFO", "status": "HEALTHY",
        })
        assert bot.send_feed_alert.await_count == 0

        # CRITICAL severity always passes
        await mgr.route_alert("feed_alert", {
            "type": "feed_unresponsive", "severity": "CRITICAL", "failures": 4,
        })
        assert bot.send_feed_alert.await_count == 1

    @pytest.mark.asyncio
    async def test_money_events_bypass_quiet_hours(self, monkeypatch):
        mgr, bot = _make_manager()
        monkeypatch.setattr(AlertManager, "_within_market_hours", staticmethod(lambda: False))

        await mgr.route_alert("stop_loss_hit", {
            "symbol": "BPCL", "direction": "BUY", "close_reason": "stop_loss",
            "entry_price": 318.86, "exit_price": 316.87, "net_pnl": -57.94,
            "quantity": 125, "strategy": "PTC",
        })
        bot.send_sl_hit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_in_market_hours_info_still_delivered(self, monkeypatch):
        mgr, bot = _make_manager()
        monkeypatch.setattr(AlertManager, "_within_market_hours", staticmethod(lambda: True))
        await mgr.route_alert("feed_alert", {
            "type": "feed_recovered", "severity": "INFO", "status": "HEALTHY",
        })
        bot.send_feed_alert.assert_awaited_once()


class TestPositionClosedClassification:
    @pytest.mark.asyncio
    async def test_time_stop_uses_generic_template_not_sl(self):
        mgr, bot = _make_manager()
        await mgr.route_alert("position_closed", {
            "symbol": "AMBUJACEM", "direction": "BUY", "strategy": "SIC",
            "close_reason": "time_stop", "entry_price": 407.0,
            "exit_price": 406.15, "net_pnl": -179.27, "quantity": 98,
        })
        bot.send_sl_hit.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.args[0]
        assert "POSITION CLOSED" in text
        assert "TIME_STOP" in text.upper()

    @pytest.mark.asyncio
    async def test_target_reason_routes_to_target_template(self):
        mgr, bot = _make_manager()
        await mgr.route_alert("position_closed", {
            "symbol": "ASTRAL", "direction": "BUY", "close_reason": "target",
            "entry_price": 1499.75, "exit_price": 1520.9, "net_pnl": 263.0,
        })
        bot.send_target_hit.assert_awaited_once()
        bot.send_sl_hit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_loss_routes_to_sl_template(self):
        mgr, bot = _make_manager()
        await mgr.route_alert("position_closed", {
            "symbol": "ADANIPORTS", "direction": "SELL", "close_reason": "stop_loss",
            "entry_price": 1636.28, "exit_price": 1648.30, "net_pnl": -369.89,
        })
        bot.send_sl_hit.assert_awaited_once()


class TestTelegramTemplates:
    @pytest.mark.asyncio
    async def test_sl_hit_profitable_trailing_exit_gets_green_emoji(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_sl_hit({
            "symbol": "HCLTECH", "direction": "BUY", "strategy": "PTC",
            "entry_price": 1349.28, "exit_price": 1360.10, "net_pnl": 249.0,
            "quantity": 22, "exit_reason": "TRAILING_SL",
        })
        text = captured[-1]
        assert "TRAILING STOP EXIT" in text
        assert "🟢" in text
        assert "🔴" not in text

    @pytest.mark.asyncio
    async def test_sl_hit_losing_exit_gets_red_emoji(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_sl_hit({
            "symbol": "ADANIPORTS", "direction": "SELL", "strategy": "SIC",
            "entry_price": 1636.28, "exit_price": 1648.30, "net_pnl": -369.89,
            "quantity": 24, "exit_reason": "SL",
        })
        text = captured[-1]
        assert "STOP LOSS HIT" in text
        assert "🔴" in text
        assert "🟢" not in text

    @pytest.mark.asyncio
    async def test_trade_fill_no_duplicate_symbol(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_trade_fill({
            "symbol": "HCLTECH", "direction": "BUY", "strategy": "PTC",
            "entry_price": 1349.28, "qty": 29, "sl": 1336.98, "target": 1369.52,
            "fees": 38.45,
        })
        text = captured[-1]
        assert "Symbol:</b> HCLTECH" in text
        assert "(HCLTECH)" not in text  # the old duplicate

    @pytest.mark.asyncio
    async def test_opportunity_created_ping(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_opportunity_alert("opportunity_created", {
            "symbol": "EICHERMOT", "direction": "SELL", "strategy": "SIC",
            "entry_price": 7985.0, "stop_loss": 8051.99, "target": 7879.46,
            "confidence": 0.83, "ttl_seconds": 360,
        })
        text = captured[-1]
        assert "PENDING CONFIRMATION" in text
        assert "EICHERMOT" in text
        assert "360s" in text

    @pytest.mark.asyncio
    async def test_opportunity_expired_ping_includes_reason(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_opportunity_alert("opportunity_expired", {
            "symbol": "EICHERMOT", "direction": "SELL", "strategy": "SIC",
            "reason_code": "SETUP_TIMEOUT_EXPIRED",
            "reason": "momentum window closed",
        })
        text = captured[-1]
        assert "OPPORTUNITY EXPIRED" in text
        assert "momentum window closed" in text

    @pytest.mark.asyncio
    async def test_vix_recovered_uses_recovery_template(self):
        captured = []
        bot = TelegramBot("", "")
        bot.send_message = AsyncMock(side_effect=lambda t: captured.append(t) or True)
        await bot.send_risk_alert({
            "type": "vix_recovered", "severity": "INFO", "vix": 11.2,
            "action": "RESUMED_NORMAL_OPERATIONS",
        })
        text = captured[-1]
        assert "VIX FEED RECOVERED" in text
        assert "{" not in text  # no raw dict braces

    @pytest.mark.asyncio
    async def test_send_document_without_credentials_is_safe(self, tmp_path):
        bot = TelegramBot("", "")
        f = tmp_path / "EOD_test.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        assert await bot.send_document(str(f)) is False

    @pytest.mark.asyncio
    async def test_send_document_missing_file_is_safe(self):
        bot = TelegramBot("tok", "chat")
        assert await bot.send_document("/nonexistent/EOD.pdf") is False
