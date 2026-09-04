"""Gate G19: Fee-Aware Minimum-Move Check.

G17 asks "are the round-trip costs sane relative to what I am RISKING?"
(fee/stop-distance ceiling). G19 asks the complementary question: "is the
TARGET MOVE worth the cost of playing at all?" — a signal whose gross
reward is barely larger than its round-trip costs needs almost everything
to go right just to net a few rupees, and statistically pays the spread
and the fee stack more than it pays the trader.

Criterion
---------
    gross_reward = |target − entry| × qty
    multiple     = gross_reward / round_trip_fees

The signal is G19-negative when ``multiple < min_move_fee_multiple``
(default 2.0× — the move must pay for the costs twice over).

Modes (``risk.g19_mode``)
-------------------------
- ``log_only`` (DEFAULT): the gate NEVER blocks. A below-multiple signal
  passes with a ``[G19 SHADOW]`` warning message, so live days accumulate
  would-block evidence in the gate log before enforcement is ever enabled.
- ``enforce``: below-multiple signals are blocked (severity warning).
- ``off``: gate skipped entirely.

Sizing estimate
---------------
Gates run BEFORE the position sizer, so the gate mirrors G17's convention:
it estimates quantity from the configured hard-risk budget
(``hard_risk_pct`` of total capital ÷ per-share stop distance) — the same
risk anchor the sizer uses. The fee/reward ratio is per-share invariant
except for the flat brokerage term, and the engine's actual-size cost
re-check re-runs G19 at the real sized quantity when enforcement is on.

Fees come from the real NSEFeeCalculator at the signal's entry price on
both legs (turnover-based fees approximated at entry; flat brokerage is
exact) — the identical model the close path and EOD reconciliation use.
"""
from typing import Any, Dict, Optional

from models.risk_state import GateResult
from core.capital_resolver import resolve_total_capital


class G19MinMoveGate:
    """Block (or shadow) trades whose target move is too small vs costs."""

    def __init__(self, config: Dict[str, Any]):
        risk_cfg = config or {}
        self.mode: str = str(risk_cfg.get("g19_mode", "log_only")).strip().lower()
        if self.mode not in ("log_only", "enforce", "off"):
            # Unknown value fails safe: shadow instead of surprise blocking.
            self.mode = "log_only"
        self.min_move_fee_multiple: float = float(risk_cfg.get("min_move_fee_multiple", 2.0))
        # Hard risk budget used for the quantity estimate (mirrors the sizer,
        # same convention as G17).
        self.hard_risk_pct: float = float(risk_cfg.get("hard_risk_pct", 1.0))
        self.brokerage_per_order: float = float(risk_cfg.get("brokerage_per_order", 20.0))

    # -- shared math (also reused by the engine's actual-size re-check) ----

    @staticmethod
    def round_trip_fees(
        entry: float,
        quantity: int,
        brokerage_per_order: float = 20.0,
    ) -> Optional[float]:
        """Full round-trip fee estimate at the given price and quantity.

        Returns ``None`` when the fee engine is unavailable (callers then
        skip rather than block — same policy as G17).
        """
        try:
            from fees.nse_fee_calculator import NSEFeeCalculator

            breakdown = NSEFeeCalculator(
                brokerage_per_order=brokerage_per_order
            ).calculate_equity_intraday(
                buy_price=entry,
                sell_price=entry,  # turnover approximation at entry price
                quantity=quantity,
                brokerage_per_order=brokerage_per_order,
            )
            fees = float(breakdown.get("total", 0.0))
            return fees if fees >= 0 else None
        except Exception:
            return None

    # -- gate entry point ---------------------------------------------------

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        def _sig_get(key: str, default: Any = None) -> Any:
            if isinstance(signal, dict):
                return signal.get(key, default)
            return getattr(signal, key, default)

        if self.mode == "off":
            return GateResult(
                gate_name="G19_MinMove",
                passed=True,
                message="Fee-aware minimum-move check disabled (g19_mode=off)",
                value=None,
                threshold=self.min_move_fee_multiple,
                severity="info",
            )

        entry = float(_sig_get("entry_price") or 0.0)
        sl = float(_sig_get("sl_price") or _sig_get("stop_loss") or 0.0)
        target = float(_sig_get("target_price") or _sig_get("target") or 0.0)

        if entry <= 0 or sl <= 0 or target <= 0:
            # Geometry is validated pre-gate; nothing actionable here.
            return GateResult(
                gate_name="G19_MinMove",
                passed=True,
                message="Minimum-move check skipped (incomplete signal geometry)",
                value=None,
                threshold=self.min_move_fee_multiple,
                severity="info",
            )

        risk_per_share = abs(entry - sl)
        if risk_per_share <= 0:
            return GateResult(
                gate_name="G19_MinMove",
                passed=True,
                message="Minimum-move check skipped (zero stop distance)",
                value=None,
                threshold=self.min_move_fee_multiple,
                severity="info",
            )

        total_capital = resolve_total_capital(context=context)
        risk_budget = total_capital * (self.hard_risk_pct / 100.0)
        est_qty = max(1, int(risk_budget / risk_per_share))

        fees = self.round_trip_fees(entry, est_qty, self.brokerage_per_order)
        if fees is None or fees <= 0:
            return GateResult(
                gate_name="G19_MinMove",
                passed=True,
                message="Minimum-move check skipped (fee calculator unavailable)",
                value=None,
                threshold=self.min_move_fee_multiple,
                severity="info",
            )

        reward_per_share = abs(target - entry)
        gross_reward = reward_per_share * est_qty
        multiple = (gross_reward / fees) if fees > 0 else 0.0
        fee_pct_of_reward = (fees / gross_reward * 100.0) if gross_reward > 0 else 999.0

        below_multiple = multiple < self.min_move_fee_multiple

        if below_multiple and self.mode == "enforce":
            return GateResult(
                gate_name="G19_MinMove",
                passed=False,
                message=(
                    f"Target move ₹{gross_reward:,.0f} is only {multiple:.2f}× the round-trip "
                    f"costs ₹{fees:,.0f} (qty≈{est_qty}) — below the {self.min_move_fee_multiple:.1f}× "
                    f"minimum; fees would consume {fee_pct_of_reward:.0f}% of the gross reward."
                ),
                value=round(multiple, 2),
                threshold=self.min_move_fee_multiple,
                severity="warning",
            )

        if below_multiple and self.mode == "log_only":
            # SHADOW verdict: pass, but surface the would-block loudly so
            # live days build the enforcement evidence base.
            return GateResult(
                gate_name="G19_MinMove",
                passed=True,
                message=(
                    f"[G19 SHADOW] Would block: target move ₹{gross_reward:,.0f} is only "
                    f"{multiple:.2f}× round-trip costs ₹{fees:,.0f} (qty≈{est_qty}) — below the "
                    f"{self.min_move_fee_multiple:.1f}× minimum; fees would consume "
                    f"{fee_pct_of_reward:.0f}% of the gross reward."
                ),
                value=round(multiple, 2),
                threshold=self.min_move_fee_multiple,
                severity="warning",
            )

        return GateResult(
            gate_name="G19_MinMove",
            passed=True,
            message=(
                f"Target move ₹{gross_reward:,.0f} = {multiple:.2f}× round-trip costs "
                f"₹{fees:,.0f} (≥ {self.min_move_fee_multiple:.1f}× minimum)"
            ),
            value=round(multiple, 2),
            threshold=self.min_move_fee_multiple,
            severity="info",
        )
