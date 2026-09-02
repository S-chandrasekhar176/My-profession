"""Gate G16: Multi-Timeframe Trend Alignment.

Verifies that the lower-timeframe (e.g. 5-min) entry signal aligns with the
higher-timeframe (15-min and daily) momentum trend to avoid counter-trend traps.
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G16MultiTimeframe:
    """Validate higher timeframe trend alignment for directional setups."""

    def __init__(self, config: Dict[str, Any]):
        self.require_alignment: bool = bool(config.get("require_trend_alignment", True))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        direction = getattr(signal, "direction", "LONG")
        if isinstance(signal, dict):
            direction = signal.get("direction", "LONG")
        direction = str(direction).upper()

        # Resolve higher-timeframe trend with EXPLICIT None checks (v0.4.3
        # fix, audit claim #3): the old `or` chain treated a legitimate
        # empty-string trend as missing and silently fell through to
        # "neutral". Resolution order: trend → nifty_trend → regime (the
        # engine supplies regime as Bull/Bear/Sideways/Volatile) → neutral.
        higher_tf_trend = context.get("trend")
        if higher_tf_trend is None:
            higher_tf_trend = context.get("nifty_trend")
        if higher_tf_trend is None:
            higher_tf_trend = context.get("regime")
        if higher_tf_trend is None:
            higher_tf_trend = "neutral"
        higher_tf_trend = str(higher_tf_trend).strip().lower()

        # Normalize to the three recognized trend classes. Unrecognized
        # values (e.g. "volatile" — a volatility STATE, not a direction, or
        # a typo like "beear") deliberately map to "neutral": that branch
        # applies the STRICTEST behaviour (breakout/momentum/trend setups
        # need confidence >= 0.60) instead of silently falling through every
        # branch and passing unconditionally (the old dead-gate failure
        # mode: unknown value ⇒ no branch matched ⇒ unconditional PASS).
        if higher_tf_trend in ("bull", "bullish", "up"):
            higher_tf_trend = "bull"
        elif higher_tf_trend in ("bear", "bearish", "down"):
            higher_tf_trend = "bear"
        else:
            higher_tf_trend = "neutral"

        # If strict alignment is required
        strat_type = str(
            getattr(signal, "strategy", "")
            or (signal.get("strategy", "") if isinstance(signal, dict) else "")
            or ""
        ).lower()
        # Use explicit None-check so confidence=0.0 is NOT treated as falsy.
        # The old `getattr(..., 0.6) or signal.get(..., 0.6)` pattern silently
        # bumped a zero-confidence signal to 0.6, letting it bypass the G16
        # neutral-market threshold check as if it had 60% conviction.
        _raw_conf = getattr(signal, "confidence", None)
        if _raw_conf is None and isinstance(signal, dict):
            _raw_conf = signal.get("confidence")
        conf = float(_raw_conf if _raw_conf is not None else 0.0)

        if self.require_alignment:
            if direction in ("BUY", "LONG") and higher_tf_trend in ("bear", "bearish", "down"):
                return GateResult(
                    gate_name="G16_MultiTimeframe",
                    passed=False,
                    message="Signal is BUY/LONG but higher timeframe trend is Bearish/Down — counter-trend trap risk",
                    value=0.0,
                    threshold=1.0,
                    severity="warning",
                )
            elif direction in ("SELL", "SHORT") and higher_tf_trend in ("bull", "bullish", "up"):
                return GateResult(
                    gate_name="G16_MultiTimeframe",
                    passed=False,
                    message="Signal is SELL/SHORT but higher timeframe trend is Bullish/Up — counter-trend trap risk",
                    value=0.0,
                    threshold=1.0,
                    severity="warning",
                )
            elif higher_tf_trend in ("neutral", "sideways", "range"):
                # In neutral/sideways trends, pure trend breakouts require higher confidence (>= 0.60)
                if any(k in strat_type for k in ("breakout", "momentum", "trend")) and conf < 0.60:
                    return GateResult(
                        gate_name="G16_MultiTimeframe",
                        passed=False,
                        message=f"Trend breakout setup in neutral market requires higher conviction (confidence {conf:.2f} < 0.60)",
                        value=conf,
                        threshold=0.60,
                        severity="info",
                    )

        return GateResult(
            gate_name="G16_MultiTimeframe",
            passed=True,
            message=f"Multi-timeframe trend verified: {direction} aligns with market momentum ({higher_tf_trend})",
            value=1.0,
            threshold=1.0,
            severity="info",
        )
