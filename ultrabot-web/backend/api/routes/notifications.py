import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from api.dependencies import get_current_user
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# In-memory notification history (recent notifications sent via WebSocket/Telegram)
_notification_history: List[Dict[str, Any]] = []
_MAX_HISTORY = 200


def _add_notification_to_history(
    event_type: str,
    message: str,
    severity: str = "info",
    extra: Optional[Dict] = None,
) -> None:
    """Add a notification to the in-memory history."""
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")

    entry = {
        "id": f"notif-{len(_notification_history) + 1}",
        "event_type": event_type,
        "message": message,
        "severity": severity,
        "extra": extra or {},
        "created_at": datetime.now(IST).isoformat(),
    }
    _notification_history.append(entry)

    # Trim to max size
    while len(_notification_history) > _MAX_HISTORY:
        _notification_history.pop(0)


@router.get("/history")
async def get_notification_history(
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get notification history."""
    try:
        history = list(_notification_history)

        # Filter by event type
        if event_type:
            history = [n for n in history if n.get("event_type") == event_type]

        total = len(history)
        history = history[offset:offset + limit]

        return {
            "notifications": history,
            "total": total,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get notification history: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get notification history: {str(exc)}",
        )


class NotificationSettingsUpdate(BaseModel):
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    morning_briefing_time: Optional[str] = None
    eod_report_time: Optional[str] = None
    sound_enabled: Optional[bool] = None
    desktop_enabled: Optional[bool] = None
    alert_trade_executed: Optional[bool] = None
    alert_partial_booking: Optional[bool] = None
    alert_stop_loss: Optional[bool] = None
    alert_target_hit: Optional[bool] = None
    alert_risk_warning: Optional[bool] = None
    alert_engine_status: Optional[bool] = None
    alert_error: Optional[bool] = None
    alert_eod_report: Optional[bool] = None


@router.get("/settings")
async def get_notification_settings(
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get saved notification settings."""
    try:
        notif_config = settings.get_notifications_config() or {}
        return {
            "telegram_enabled": notif_config.get("telegram_enabled", False),
            "telegram_bot_token": notif_config.get("telegram_bot_token", ""),
            "telegram_chat_id": notif_config.get("telegram_chat_id", ""),
            "morning_briefing_time": str(notif_config.get("morning_briefing_time", "08:45")),
            "eod_report_time": str(notif_config.get("eod_report_time", "15:45")),
            "sound_enabled": notif_config.get("sound_enabled", True),
            "desktop_enabled": notif_config.get("desktop_enabled", True),
            "alert_trade_executed": notif_config.get("alert_trade_executed", True),
            "alert_partial_booking": notif_config.get("alert_partial_booking", True),
            "alert_stop_loss": notif_config.get("alert_stop_loss", True),
            "alert_target_hit": notif_config.get("alert_target_hit", True),
            "alert_risk_warning": notif_config.get("alert_risk_warning", True),
            "alert_engine_status": notif_config.get("alert_engine_status", False),
            "alert_error": notif_config.get("alert_error", True),
            "alert_eod_report": notif_config.get("alert_eod_report", True),
        }
    except Exception as exc:
        logger.error("Failed to get notification settings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get notification settings: {str(exc)}",
        )


@router.put("/settings")
async def update_notification_settings(
    body: NotificationSettingsUpdate,
    request: Request,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Update and persist notification settings."""
    try:
        update_data = body.model_dump(exclude_none=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

        notif_config = settings._raw_config.setdefault("notifications", {})
        notif_config.update(update_data)

        # Ensure telegram_enabled is set if credentials are provided
        if notif_config.get("telegram_bot_token") and notif_config.get("telegram_chat_id"):
            notif_config.setdefault("telegram_enabled", True)

        # Persist updated settings to defaults.yaml
        settings.save()

        # Update in-memory instances
        bot_token = str(notif_config.get("telegram_bot_token", ""))
        chat_id = str(notif_config.get("telegram_chat_id", ""))
        if hasattr(request.app.state, "telegram_bot") and request.app.state.telegram_bot is not None:
            request.app.state.telegram_bot.update_credentials(bot_token, chat_id)

        return {
            "message": "Notification settings updated successfully",
            "updated_keys": list(update_data.keys()),
            "config": notif_config,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update notification settings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update notification settings: {str(exc)}",
        )


class NotificationTestRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None


class EventTestRequest(BaseModel):
    event_type: str  # trade_executed, partial_booking, stop_loss_hit, target_hit, risk_limit_warning, engine_status_change, error_alert, eod_report


@router.post("/test")
async def send_test_notification(
    body: Optional[NotificationTestRequest] = None,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Send a test notification via Telegram Bot API."""
    import httpx

    try:
        notif_config = settings.get_notifications_config() or {}
        bot_token = (body.telegram_bot_token if body and body.telegram_bot_token else None) or notif_config.get("telegram_bot_token", "")
        chat_id = (body.telegram_chat_id if body and body.telegram_chat_id else None) or notif_config.get("telegram_chat_id", "")

        bot_token = str(bot_token).strip()
        chat_id = str(chat_id).strip()

        if not bot_token or not chat_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telegram Bot Token and Chat ID are required. Please enter both fields.",
            )

        # Send test message via Telegram Bot API using httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": (
                "🔔 <b>UltraBot Web Test Notification</b>\n\n"
                "✅ Telegram bot integration is active and working correctly!\n\n"
                f"• <b>Engine</b>: UltraBot Pro\n"
                f"• <b>Time</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
                "• <b>Status</b>: Ready for trade alerts"
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            body_json = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

            if resp.status_code == 200 and body_json.get("ok"):
                _add_notification_to_history(
                    event_type="test",
                    message="Test notification sent successfully to Telegram",
                    severity="info",
                )
                return {
                    "message": "Telegram test notification sent successfully!",
                    "telegram_message_id": body_json.get("result", {}).get("message_id"),
                }
            else:
                desc = body_json.get("description", resp.text)
                logger.warning("Telegram API error (%s): %s", resp.status_code, desc)
                if resp.status_code == 401:
                    detail_msg = f"Telegram 401 Unauthorized: Invalid Bot Token ({desc})"
                elif resp.status_code == 400:
                    detail_msg = f"Telegram 400 Bad Request: {desc} (Check your Chat ID)"
                else:
                    detail_msg = f"Telegram API error ({resp.status_code}): {desc}"

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=detail_msg,
                )

    except HTTPException:
        raise
    except httpx.RequestError as exc:
        logger.error("Network error reaching Telegram API: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Network error connecting to Telegram servers: {str(exc)}",
        )
    except Exception as exc:
        logger.error("Failed to send test notification: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send test notification: {str(exc)}",
        )


