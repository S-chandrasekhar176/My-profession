"""Crash-aware engine auto-resume (HOTFIX #8, live 2026-09-01).

Resilience drill finding (13:06 IST kill test): a process kill leaves the
same-day session record in status="running", and on reboot the engine sits
in "stopped" until a human manually starts it — with stop-losses unenforced
the whole time. A graceful stop writes status="stopped" and a completed day
writes "completed", so status="running" on boot is a reliable crash signal.

auto_resume_if_crashed() restores operation automatically ONLY in that case,
preserving user intent (explicitly stopped / completed sessions are never
auto-resumed).
"""
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def auto_resume_if_crashed(
    engine: Any,
    settle_delay: float = 5.0,
) -> Optional[str]:
    """Restart the engine if a same-day session was left 'running' by a crash.

    Returns the resumed session_id, or None when no resume happened.
    Never raises — all failures are logged so app startup stays clean.
    """
    try:
        # Let the feed manager, scheduler and DB pool settle before starting.
        await asyncio.sleep(settle_delay)
        session_manager = getattr(engine, "session_manager", None)
        if session_manager is None:
            logger.warning("[auto-resume] engine has no session_manager — skipping")
            return None

        sess = await session_manager.get_same_day_session()
        if not sess or sess.get("status") != "running":
            return None

        mode = str(sess.get("mode") or "").lower()
        broker = str(sess.get("broker") or "").lower()
        if not mode or not broker or mode == "unknown" or broker == "unknown":
            logger.warning(
                "[auto-resume] same-day session %s is 'running' but mode/broker "
                "unknown — NOT auto-resuming (manual start required)",
                sess.get("session_id"),
            )
            return None

        logger.warning(
            "[auto-resume] same-day session %s left in 'running' state by a "
            "crash/kill — auto-resuming engine (mode=%s, broker=%s)",
            sess.get("session_id"), mode, broker,
        )
        result = await engine.start(mode=mode, broker_name=broker)
        session_id = sess.get("session_id")
        logger.info("[auto-resume] engine restarted for session %s: %s", session_id, result)
        return session_id
    except Exception as exc:  # never break app startup
        logger.error("[auto-resume] failed: %s", exc, exc_info=True)
        return None
