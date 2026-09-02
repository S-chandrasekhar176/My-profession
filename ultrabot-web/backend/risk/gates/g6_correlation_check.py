"""Gate G6: Correlation Check.

Evaluates cross-asset / inter-stock correlation matrix.
Blocks trades if the proposed asset has high correlation (> threshold, e.g. 0.85)
with an already open position, preventing systemic portfolio risk.
"""
from typing import Any, Dict, List
from models.risk_state import GateResult

from utils.market_utils import get_stock_sector

# Standard empirical correlation coefficients between major Indian liquid assets
_PAIR_CORRELATIONS: Dict[frozenset, float] = {
    # Banking & Financials
    frozenset({"HDFCBANK", "ICICIBANK"}): 0.88,
    frozenset({"HDFCBANK", "KOTAKBANK"}): 0.82,
    frozenset({"ICICIBANK", "AXISBANK"}): 0.85,
    frozenset({"SBIN", "ICICIBANK"}): 0.83,
    frozenset({"SBIN", "HDFCBANK"}): 0.79,
    frozenset({"SBIN", "AXISBANK"}): 0.84,
    frozenset({"BAJFINANCE", "BAJAJFINSV"}): 0.91,
    frozenset({"BAJFINANCE", "HDFCBANK"}): 0.76,
    frozenset({"HDFCLIFE", "SBILIFE"}): 0.82,
    # IT Services
    frozenset({"INFY", "TCS"}): 0.89,
    frozenset({"INFY", "WIPRO"}): 0.84,
    frozenset({"TCS", "WIPRO"}): 0.81,
    frozenset({"HCLTECH", "TECHM"}): 0.86,
    frozenset({"INFY", "HCLTECH"}): 0.85,
    # Energy & Commodities
    frozenset({"RELIANCE", "ONGC"}): 0.78,
    frozenset({"BPCL", "ONGC"}): 0.83,
    frozenset({"NTPC", "POWERGRID"}): 0.85,
    # Auto — TATAMOTORS demerged Oct-2025 into TMPV (PV) + TMCV (CV); pairs
    # remapped to TMPV as the closest surviving auto peer. Values remain
    # heuristic priors pending measured correlations.
    frozenset({"TMPV", "MARUTI"}): 0.76,
    frozenset({"BAJAJ-AUTO", "HEROMOTOCO"}): 0.84,
    frozenset({"M&M", "TMPV"}): 0.80,
    frozenset({"TMPV", "TMCV"}): 0.88,
    # Metals
    frozenset({"TATASTEEL", "JSWSTEEL"}): 0.89,
    frozenset({"HINDALCO", "TATASTEEL"}): 0.83,
    frozenset({"JSWSTEEL", "HINDALCO"}): 0.85,
    # Indices
    frozenset({"NIFTY", "BANKNIFTY"}): 0.86,
    frozenset({"NIFTY", "FINNIFTY"}): 0.91,
    frozenset({"BANKNIFTY", "FINNIFTY"}): 0.94,
    frozenset({"SBIN", "BANKNIFTY"}): 0.87,
    frozenset({"HDFCBANK", "BANKNIFTY"}): 0.92,
    frozenset({"ICICIBANK", "BANKNIFTY"}): 0.91,
    frozenset({"NIFTY", "MIDCPNIFTY"}): 0.79,
}


class G6CorrelationCheck:
    """Block trades when correlation with existing open positions exceeds threshold."""

    def __init__(self, config: Dict[str, Any]):
        self.max_correlation: float = float(config.get("max_pairwise_correlation", config.get("max_correlation", 0.85)))

    def get_correlation(self, sym1: str, sym2: str) -> float:
        """Return estimated pairwise correlation between two symbols."""
        if not sym1 or not sym2:
            return 0.0
        s1 = sym1.upper().strip()
        s2 = sym2.upper().strip()
        if s1 == s2:
            return 1.0
        
        pair_key = frozenset({s1, s2})
        if pair_key in _PAIR_CORRELATIONS:
            return _PAIR_CORRELATIONS[pair_key]
        
        # Sector-based empirical fallback
        sec1 = get_stock_sector(s1)
        sec2 = get_stock_sector(s2)
        if sec1 and sec2 and sec1 == sec2 and sec1 != "Unknown":
            return 0.70
        return 0.35

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        sym = str(getattr(signal, "symbol", "") or context.get("symbol", ""))
        # v0.4.3 (audit claim #1): explicit None checks — an EMPTY list is a
        # VALID value ("no open positions") and must NOT fall through to the
        # second key. The old falsy-`or` chain treated a legitimately-empty
        # open_position_symbols as missing and silently pulled the list from
        # open_positions_list — which a future caller could populate with
        # stale/divergent data. The engine currently writes both keys to the
        # same list in lockstep, so this is a latent-trap fix: explicit None
        # checks make the contract safe for any future caller.
        open_positions = context.get("open_position_symbols")
        if open_positions is None:
            open_positions = context.get("open_positions_list")
        if open_positions is None:
            open_positions = []

        # If open_positions is provided
        if open_positions:
            for p in open_positions:
                pos_sym = p if isinstance(p, str) else getattr(p, "symbol", p.get("symbol", "") if isinstance(p, dict) else "")
                if not pos_sym or pos_sym.upper() == sym.upper():
                    continue
                corr = self.get_correlation(sym, str(pos_sym))
                if corr >= self.max_correlation:
                    return GateResult(
                        gate_name="G6_CorrelationCheck",
                        passed=False,
                        message=(
                            f"High correlation ({corr:.2f}) between incoming {sym} and open position {pos_sym} "
                            f"(limit: {self.max_correlation:.2f})"
                        ),
                        value=corr,
                        threshold=self.max_correlation,
                        severity="warning",
                    )

        return GateResult(
            gate_name="G6_CorrelationCheck",
            passed=True,
            message=f"Correlation check passed for {sym} (all pairs < {self.max_correlation:.2f})",
            value=0.0,
            threshold=self.max_correlation,
            severity="info",
        )
