"""Alert manager – routes alerts to appropriate notification channels.

Central hub that receives typed alerts and dispatches them to Telegram,
WebSocket clients, and log outputs based on alert type and configuration.
"""
import logging
import time
from datetime import datetime, time as dtime
from typing import Any, Callable, Dict, Optional, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# v0.4.8 P1 (noise control): quiet-hours window + per-subtype cooldowns.
# The live session produced off-hours INFO chatter (feed/VIX alerts at
# 07:57 and 15:32) and outage cascades that repeated every ~6s engine
# cycle. Rules:
#   * money events + CRITICAL severity ALWAYS reach Telegram
#   * INFO-level health chatter outside Mon-Fri 09:00-15:35 IST is logged
#     and websocketed but not messaged
#   * repeating failure subtypes are cooled down; RECOVERY subtypes never
#     are (the operator must always see the all-clear)
IST = ZoneInfo("Asia/Kolkata")

_SUBTYPE_COOLDOWN_SECONDS = {
    "feed_unresponsive": 300.0,
    "feed_frozen": 300.0,
    "vix_stale_warning": 900.0,
    "vix_critically_stale": 900.0,
}

# Mapping of alert_type -> TelegramBot method name
_TELEGRAM_METHODS = {
    "trade_fill": "send_trade_fill",
    "trade_executed": "send_trade_fill",
    "partial_booking": "send_partial_booking",
    "partial_book": "send_partial_booking",
    "stop_loss_hit": "send_sl_hit",
    "sl_hit": "send_sl_hit",
    "target_hit": "send_target_hit",
    "trade_exit": "send_trade_exit",
    "position_closed": "send_trade_exit",
    "risk_event": "send_risk_alert",
    "risk_limit_warning": "send_risk_alert",
    "risk_alert": "send_risk_alert",
    "engine_status": "send_engine_status",
    "engine_state_change": "send_engine_status",
    "error_alert": "send_error_alert",
    "error": "send_error_alert",
    "morning_briefing": "send_morning_briefing",
    "eod_report": "send_eod_report",
    # v0.4.8 HF-10: previously unmapped — feed health alerts fell through
    # to the raw str(dict) fallback, and opportunity lifecycle had no
    # Telegram presence at all.
    "feed_alert": "send_feed_alert",
    "opportunity_created": "send_opportunity_alert",
    "opportunity_expired": "send_opportunity_alert",
}

# Alert type -> config toggle key mapping
_CONFIG_TOGGLES = {
    "trade_fill": "alert_trade_executed",
    "trade_executed": "alert_trade_executed",
    "partial_booking": "alert_partial_booking",
    "partial_book": "alert_partial_booking",
    "stop_loss_hit": "alert_stop_loss",
    "sl_hit": "alert_stop_loss",
    "target_hit": "alert_target_hit",
    "trade_exit": "alert_trade_executed",
    "position_closed": "alert_trade_executed",
    "risk_event": "alert_risk_warning",
    "risk_limit_warning": "alert_risk_warning",
    "risk_alert": "alert_risk_warning",
    "engine_status": "alert_engine_status",
    "engine_state_change": "alert_engine_status",
    "error_alert": "alert_error",
    "error": "alert_error",
    "eod_report": "alert_eod_report",
    # v0.4.8 HF-10 additions (default True when absent from config)
    "feed_alert": "alert_feed_health",
    "opportunity_created": "alert_opportunities",
    "opportunity_expired": "alert_opportunities",
}


