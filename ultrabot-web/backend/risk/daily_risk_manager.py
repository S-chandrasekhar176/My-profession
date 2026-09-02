from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from models.risk_state import DailyRiskStatus

IST = ZoneInfo("Asia/Kolkata")


class DailyRiskManager:
    """Tracks daily P&L, trade count, consecutive losses, cooloff state.

    Provides methods to decide whether to reduce position sizes, stop
    trading entirely, or enter a cooloff period.
    """

    def __init__(self, config: Dict[str, Any], total_capital: float = 100000.0):
        # NOTE: `config` must be the live settings._raw_config["risk"] dict
        # reference (see config/settings.py get_risk_config()). The 4
        # threshold properties below always read straight from it, so a
        # Settings change applies on the very next check — no restart
        # needed. This object's own mutable daily state (pnl, wins,
        # losses, cooloff_until) is NOT touched by config changes, since
        # rebuilding this whole object (like RiskEngine's stateless gates)
        # would wipe today's already-tracked P&L.
        self.config = config
        self.total_capital = total_capital

        # Mutable daily state
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.breakeven: int = 0
        self.consecutive_losses: int = 0
        self.peak_capital: float = total_capital
        self.cooloff_until: Optional[datetime] = None
        self.gate_rejections: List[Dict[str, Any]] = []

    @property
    def max_daily_loss_pct(self) -> float:
        return float(self.config.get("max_daily_loss_pct", 3))

    @property
    def max_daily_trades(self) -> int:
        return int(self.config.get("max_daily_trades", 10))

    @property
    def max_consecutive_losses(self) -> int:
        return int(self.config.get("max_consecutive_losses", 5))

    @property
    def consec_loss_cooloff_minutes(self) -> int:
        return int(
            self.config.get("consec_loss_cooloff_minutes")
            or self.config.get("cooloff_minutes")
            or 30
        )

    def _max_daily_loss_rupee(self) -> float:
        return self.total_capital * (self.max_daily_loss_pct / 100.0)

    def _in_cooloff(self) -> bool:
        if self.cooloff_until is None:
            return False
        return datetime.now(timezone.utc) < self.cooloff_until

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_daily_limits(
        self,
        open_positions_count: int = 0,
        capital_in_use: float = 0.0,
    ) -> DailyRiskStatus:
        """Return a full DailyRiskStatus snapshot of the current state.

        HOTFIX #7 (live 2026-09-01): open_positions / capital_in_use /
        capital_usage_pct were previously HARDCODED to 0 in the status object,
        which made the /status risk banner blind to live positions. The risk
        GATES were never affected (the engine supplies the real count to G1
        via _build_risk_context). Callers that know the live values should
        pass them in; defaults preserve the old signature.
        """
        loss_limit = self._max_daily_loss_rupee()
        loss_pct = (self.daily_pnl / self.total_capital * 100.0) if self.total_capital > 0 else 0.0
        daily_loss_limit_hit = self.daily_pnl <= -loss_limit
        max_consec_hit = self.consecutive_losses >= self.max_consecutive_losses
        daily_trade_limit_hit = self.daily_trades >= self.max_daily_trades
        in_cooloff = self._in_cooloff()

        current_capital = self.total_capital + self.daily_pnl
        drawdown_pct = 0.0
        if self.peak_capital > 0:
            drawdown_pct = max(0.0, (self.peak_capital - current_capital) / self.peak_capital * 100.0)
        max_dd_limit = float(self.config.get("max_drawdown_pct", 5.0))
        drawdown_limit_hit = drawdown_pct >= max_dd_limit

        can_trade = (
            not daily_loss_limit_hit
            and not daily_trade_limit_hit
            and not max_consec_hit
            and not in_cooloff
            and not drawdown_limit_hit
        )

        block_reason: Optional[str] = None
        if daily_loss_limit_hit:
            block_reason = (
                f"Daily loss limit hit: P&L {self.daily_pnl:,.0f} <= "
                f"-{self.max_daily_loss_pct}% of capital"
            )
        elif drawdown_limit_hit:
            block_reason = f"Max drawdown hit: {drawdown_pct:.2f}% >= {max_dd_limit:.2f}%"
        elif daily_trade_limit_hit:
            block_reason = (
                f"Daily trade limit hit: {self.daily_trades}/{self.max_daily_trades}"
            )
        elif max_consec_hit:
            block_reason = (
                f"Max consecutive losses hit: {self.consecutive_losses}/{self.max_consecutive_losses}"
            )
        elif in_cooloff:
            block_reason = f"In cooloff until {self.cooloff_until.isoformat() if self.cooloff_until else 'N/A'}"

        return DailyRiskStatus(
            date=datetime.now(IST).strftime("%Y-%m-%d"),
            total_trades=self.daily_trades,
            wins=self.wins,
            losses=self.losses,
            breakeven=self.breakeven,
            net_pnl=self.daily_pnl,
            net_pnl_pct=loss_pct,
            daily_loss_pct=abs(loss_pct) if self.daily_pnl < 0 else 0.0,
            consecutive_losses=self.consecutive_losses,
            max_consecutive_losses_hit=max_consec_hit,
            daily_trade_limit_hit=daily_trade_limit_hit,
            daily_loss_limit_hit=daily_loss_limit_hit,
            max_drawdown_pct=round(drawdown_pct, 2),
            drawdown_limit_hit=drawdown_limit_hit,
            capital_in_use=capital_in_use,
            capital_usage_pct=(
                round(capital_in_use / self.total_capital * 100.0, 2)
                if self.total_capital > 0 and capital_in_use > 0
                else 0.0
            ),
            open_positions=open_positions_count,
            max_positions_hit=False,
            in_cooloff=in_cooloff,
            cooloff_until=(
                self.cooloff_until.isoformat() if self.cooloff_until else None
            ),
            vix=None,
            vix_above_threshold=False,
            regime=None,
            can_take_new_trades=can_trade,
            block_reason=block_reason,
        )

    async def get_daily_risk_status(
        self,
        open_positions_count: int = 0,
        capital_in_use: float = 0.0,
    ) -> DailyRiskStatus:
        """Async-compatible alias to check_daily_limits for engine callers."""
        return self.check_daily_limits(
            open_positions_count=open_positions_count,
            capital_in_use=capital_in_use,
        )

    def should_reduce_size(self) -> bool:
        """Return True when daily loss is >= 67 % of the max daily loss limit."""
        loss_limit = self._max_daily_loss_rupee()
        if loss_limit <= 0:
            return False
        reduction_threshold = loss_limit * 0.67
        return self.daily_pnl <= -reduction_threshold

    def should_stop_trading(self) -> bool:
        """Return True when ANY hard stop condition is met."""
        if self.daily_trades >= self.max_daily_trades:
            return True
        if self.daily_pnl <= -self._max_daily_loss_rupee():
            return True
        if self.consecutive_losses >= self.max_consecutive_losses:
            return True
        if self._in_cooloff():
            return True

        # Drawdown check
        if self.peak_capital > 0:
            current_capital = self.total_capital + self.daily_pnl
            drawdown_pct = max(0.0, (self.peak_capital - current_capital) / self.peak_capital * 100.0)
            max_dd_limit = float(self.config.get("max_drawdown_pct", 5.0))
            if drawdown_pct >= max_dd_limit:
                return True

        return False

    def record_pnl(self, pnl: float) -> None:
        """Record realized P&L (e.g., a partial-booking leg) WITHOUT counting a new trade.

        Updates daily P&L, peak capital and the daily-loss cooloff check, but
        leaves trade counters (wins/losses/breakeven/consecutive) untouched so
        G4 (max daily trades) still counts ENTRY trades only.
        """
        self.daily_pnl += pnl

        current_capital = self.total_capital + self.daily_pnl
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital

        if self.daily_pnl <= -self._max_daily_loss_rupee():
            self._enter_cooloff()

    def record_trade_result(self, pnl: float) -> None:
        """Record a completed trade's P&L and update counters."""
        self.daily_pnl += pnl
        self.daily_trades += 1

        current_capital = self.total_capital + self.daily_pnl
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital

        if pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        elif pnl < 0:
            self.losses += 1
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                self._enter_cooloff()
        else:
            self.breakeven += 1
            self.consecutive_losses = 0

        if self.daily_pnl <= -self._max_daily_loss_rupee():
            self._enter_cooloff()

    def record_gate_rejection(
        self, gate_name: str, result: Any, signal: Any
    ) -> None:
        """Record that a signal was rejected by a specific gate."""
        self.gate_rejections.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "gate": gate_name,
                "passed": getattr(result, "passed", False),
                "message": getattr(result, "message", ""),
                "severity": getattr(result, "severity", "info"),
                "symbol": getattr(signal, "symbol", ""),
                "direction": getattr(signal, "direction", ""),
            }
        )

    def get_cooloff_status(self) -> Dict[str, Any]:
        """Return the current cooloff state."""
        active = self._in_cooloff()
        remaining_minutes: float = 0.0
        if self.cooloff_until is not None:
            remaining = self.cooloff_until - datetime.now(timezone.utc)
            remaining_minutes = max(remaining.total_seconds() / 60.0, 0.0)
        return {
            "in_cooloff": active,
            "cooloff_until": (
                self.cooloff_until.isoformat() if self.cooloff_until else None
            ),
            "remaining_minutes": round(remaining_minutes, 1),
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooloff_duration_minutes": self.consec_loss_cooloff_minutes,
        }

    def reset_daily(self) -> None:
        """Reset all daily counters (call at start of each trading day)."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.wins = 0
        self.losses = 0
        self.breakeven = 0
        self.consecutive_losses = 0
        self.cooloff_until = None
        self.gate_rejections = []
        self.peak_capital = self.total_capital

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enter_cooloff(self) -> None:
        """Set the cooloff timer from now."""
        self.cooloff_until = datetime.now(timezone.utc) + timedelta(
            minutes=self.consec_loss_cooloff_minutes
        )
