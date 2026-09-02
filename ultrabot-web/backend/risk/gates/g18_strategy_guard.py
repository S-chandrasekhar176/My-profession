"""Gate G18: Per-Strategy Guard (daily loss cap + consecutive-loss cooldown).

Portfolio-level limits (G4/G5) halt the whole engine; they cannot stop ONE
strategy from bleeding while others still perform. G18 adds the per-strategy
layer required for robustness Phase 1:

1. Daily loss cap — block NEW entries of strategy S when S's realized
   net P&L today has fallen below −(cap% of total capital). Default 1.0%,
   with per-strategy overrides (e.g. MRF 0.75%).
2. Consecutive-loss cooldown — after N consecutive losing closes from the
   same strategy today, block its new entries for a cooldown window measured
   from the last loss.

All inputs come from the trades ledger via the engine's risk context
(``strategy_daily_pnl``, ``strategy_consecutive_losses``,
``strategy_last_loss_at``) — never from seeded or synthetic tables.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from models.risk_state import GateResult


class G18StrategyGuard:
    """Per-strategy daily loss cap and consecutive-loss cooldown."""

    def __init__(self, config: Dict[str, Any]):
        risk_cfg = config or {}
        self.default_daily_loss_pct: float = float(risk_cfg.get("per_strategy_daily_loss_pct", 1.0))
        overrides = risk_cfg.get("per_strategy_daily_loss_overrides", {}) or {}
        self.daily_loss_overrides: Dict[str, float] = {
            str(k).upper(): float(v) for k, v in (overrides.items() if isinstance(overrides, dict) else [])
        }
        self.consec_loss_limit: int = int(risk_cfg.get("per_strategy_consec_loss_limit", 2))
        self.consec_loss_cooldown_minutes: float = float(
            risk_cfg.get("per_strategy_consec_loss_cooldown_minutes", 240)
        )

    def _daily_loss_pct_for(self, strategy: str) -> float:
        return self.daily_loss_overrides.get(str(strategy).upper(), self.default_daily_loss_pct)

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        strategy_raw = getattr(signal, "strategy", "") or (signal.get("strategy") if isinstance(signal, dict) else "")
        strategy = str(strategy_raw or "").strip()
        if not strategy:
            return GateResult(
                gate_name="G18_StrategyGuard",
                passed=True,
                message="Per-strategy guard skipped (no strategy on signal)",
                value=None,
                threshold=None,
                severity="info",
            )

        # ---- 1. Per-strategy daily loss cap ---------------------------------
        daily_pnl = context.get("strategy_daily_pnl")
        cap_pct = self._daily_loss_pct_for(strategy)
        if daily_pnl is not None and float(daily_pnl) < 0:
            # Capital resolution mirrors the other gates
            from core.capital_resolver import resolve_total_capital

            total_capital = resolve_total_capital(context=context)
            cap_rupees = total_capital * (cap_pct / 100.0)
            if cap_rupees > 0 and float(daily_pnl) <= -cap_rupees:
                return GateResult(
                    gate_name="G18_StrategyGuard",
                    passed=False,
                    message=(
                        f"{strategy} is down ₹{abs(float(daily_pnl)):,.0f} today — at/below its "
                        f"per-strategy cap of {cap_pct:.2f}% (₹{cap_rupees:,.0f}). "
                        f"No new {strategy} entries until tomorrow."
                    ),
                    value=round(float(daily_pnl), 2),
                    threshold=-round(cap_rupees, 2),
                    severity="warning",
                )

        # ---- 2. Consecutive-loss cooldown -----------------------------------
        consec_losses = context.get("strategy_consecutive_losses")
        last_loss_at = context.get("strategy_last_loss_at")
        if (
            consec_losses is not None
            and int(consec_losses) >= self.consec_loss_limit
            and self.consec_loss_cooldown_minutes > 0
        ):
            now = context.get("current_time")
            now_dt: Optional[datetime] = None
            if isinstance(now, datetime):
                now_dt = now
            elif isinstance(last_loss_at, str) and isinstance(now, datetime):
                now_dt = now

            last_loss_dt: Optional[datetime] = None
            if isinstance(last_loss_at, datetime):
                last_loss_dt = last_loss_at
            elif isinstance(last_loss_at, str):
                try:
                    last_loss_dt = datetime.fromisoformat(last_loss_at)
                except Exception:
                    last_loss_dt = None

            if last_loss_dt is not None and now_dt is not None:
                minutes_since = (now_dt - last_loss_dt).total_seconds() / 60.0
                if minutes_since < self.consec_loss_cooldown_minutes:
                    remaining = self.consec_loss_cooldown_minutes - minutes_since
                    return GateResult(
                        gate_name="G18_StrategyGuard",
                        passed=False,
                        message=(
                            f"{strategy} has {int(consec_losses)} consecutive losses today — "
                            f"entries paused for {int(remaining)} more min "
                            f"(cooldown {int(self.consec_loss_cooldown_minutes)} min)."
                        ),
                        value=int(consec_losses),
                        threshold=self.consec_loss_limit,
                        severity="warning",
                    )
            else:
                # No reliable timestamps → conservatively apply the cooldown
                return GateResult(
                    gate_name="G18_StrategyGuard",
                    passed=False,
                    message=(
                        f"{strategy} has {int(consec_losses)} consecutive losses today — "
                        f"entries paused under the consecutive-loss cooldown."
                    ),
                    value=int(consec_losses),
                    threshold=self.consec_loss_limit,
                    severity="warning",
                )

        return GateResult(
            gate_name="G18_StrategyGuard",
            passed=True,
            message=f"{strategy} within per-strategy daily loss cap ({cap_pct:.2f}%) and cooldown rules",
            value=None,
            threshold=None,
            severity="info",
        )