@router.post("/test-event")
async def test_specific_event_notification(
    body: EventTestRequest,
    request: Request,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Test a specific notification event template via AlertManager."""
    notif_config = settings.get_notifications_config() or {}
    bot_token = str(notif_config.get("telegram_bot_token", "")).strip()
    chat_id = str(notif_config.get("telegram_chat_id", "")).strip()

    if not bot_token or not chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram bot token and chat ID must be saved first before testing events.",
        )

    from notifications.telegram_bot import TelegramBot
    from notifications.alert_manager import AlertManager

    alert_mgr = getattr(request.app.state, "alert_manager", None)
    if alert_mgr is None:
        bot = TelegramBot(bot_token=bot_token, chat_id=chat_id)
        alert_mgr = AlertManager(telegram_bot=bot, config=settings)

    event = body.event_type.lower()
    sent = False

    if event in ("trade_executed", "trade_fill"):
        sent = await alert_mgr.route_alert("trade_fill", {
            "symbol": "RELIANCE",
            "direction": "BUY",
            "strategy": "ORB",
            "entry_price": 2850.50,
            "quantity": 50,
            "stop_loss": 2820.00,
            "target": 2910.00,
            "fees": 45.20,
        })
    elif event in ("partial_booking", "partial_book"):
        sent = await alert_mgr.route_alert("partial_booking", {
            "symbol": "TCS",
            "direction": "BUY",
            "entry_price": 3950.00,
            "booked_price": 4020.00,
            "booked_qty": 25,
            "remaining_qty": 25,
            "stage_name": "T1 (1:1.5 RR)",
            "pnl": 1750.00,
        })
    elif event in ("stop_loss_hit", "sl_hit"):
        sent = await alert_mgr.route_alert("stop_loss_hit", {
            "symbol": "INFY",
            "direction": "BUY",
            "strategy": "MRF",
            "entry_price": 1820.00,
            "exit_price": 1795.00,
            "quantity": 40,
            "pnl": -1000.00,
            "net_pnl": -1045.00,
            "pnl_pct": -1.37,
        })
    elif event in ("target_hit", "target"):
        sent = await alert_mgr.route_alert("target_hit", {
            "symbol": "HDFCBANK",
            "direction": "BUY",
            "strategy": "VC",
            "entry_price": 1650.00,
            "exit_price": 1700.00,
            "target": 1700.00,
            "quantity": 60,
            "pnl": 3000.00,
            "net_pnl": 2940.00,
            "pnl_pct": 3.03,
        })
    elif event in ("risk_limit_warning", "risk_event", "risk_warning"):
        sent = await alert_mgr.route_alert("risk_event", {
            "message": "Daily maximum drawdown threshold reached (2.5% of total capital). New trade entries paused.",
            "rule": "MAX_DAILY_DRAWDOWN_LIMIT",
        })
    elif event in ("engine_status_change", "engine_status"):
        sent = await alert_mgr.route_alert("engine_status", {
            "state": "running",
            "mode": "paper",
            "broker": "yahoofinance",
            "details": "Engine initialized with 4 active strategies (Sideways regime).",
        })
    elif event in ("error_alert", "error"):
        sent = await alert_mgr.route_alert("error_alert", {
            "error_type": "BrokerOrderTimeoutError",
            "severity": "critical",
            "error_code": "ERR-2026-0821-0001",
            "what_happened": "Broker order acknowledgement timed out after 5000ms",
            "why_happened": "High latency on broker order gateway",
            "how_to_fix": "Auto-recovery re-polling order status from broker",
            "context": {"broker": "Angel One", "symbol": "NIFTY26AUG24500CE"},
        })
    elif event in ("eod_report", "eod"):
        sent = await alert_mgr.route_alert("eod_report", {
            "daily_summary": {
                "date": datetime.now().strftime("%d-%b-%Y"),
                "net_pnl": 5820.50,
                "gross_pnl": 6100.00,
                "total_fees": 279.50,
                "total_trades": 6,
                "wins": 4,
                "losses": 2,
                "win_rate": 66.7,
                "best_trade": 2940.00,
                "worst_trade": -1045.00,
            },
            "trades": [
                {"symbol": "HDFCBANK", "direction": "BUY", "strategy": "VC", "net_pnl": 2940.00, "status": "CLOSED"},
                {"symbol": "TCS", "direction": "BUY", "strategy": "ORB", "net_pnl": 1750.00, "status": "CLOSED"},
                {"symbol": "INFY", "direction": "BUY", "strategy": "MRF", "net_pnl": -1045.00, "status": "CLOSED"},
                {"symbol": "RELIANCE", "direction": "BUY", "strategy": "ORB", "net_pnl": 2175.50, "status": "CLOSED"},
            ],
        })
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown event type '{event}'. Supported: trade_executed, partial_booking, stop_loss_hit, target_hit, risk_limit_warning, engine_status_change, error_alert, eod_report",
        )

    return {
        "event_type": event,
        "sent_to_telegram": sent,
        "message": f"Event alert '{event}' dispatched successfully.",
    }