class AlertManager:
    """Route alerts to Telegram and/or WebSocket based on type and config.

    Args:
        telegram_bot: A TelegramBot instance.
        config: Notification config dict or Settings object.
        ws_manager: Optional WebSocketManager for broadcasting to frontend.
    """

    def __init__(
        self,
        telegram_bot: Any,
        config: Union[Dict[str, Any], Any],
        ws_manager: Any = None,
    ):
        self.telegram_bot = telegram_bot
        self._config = config
        self.ws_manager = ws_manager
        self._last_alert_time: Dict[str, float] = {}

    @property
    def config(self) -> Dict[str, Any]:
        if hasattr(self._config, "get_notifications_config"):
            return self._config.get_notifications_config()
        if isinstance(self._config, dict):
            return self._config
        return {}

    def is_alert_enabled(self, alert_type: str) -> bool:
        """Check if Telegram alert is enabled globally and for this specific event."""
        cfg = self.config
        telegram_enabled = bool(cfg.get("telegram_enabled", False))
        if not telegram_enabled:
            return False

        toggle_key = _CONFIG_TOGGLES.get(alert_type)
        if toggle_key:
            # Default to True if not specified
            return bool(cfg.get(toggle_key, True))
        return True

    async def route_alert(self, alert_type: str, data: Any) -> bool:
        """Route an alert to all enabled channels with rate-limiting.

        Args:
            alert_type: One of the supported alert types.
            data: Payload specific to the alert type.

        Returns:
            True if at least one channel successfully received the alert.
        """
        sent_any = False
        data_dict = data if isinstance(data, dict) else {"text": str(data)}

        # Rate limit repetitive error alerts (1 per 3 seconds per error type)
        if alert_type in ("error_alert", "error"):
            err_key = f"{alert_type}:{data_dict.get('error_type', 'generic')}"
            now = time.time()
            rate_limit = float(self.config.get("alert_rate_limit_seconds", 3.0))
            if now - self._last_alert_time.get(err_key, 0) < rate_limit:
                logger.debug("Alert rate limited for %s", err_key)
                return False
            self._last_alert_time[err_key] = now

        # v0.4.8 P1: cooldown repeating FAILURE subtypes (feed outage -> VIX
        # cascade repeated every engine cycle in the live session).
        subtype = str(data_dict.get("type", "") or "")
        if subtype and alert_type in ("feed_alert", "risk_alert", "risk_event"):
            if "recover" not in subtype.lower() and "restore" not in subtype.lower():
                cooldown = _SUBTYPE_COOLDOWN_SECONDS.get(subtype, 0.0)
                if cooldown > 0:
                    key = f"subtype:{subtype}"
                    now = time.time()
                    if now - self._last_alert_time.get(key, 0) < cooldown:
                        logger.debug("Alert subtype '%s' cooled down — suppressed.", subtype)
                        return False
                    self._last_alert_time[key] = now

        # ---- Telegram Channel (v0.4.8 P1: quiet-hours gate for non-critical
        # health chatter — WS + log channels still receive everything) ----
        telegram_ok = self.is_alert_enabled(alert_type) and self.telegram_bot is not None
        if telegram_ok and not self._is_always_delivered(alert_type, data_dict):
            if not self._within_market_hours():
                logger.info(
                    "Non-critical alert '%s' suppressed outside market hours.", alert_type
                )
                telegram_ok = False
        if telegram_ok:
            try:
                sent_telegram = await self._send_telegram(alert_type, data)
                if sent_telegram:
                    sent_any = True
            except Exception as tg_err:
                logger.error("Failed to send Telegram alert '%s': %s", alert_type, tg_err)

        # ---- WebSocket Channel (best-effort) ----
        if self.ws_manager is not None:
            try:
                sent_ws = await self._send_websocket(alert_type, data_dict)
                if sent_ws:
                    sent_any = True
            except Exception as ws_err:
                logger.debug("WebSocket broadcast failed for '%s': %s", alert_type, ws_err)

        # ---- Log Channel ----
        self._log_alert(alert_type, data_dict)

        return sent_any

    # ------------------------------------------------------------------
    # Internal dispatchers
    # ------------------------------------------------------------------

    async def _send_telegram(self, alert_type: str, data: Any) -> bool:
        """Dispatch to the appropriate TelegramBot method."""
        if self.telegram_bot is None:
            return False

        # Refresh credentials from config if bot token was updated
        cfg = self.config
        cfg_token = cfg.get("telegram_bot_token")
        cfg_chat_id = cfg.get("telegram_chat_id")
        if cfg_token and (cfg_token != self.telegram_bot.bot_token or str(cfg_chat_id or "") != self.telegram_bot.chat_id):
            self.telegram_bot.update_credentials(cfg_token, str(cfg_chat_id or ""))

        data_dict = data if isinstance(data, dict) else {"text": str(data)}

        # 1. Trade executed
        if alert_type in ("trade_fill", "trade_executed"):
            return await self.telegram_bot.send_trade_fill(data)

        # 2. Partial booking
        elif alert_type in ("partial_booking", "partial_book"):
            return await self.telegram_bot.send_partial_booking(data)

        # 3. Stop loss hit
        elif alert_type in ("stop_loss_hit", "sl_hit"):
            return await self.telegram_bot.send_sl_hit(data)

        # 4. Target hit
        elif alert_type == "target_hit":
            return await self.telegram_bot.send_target_hit(data)

        # 5. Generic trade exit / position closed (dispatches to SL / Target / generic)
        elif alert_type in ("trade_exit", "position_closed"):
            # v0.4.8 HF-7: classify through the shared taxonomy instead of
            # substring matching ("time_stop" contains "stop" and used to
            # render through the STOP LOSS template).
            from utils.exit_taxonomy import classify_exit, exit_alert_kind, EXIT_LABELS

            exit_class = classify_exit(
                close_reason=str(data_dict.get("close_reason") or data_dict.get("reason") or ""),
                direction=str(data_dict.get("direction", "")),
                entry_price=float(data_dict.get("entry_price", 0) or 0),
                exit_price=float(data_dict.get("exit_price", 0) or 0),
                stop_loss=data_dict.get("stop_loss"),
            )
            kind = exit_alert_kind(exit_class)
            if kind == "target_hit":
                return await self.telegram_bot.send_target_hit(data)
            elif kind == "stop_loss_hit":
                return await self.telegram_bot.send_sl_hit(data)
            else:
                sym = data_dict.get("symbol", "?")
                direction = data_dict.get("direction", "")
                pnl = float(data_dict.get("net_pnl", data_dict.get("pnl", 0.0)) or 0.0)
                exit_p = float(data_dict.get("exit_price", 0.0) or 0.0)
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                raw_reason = str(data_dict.get("close_reason") or data_dict.get("reason") or "")
                label = EXIT_LABELS.get(exit_class, raw_reason)
                msg = (
                    f"🔒 <b>POSITION CLOSED – {label.upper()}</b>\n\n"
                    f"<b>Symbol:</b> {sym} | <b>Direction:</b> {direction}\n"
                    f"<b>Exit Price:</b> ₹{exit_p:.2f}\n"
                    f"{pnl_emoji} <b>Net P&amp;L:</b> ₹{pnl:+.2f}\n"
                    f"<b>Reason:</b> {raw_reason}"
                )
                return await self.telegram_bot.send_message(msg)

        # 6. Risk warning
        elif alert_type in ("risk_event", "risk_limit_warning", "risk_alert"):
            return await self.telegram_bot.send_risk_alert(data)

        # 7. Engine status change
        elif alert_type in ("engine_status", "engine_state_change"):
            state = data_dict.get("state") or data_dict.get("status") or "UNKNOWN"
            mode = data_dict.get("mode", "")
            broker = data_dict.get("broker", "")
            details = data_dict.get("details") or data_dict.get("message") or ""
            return await self.telegram_bot.send_engine_status(state=state, mode=mode, broker=broker, details=details)

        # 8. Error alert
        elif alert_type in ("error_alert", "error"):
            return await self.telegram_bot.send_error_alert(data)

        # 9. Morning briefing
        elif alert_type == "morning_briefing":
            watchlist = data_dict.get("watchlist", [])
            regime = data_dict.get("regime", "Unknown")
            vix = float(data_dict.get("vix", 0.0))
            return await self.telegram_bot.send_morning_briefing(watchlist, regime, vix)

        # 10. EOD Report
        elif alert_type == "eod_report":
            summary = data_dict.get("daily_summary", data_dict)
            trades = data_dict.get("trades", [])
            return await self.telegram_bot.send_eod_report(summary, trades)

        # 11. Feed health (v0.4.8 HF-10: previously fell through to the raw
        # str(dict) fallback — operators received python dict dumps).
        elif alert_type == "feed_alert":
            return await self.telegram_bot.send_feed_alert(data)

        # 12. Opportunity lifecycle (v0.4.8 P1: human-in-the-loop pings)
        elif alert_type in ("opportunity_created", "opportunity_expired"):
            return await self.telegram_bot.send_opportunity_alert(alert_type, data)

        else:
            text = data_dict.get("text", str(data))
            return await self.telegram_bot.send_message(text)

    # ------------------------------------------------------------------
    # Noise-control helpers (v0.4.8 P1)
    # ------------------------------------------------------------------

    # Alert types that ALWAYS reach Telegram regardless of market hours:
    # anything money- or lifecycle-related, plus errors and risk events.
    _ALWAYS_DELIVERED = {
        "trade_fill", "trade_executed", "partial_booking", "partial_book",
        "stop_loss_hit", "sl_hit", "target_hit", "trade_exit",
        "position_closed", "eod_report", "morning_briefing",
        "opportunity_created", "opportunity_expired",
        "error_alert", "error", "risk_event", "risk_limit_warning",
        "engine_status", "engine_state_change",
    }

    def _is_always_delivered(self, alert_type: str, data_dict: dict) -> bool:
        """True for money/lifecycle alerts and anything CRITICAL severity."""
        if alert_type in self._ALWAYS_DELIVERED:
            return True
        severity = str(data_dict.get("severity", "") or "").upper()
        return severity in ("CRITICAL", "HIGH")

    @staticmethod
    def _within_market_hours() -> bool:
        """True inside NSE regular hours (Mon-Fri 09:00-15:35 IST).

        Deliberately self-contained (no MarketHours import): the quiet-hours
        gate only needs a coarse window to stop INFO-level feed/VIX chatter
        at 07:57 or 15:32 from buzzing the operator's phone.
        """
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return dtime(9, 0) <= now.time() <= dtime(15, 35)

    async def _send_websocket(self, alert_type: str, data: dict) -> bool:
        """Broadcast alert payload to all connected WebSocket clients."""
        try:
            payload = {
                "type": alert_type,
                "data": data,
            }
            await self.ws_manager.broadcast(alert_type, payload)
            return True
        except Exception as exc:
            logger.debug("WebSocket broadcast failed for '%s': %s", alert_type, exc)
            return False

    @staticmethod
    def _log_alert(alert_type: str, data: dict) -> None:
        """Write alert to the application logger."""
        if alert_type in ("error_alert", "error", "risk_event", "risk_limit_warning"):
            logger.warning("[%s] %s", alert_type, data)
        else:
            logger.info("[%s] %s", alert_type, data)
