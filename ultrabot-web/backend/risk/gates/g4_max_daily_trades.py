"""Gate G4: Max Daily Trades.

Blocks new trades when the daily trade count reaches the configured limit.
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G4MaxDailyTrades:
    """Limit the total number of trades taken in a single day."""

    def __init__(self, config: Dict[str, Any]):
        # int() cast for the same reason as G1 — config may deliver strings/floats.
        raw = config.get("max_daily_trades", 10)
        try:
            self.max_daily_trades: int = int(raw)
        except (TypeError, ValueError):
            self.max_daily_trades = 10

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        # Type guard: context values can be None (JSON null) or collections
        # — the old `context.get("daily_trades", 0)` crashed with TypeError
        # on `None >= int` because .get's default only applies when the key
        # is ABSENT, not when its value is None.
        raw = context.get("daily_trades")
        if raw is None:
            raw = context.get("daily_trade_count", 0)
        if isinstance(raw, (list, tuple, dict)):
            daily_trades = len(raw)
        elif isinstance(raw, bool):
            daily_trades = int(raw)
        elif isinstance(raw, (int, float)):
            daily_trades = int(raw)
        else:
            try:
                daily_trades = int(str(raw).strip())
            except (TypeError, ValueError):
                daily_trades = 0

        if daily_trades >= self.max_daily_trades:
            return GateResult(
                gate_name="G4_MaxDailyTrades",
                passed=False,
                message=(
                    f"Daily trades ({daily_trades}) >= limit ({self.max_daily_trades})"
                ),
                value=float(daily_trades),
                threshold=float(self.max_daily_trades),
                severity="warning",
            )

        return GateResult(
            gate_name="G4_MaxDailyTrades",
            passed=True,
            message=(
                f"Daily trades ({daily_trades}) < limit ({self.max_daily_trades})"
            ),
            value=float(daily_trades),
            threshold=float(self.max_daily_trades),
            severity="info",
        )
