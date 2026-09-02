"""Gate G17: Round-Trip Cost Pre-Check.

Blocks signals whose transaction costs would eat an excessive share of the
trade's defined risk. This is the classic intraday cost trap: a tight stop on
a small position can carry ₹40+ of flat brokerage plus statutory fees against
a monetary risk of barely more — the trade then needs a large move just to
break even.

Sizing estimate
---------------
Gates run BEFORE the position sizer, so the gate estimates the typical
quantity from the configured hard risk budget (``hard_risk_pct`` of total
capital divided by the per-share stop distance) — the same risk anchor the
sizer uses. Fees are computed with the real NSEFeeCalculator at the signal's
entry price on both legs (turnover-based fees are approximated at entry;
flat brokerage is exact).

The check rejects when round-trip fees exceed ``max_fee_pct_of_risk``%
(default 30%) of the estimated gross monetary risk.
"""
from typing import Any, Dict

from models.risk_state import GateResult
from core.capital_resolver import resolve_total_capital


class G17CostPreCheck:
    """Reject trades whose round-trip costs are oversized relative to risk."""

    def __init__(self, config: Dict[str, Any]):
        risk_cfg = config or {}
        self.max_fee_pct_of_risk: float = float(risk_cfg.get("max_fee_pct_of_risk", 30.0))
        # Hard risk budget used for the quantity estimate (mirrors the sizer).
        self.hard_risk_pct: float = float(risk_cfg.get("hard_risk_pct", 1.0))
        self.brokerage_per_order: float = float(risk_cfg.get("brokerage_per_order", 20.0))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        def _sig_get(key: str, default: Any = None) -> Any:
            if isinstance(signal, dict):
                return signal.get(key, default)
            return getattr(signal, key, default)

        entry = float(_sig_get("entry_price") or 0.0)
        sl = float(_sig_get("sl_price") or _sig_get("stop_loss") or 0.0)
        target = float(_sig_get("target_price") or _sig_get("target") or 0.0)

        if entry <= 0 or sl <= 0 or target <= 0:
            # Geometry is validated pre-gate; nothing actionable here.
            return GateResult(
                gate_name="G17_CostPreCheck",
                passed=True,
                message="Cost pre-check skipped (incomplete signal geometry)",
                value=None,
                threshold=self.max_fee_pct_of_risk,
                severity="info",
            )

        risk_per_share = abs(entry - sl)
        if risk_per_share <= 0:
            return GateResult(
                gate_name="G17_CostPreCheck",
                passed=True,
                message="Cost pre-check skipped (zero stop distance)",
                value=None,
                threshold=self.max_fee_pct_of_risk,
                severity="info",
            )

        total_capital = resolve_total_capital(context=context)
        risk_budget = total_capital * (self.hard_risk_pct / 100.0)
        est_qty = max(1, int(risk_budget / risk_per_share))
        gross_risk = risk_per_share * est_qty
        reward_per_share = abs(target - entry)

        # Real fee computation at the signal's price levels
        try:
            from fees.nse_fee_calculator import NSEFeeCalculator

            calc = NSEFeeCalculator(brokerage_per_order=self.brokerage_per_order)
            fee_breakdown = calc.calculate_equity_intraday(
                buy_price=entry,
                sell_price=entry,  # turnover approximation at entry price
                quantity=est_qty,
                brokerage_per_order=self.brokerage_per_order,
            )
            fees = float(fee_breakdown.get("total", 0.0))
        except Exception:
            # Fee engine unavailable — do not block trading on a calculator bug.
            return GateResult(
                gate_name="G17_CostPreCheck",
                passed=True,
                message="Cost pre-check skipped (fee calculator unavailable)",
                value=None,
                threshold=self.max_fee_pct_of_risk,
                severity="info",
            )

        fee_pct_of_risk = (fees / gross_risk * 100.0) if gross_risk > 0 else 999.0
        fee_pct_of_reward = (fees / (reward_per_share * est_qty) * 100.0) if reward_per_share > 0 else 999.0

        if fee_pct_of_risk > self.max_fee_pct_of_risk:
            return GateResult(
                gate_name="G17_CostPreCheck",
                passed=False,
                message=(
                    f"Round-trip costs ₹{fees:,.0f} are {fee_pct_of_risk:.0f}% of the estimated "
                    f"monetary risk ₹{gross_risk:,.0f} (qty≈{est_qty} × SL dist ₹{risk_per_share:.2f}) — "
                    f"above the {self.max_fee_pct_of_risk:.0f}% ceiling. Trade needs an oversized move "
                    f"just to break even ({fee_pct_of_reward:.0f}% of gross reward)."
                ),
                value=round(fee_pct_of_risk, 2),
                threshold=self.max_fee_pct_of_risk,
                severity="warning",
            )

        return GateResult(
            gate_name="G17_CostPreCheck",
            passed=True,
            message=(
                f"Round-trip costs ₹{fees:,.0f} = {fee_pct_of_risk:.1f}% of estimated risk "
                f"₹{gross_risk:,.0f} (within {self.max_fee_pct_of_risk:.0f}% ceiling)"
            ),
            value=round(fee_pct_of_risk, 2),
            threshold=self.max_fee_pct_of_risk,
            severity="info",
        )
