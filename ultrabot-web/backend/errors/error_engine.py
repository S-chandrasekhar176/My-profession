"""ErrorEngine – singleton that handles all errors.

Workflow:
1. Captures error context
2. Generates error code: ERR-YYYY-MMDD-NNNN
3. Determines severity
4. Attempts auto-recovery
5. Saves to DB
6. Broadcasts via WebSocket (if available)
7. Sends Telegram notification (if configured)
"""
import asyncio
import logging
import traceback
from datetime import datetime, date
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import threading
from errors.error_types import UltraBotError, BrokerError, FeedError
from errors.error_context import capture_context
from errors.auto_recovery import AutoRecovery

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class ErrorEngine:
    """Singleton error engine for UltraBot."""

    _instance: Optional["ErrorEngine"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ErrorEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._auto_recovery = AutoRecovery()
        self._counter: Dict[str, int] = {}  # date -> count
        self._ws_callback: Optional[Callable] = None
        self._telegram_callback: Optional[Callable] = None
        self._db_session_getter: Optional[Callable] = None

    # ────────────────────────────────────────
    # Configuration
    # ────────────────────────────────────────

    def set_ws_callback(self, callback: Callable) -> None:
        """Set a callback for broadcasting errors via WebSocket."""
        self._ws_callback = callback

    def set_telegram_callback(self, callback: Callable) -> None:
        """Set a callback for sending errors via Telegram."""
        self._telegram_callback = callback

    def set_db_session_getter(self, getter: Callable) -> None:
        """Set an async callable that returns a DB session."""
        self._db_session_getter = getter

    @property
    def auto_recovery(self) -> AutoRecovery:
        return self._auto_recovery

    # ────────────────────────────────────────
    # Error code generation
    # ────────────────────────────────────────

    def _generate_error_code(self) -> str:
        """Generate a unique error code: ERR-YYYY-MMDD-NNNN."""
        today = datetime.now(IST).date().isoformat()  # YYYY-MM-DD
        key = today.replace("-", "")  # YYYYMMDD
        count = self._counter.get(key, 0) + 1
        self._counter[key] = count
        return f"ERR-{today}-{count:04d}"

    # ────────────────────────────────────────
    # Severity determination
    # ────────────────────────────────────────

    def _determine_severity(self, error: UltraBotError) -> str:
        """Determine severity, potentially overriding the error's own."""
        # Trust the error's own severity unless it seems wrong
        if error.severity in ("info", "warning", "error", "critical"):
            return error.severity
        return "error"

    # ────────────────────────────────────────
    # Main error handler
    # ────────────────────────────────────────

    async def handle_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        **recovery_kwargs: Any,
    ) -> Dict[str, Any]:
        """Handle an error through the full pipeline.

        Args:
            error: The exception (should be UltraBotError or subclass).
            context: Additional context dict (merged with auto-captured).
            session_id: Current trading session ID.
            **recovery_kwargs: Passed to AutoRecovery.recover().

        Returns:
            dict with error_code, severity, recovery_result, saved_to_db.
        """
        # Normalize to UltraBotError if it's a raw exception
        if not isinstance(error, UltraBotError):
            ultra_error = UltraBotError(
                what_happened=str(error),
                why_happened="Unhandled exception type",
                how_to_fix="Review stack trace and add proper error handling",
            )
            ultra_error.error_type = type(error).__name__
        else:
            ultra_error = error

        # 1. Capture context
        full_context = capture_context(**(context or {}))
        full_context["session_id"] = session_id
        if isinstance(error, UltraBotError):
            full_context.update(error.context)

        # 2. Generate code
        error_code = self._generate_error_code()
        severity = self._determine_severity(ultra_error)

        # 3. Get full stack trace
        stack_trace = traceback.format_exc()

        # 4. Attempt auto-recovery
        recovery_result: Dict[str, Any] = {"success": False, "message": "No recovery strategy"}
        if isinstance(ultra_error, UltraBotError):
            try:
                recovery_result = await self._auto_recovery.recover(ultra_error, **recovery_kwargs)
            except Exception as rec_exc:
                logger.error(f"Auto-recovery itself threw: {rec_exc}")
                recovery_result = {
                    "success": False,
                    "message": f"Recovery threw: {rec_exc}",
                    "action": "manual_intervention",
                }

        # 5. Save to DB
        saved = False
        if self._db_session_getter is not None:
            repo = None
            try:
                from db.repository import Repository
                db_obj = await self._db_session_getter()
                repo = db_obj if isinstance(db_obj, Repository) else Repository(db_obj)
                await repo.create_error_log(
                    error_code=error_code,
                    error_type=ultra_error.error_type,
                    severity=severity,
                    what_happened=ultra_error.what_happened,
                    why_happened=ultra_error.why_happened,
                    how_to_fix=ultra_error.how_to_fix,
                    context=full_context,
                    stack_trace=stack_trace,
                    auto_recovery_attempted=True,
                    auto_recovery_result=str(recovery_result),
                    session_id=session_id,
                )
                saved = True
            except Exception as db_exc:
                logger.error(f"Failed to save error to DB: {db_exc}")
                saved = False
            finally:
                if repo is not None and hasattr(repo, "close"):
                    try:
                        await repo.close()
                    except Exception:
                        pass

        # 6. Broadcast via WebSocket
        if self._ws_callback is not None:
            try:
                ws_payload = {
                    "type": "error",
                    "error_code": error_code,
                    "error_type": ultra_error.error_type,
                    "severity": severity,
                    "what_happened": ultra_error.what_happened,
                    "recovery": recovery_result,
                    "timestamp": datetime.now(IST).isoformat(),
                }
                if asyncio.iscoroutinefunction(self._ws_callback):
                    await self._ws_callback(ws_payload)
                else:
                    self._ws_callback(ws_payload)
            except Exception as ws_exc:
                logger.error(f"Failed to broadcast error via WS: {ws_exc}")

        # 7. Send Telegram notification (critical/warning only)
        if severity in ("critical", "warning") and self._telegram_callback is not None:
            try:
                msg = (
                    f"⚠️ <b>{ultra_error.error_type}</b> [{severity.upper()}]\n"
                    f"Code: {error_code}\n"
                    f"{ultra_error.what_happened}\n"
                    f"Recovery: {recovery_result.get('action', 'none')}"
                )
                if asyncio.iscoroutinefunction(self._telegram_callback):
                    await self._telegram_callback(msg)
                else:
                    self._telegram_callback(msg)
            except Exception as tg_exc:
                logger.error(f"Failed to send Telegram notification: {tg_exc}")

        # Log appropriately
        log_fn = {
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }.get(severity, logger.error)
        log_fn(f"[{error_code}] {ultra_error.error_type}: {ultra_error.what_happened}")

        return {
            "error_code": error_code,
            "severity": severity,
            "recovery_result": recovery_result,
            "saved_to_db": saved,
        }

    # ────────────────────────────────────────
    # Resolve error
    # ────────────────────────────────────────

    async def resolve_error(self, error_id: str, resolution_note: str = "") -> bool:
        """Mark an error as resolved in the DB."""
        if self._db_session_getter is None:
            logger.warning("No DB session getter set, cannot resolve error")
            return False
        repo = None
        try:
            from db.repository import Repository
            db_obj = await self._db_session_getter()
            repo = db_obj if isinstance(db_obj, Repository) else Repository(db_obj)
            result = await repo.resolve_error(error_id, resolution_note=resolution_note)
            if result:
                logger.info(f"Error {error_id} resolved: {resolution_note}")
            return result is not None
        except Exception as e:
            logger.error(f"Failed to resolve error {error_id}: {e}")
            return False
        finally:
            if repo is not None and hasattr(repo, "close"):
                try:
                    await repo.close()
                except Exception:
                    pass

    # ────────────────────────────────────────
    # Get errors
    # ────────────────────────────────────────

    async def get_errors(
        self,
        resolved: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get error logs from DB."""
        if self._db_session_getter is None:
            return []
        repo = None
        try:
            from db.repository import Repository
            db_obj = await self._db_session_getter()
            repo = db_obj if isinstance(db_obj, Repository) else Repository(db_obj)
            errors = await repo.get_errors(resolved=resolved, limit=limit, offset=offset)
            import json
            results = []
            for e in errors:
                d = {
                    "id": e.id,
                    "error_code": e.error_code,
                    "error_type": e.error_type,
                    "severity": e.severity,
                    "what_happened": e.what_happened,
                    "why_happened": e.why_happened,
                    "how_to_fix": e.how_to_fix,
                    "context": json.loads(e.context) if isinstance(e.context, str) else e.context,
                    "stack_trace": e.stack_trace,
                    "is_resolved": e.is_resolved,
                    "resolved_at": e.resolved_at,
                    "resolution_note": e.resolution_note,
                    "auto_recovery_attempted": e.auto_recovery_attempted,
                    "auto_recovery_result": e.auto_recovery_result,
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
                results.append(d)
            return results
        except Exception as e:
            logger.error(f"Failed to get errors: {e}")
            return []
        finally:
            if repo is not None and hasattr(repo, "close"):
                try:
                    await repo.close()
                except Exception:
                    pass

    # ────────────────────────────────────────
    # Get error stats
    # ────────────────────────────────────────

    async def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics from DB."""
        if self._db_session_getter is None:
            return {"total_errors": 0, "unresolved": 0, "today_count": 0, "critical_unresolved": 0, "by_type": {}}
        repo = None
        try:
            from db.repository import Repository
            db_obj = await self._db_session_getter()
            repo = db_obj if isinstance(db_obj, Repository) else Repository(db_obj)
            stats = await repo.get_error_stats()
            return stats
        except Exception as e:
            logger.error(f"Failed to get error stats: {e}")
            return {"total_errors": 0, "unresolved": 0, "today_count": 0, "critical_unresolved": 0, "by_type": {}}
        finally:
            if repo is not None and hasattr(repo, "close"):
                try:
                    await repo.close()
                except Exception:
                    pass


# Global singleton
error_engine = ErrorEngine()
