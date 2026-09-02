"""Gate G8: Time-of-Day Filter.

Only allows new trades within the configured intraday window.
Defaults to 09:30-15:15 IST (NSE opens 09:15; the first 15 minutes are
intentionally reserved for the opening range to form — ORB entries
begin at 09:35 by design).
"""
from datetime import datetime, time
from typing import Any, Dict
from zoneinfo import ZoneInfo

from models.risk_state import GateResult

IST = ZoneInfo("Asia/Kolkata")


class G8TimeOfDay:
    """Allow new trades only within the configured time window."""

    def __init__(self, config: Dict[str, Any]):
        self.window_start: time = self._parse_time(
            config.get("new_trade_window_start", "09:30")
        )
        self.window_end: time = self._parse_time(
            config.get("new_trade_window_end", "15:15")
        )

    @staticmethod
    def _parse_time(t_str: str) -> time:
        try:
            return time.fromisoformat(t_str)
        except (ValueError, TypeError):
            parts = t_str.split(":")
            return time(int(parts[0]), int(parts[1]))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        current_time_raw = context.get("current_time")

        # Determine the time to evaluate
        if current_time_raw is None:
            now = datetime.now(IST).time()
        elif isinstance(current_time_raw, datetime):
            now = current_time_raw.astimezone(IST).time()
        elif isinstance(current_time_raw, time):
            now = current_time_raw
        elif isinstance(current_time_raw, str):
            try:
                now = self._parse_time(current_time_raw)
            except Exception:
                try:
                    parsed = datetime.fromisoformat(current_time_raw)
                    now = parsed.astimezone(IST).time() if parsed.tzinfo else parsed.time()
                except (ValueError, TypeError):
                    now = datetime.now(IST).time()
        else:
            now = datetime.now(IST).time()

        if self.window_start <= now <= self.window_end:
            return GateResult(
                gate_name="G8_TimeOfDay",
                passed=True,
                message=(
                    f"Current time {now.strftime('%H:%M')} is within "
                    f"window {self.window_start.strftime('%H:%M')}-"
                    f"{self.window_end.strftime('%H:%M')}"
                ),
                severity="info",
            )

        return GateResult(
            gate_name="G8_TimeOfDay",
            passed=False,
            message=(
                f"Current time {now.strftime('%H:%M')} is outside "
                f"window {self.window_start.strftime('%H:%M')}-"
                f"{self.window_end.strftime('%H:%M')}"
            ),
            severity="warning",
        )
