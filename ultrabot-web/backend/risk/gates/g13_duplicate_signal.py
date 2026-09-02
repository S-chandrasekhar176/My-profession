import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from models.risk_state import GateResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from db.repository import Repository


class G13DuplicateSignal:
    """Block duplicate signals for the same symbol+direction within a time window."""

    LOOKBACK_MINUTES = 15

    def __init__(self, config: Dict[str, Any]):
        self.lookback_minutes: int = int(config.get("duplicate_signal_lookback_minutes", 15))
        self.repository: Optional["Repository"] = None

    def set_repository(self, repo: "Repository") -> None:
        """Inject the repository for database lookups."""
        self.repository = repo

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        if self.repository is None:
            return GateResult(
                gate_name="G13_DuplicateSignal",
                passed=True,
                message="Repository not set, duplicate check skipped",
                severity="info",
            )

        repo = None
        should_close = False
        try:
            raw_repo = self.repository
            if callable(raw_repo):
                should_close = True
                if asyncio.iscoroutinefunction(raw_repo):
                    repo = await raw_repo()
                else:
                    repo = raw_repo()
            else:
                repo = raw_repo

            direction = getattr(signal, "direction", "LONG").upper()
            symbol = getattr(signal, "symbol", "").upper()
            recent_signals = await repo.get_signals_by_symbol(symbol, limit=50)
            cutoff = datetime.now() - timedelta(minutes=self.lookback_minutes)

            duplicate_found = False
            for sig in recent_signals:
                if getattr(sig, "direction", "").upper() != direction:
                    continue
                try:
                    sig_time = datetime.fromisoformat(sig.created_at)
                    if sig_time >= cutoff:
                        duplicate_found = True
                        break
                except (ValueError, TypeError, AttributeError):
                    continue

            if duplicate_found:
                return GateResult(
                    gate_name="G13_DuplicateSignal",
                    passed=False,
                    message=(
                        f"Duplicate {direction} signal for {symbol} within "
                        f"last {self.lookback_minutes} minutes"
                    ),
                    severity="warning",
                )

            return GateResult(
                gate_name="G13_DuplicateSignal",
                passed=True,
                message=(
                    f"No duplicate {direction} signal for {symbol} within "
                    f"last {self.lookback_minutes} minutes"
                ),
                severity="info",
            )
        except Exception as exc:
            logger.warning("G13 DuplicateSignal check encountered error for %s (%s): %s", symbol, direction, exc)
            return GateResult(
                gate_name="G13_DuplicateSignal",
                passed=True,
                message=f"Duplicate check error, allowing trade: {exc}",
                severity="info",
            )
        finally:
            if should_close and repo is not None and hasattr(repo, "close"):
                try:
                    await repo.close()
                except Exception:
                    pass
