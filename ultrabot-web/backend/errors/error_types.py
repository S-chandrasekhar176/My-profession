"""Custom exception hierarchy for UltraBot Web."""
from typing import Any, Dict, Optional


class UltraBotError(Exception):
    """Base exception for all UltraBot errors.

    Attributes:
        error_type: Short machine-readable type string.
        severity: One of info, warning, error, critical.
        what_happened: Human-readable description of the error.
        why_happened: Explanation of root cause.
        how_to_fix: Suggested fix.
        context: Arbitrary dict with extra context.
    """

    error_type: str = "UltraBotError"
    severity: str = "error"

    def __init__(
        self,
        what_happened: str = "An unknown error occurred",
        why_happened: Optional[str] = None,
        how_to_fix: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.what_happened = what_happened
        self.why_happened = why_happened or ""
        self.how_to_fix = how_to_fix or ""
        self.context = context or {}
        super().__init__(self.what_happened)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type,
            "severity": self.severity,
            "what_happened": self.what_happened,
            "why_happened": self.why_happened,
            "how_to_fix": self.how_to_fix,
            "context": self.context,
        }


# ──────────────────────────────────────────
# Broker errors
# ──────────────────────────────────────────

class BrokerError(UltraBotError):
    """Base for all broker-related errors."""
    error_type = "BrokerError"


class TokenExpiredError(BrokerError):
    """Broker JWT/session token has expired."""
    error_type = "TokenExpiredError"
    severity = "warning"

    def __init__(self, broker: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Token expired for broker: {broker}"),
            why_happened=kwargs.pop("why_happened", "JWT tokens typically expire after a few hours. The refresh token may also be invalid."),
            how_to_fix=kwargs.pop("how_to_fix", "Re-authenticate with the broker using stored credentials or prompt user to login again."),
            context={"broker": broker, **kwargs.pop("context", {})},
        )


class OrderRejectedError(BrokerError):
    """Broker rejected the order."""
    error_type = "OrderRejectedError"
    severity = "error"

    def __init__(self, order_id: str = "", reason: str = "", symbol: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Order {order_id} for {symbol} was rejected: {reason}"),
            why_happened=kwargs.pop("why_happened", f"The broker rejected the order. Reason: {reason}. Possible causes: insufficient margin, invalid price, market closed, or circuit breaker."),
            how_to_fix=kwargs.pop("how_to_fix", "Check order parameters (price, quantity, lot size). Verify margin availability. If market is closed, retry during trading hours."),
            context={"order_id": order_id, "reason": reason, "symbol": symbol, **kwargs.pop("context", {})},
        )


class ConnectionLostError(BrokerError):
    """Connection to broker API lost."""
    error_type = "ConnectionLostError"
    severity = "critical"

    def __init__(self, broker: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Connection lost to broker: {broker}"),
            why_happened=kwargs.pop("why_happened", "Network connectivity issue, broker API downtime, or firewall blocking the connection."),
            how_to_fix=kwargs.pop("how_to_fix", "Check internet connectivity. Wait for auto-reconnect. If persistent, verify broker API status."),
            context={"broker": broker, **kwargs.pop("context", {})},
        )


# ──────────────────────────────────────────
# Feed errors
# ──────────────────────────────────────────

class FeedError(UltraBotError):
    """Base for all market data feed errors."""
    error_type = "FeedError"


class WebSocketDisconnectedError(FeedError):
    """WebSocket feed disconnected."""
    error_type = "WebSocketDisconnectedError"
    severity = "warning"

    def __init__(self, feed_url: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"WebSocket feed disconnected: {feed_url}"),
            why_happened=kwargs.pop("why_happened", "Network interruption, server-side close, or ping/pong timeout."),
            how_to_fix=kwargs.pop("how_to_fix", "Auto-reconnect with exponential backoff. If persistent, check feed URL and network."),
            context={"feed_url": feed_url, **kwargs.pop("context", {})},
        )


class StaleDataError(FeedError):
    """Market data is stale (no update for too long)."""
    error_type = "StaleDataError"
    severity = "warning"

    def __init__(self, symbol: str = "", seconds_stale: float = 0, **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Stale data for {symbol}: no update for {seconds_stale}s"),
            why_happened=kwargs.pop("why_happened", "Feed stopped sending updates. Possible disconnection or market halt."),
            how_to_fix=kwargs.pop("how_to_fix", "Check feed connection. If market is in a trading halt, wait. Otherwise, reconnect feed."),
            context={"symbol": symbol, "seconds_stale": seconds_stale, **kwargs.pop("context", {})},
        )


class PriceMismatchError(FeedError):
    """Price mismatch between signal price and current market price."""
    error_type = "PriceMismatchError"
    severity = "warning"

    def __init__(self, symbol: str = "", signal_price: float = 0, market_price: float = 0, mismatch_pct: float = 0, **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Price mismatch for {symbol}: signal={signal_price}, market={market_price} ({mismatch_pct}% off)"),
            why_happened=kwargs.pop("why_happened", "Signal was generated some time ago. Market has moved. Or stale data was used."),
            how_to_fix=kwargs.pop("how_to_fix", "Skip this signal. Wait for a fresh signal at current market price."),
            context={"symbol": symbol, "signal_price": signal_price, "market_price": market_price, "mismatch_pct": mismatch_pct, **kwargs.pop("context", {})},
        )


# ──────────────────────────────────────────
# Strategy errors
# ──────────────────────────────────────────

class StrategyError(UltraBotError):
    """Base for all strategy-related errors."""
    error_type = "StrategyError"


class InsufficientDataError(StrategyError):
    """Not enough historical data to run strategy."""
    error_type = "InsufficientDataError"
    severity = "warning"

    def __init__(self, symbol: str = "", strategy: str = "", required_bars: int = 0, available_bars: int = 0, **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Insufficient data for {strategy} on {symbol}: need {required_bars} bars, have {available_bars}"),
            why_happened=kwargs.pop("why_happened", "Instrument is newly listed, or data feed has gaps, or session just started."),
            how_to_fix=kwargs.pop("how_to_fix", "Wait for more candles to accumulate. Check data feed for gaps."),
            context={"symbol": symbol, "strategy": strategy, "required_bars": required_bars, "available_bars": available_bars, **kwargs.pop("context", {})},
        )


class CalculationError(StrategyError):
    """Error in strategy calculation (division by zero, NaN, etc)."""
    error_type = "CalculationError"
    severity = "error"

    def __init__(self, strategy: str = "", detail: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Calculation error in {strategy}: {detail}"),
            why_happened=kwargs.pop("why_happened", "Mathematical error in indicator calculation. Division by zero, NaN propagation, or invalid input data."),
            how_to_fix=kwargs.pop("how_to_fix", "Check input data for zeros/NaN. Add guards in calculation. Log full stack trace."),
            context={"strategy": strategy, "detail": detail, **kwargs.pop("context", {})},
        )


# ──────────────────────────────────────────
# Risk errors
# ──────────────────────────────────────────

class RiskError(UltraBotError):
    """Base for risk management errors."""
    error_type = "RiskError"
    severity = "warning"


class DailyLimitError(RiskError):
    """A daily limit has been hit."""
    error_type = "DailyLimitError"
    severity = "warning"

    def __init__(self, limit_type: str = "", current: float = 0, limit: float = 0, **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Daily {limit_type} limit hit: {current} vs limit {limit}"),
            why_happened=kwargs.pop("why_happened", f"The {limit_type} has reached the configured maximum limit. Trading should stop for the day."),
            how_to_fix=kwargs.pop("how_to_fix", f"No new trades until the next session. Monitor existing positions. Consider reducing position sizes."),
            context={"limit_type": limit_type, "current": current, "limit": limit, **kwargs.pop("context", {})},
        )


class RiskGateRejectionError(RiskError):
    """A signal was rejected by a risk gate."""
    error_type = "RiskGateRejectionError"
    severity = "info"

    def __init__(self, gate_name: str = "", reason: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Signal rejected by risk gate '{gate_name}': {reason}"),
            why_happened=kwargs.pop("why_happened", f"The risk gate '{gate_name}' determined the signal does not meet risk criteria."),
            how_to_fix=kwargs.pop("how_to_fix", "This is expected behavior. The signal was not suitable for entry. No action needed."),
            context={"gate_name": gate_name, "reason": reason, **kwargs.pop("context", {})},
        )


# ──────────────────────────────────────────
# Engine errors
# ──────────────────────────────────────────

class EngineError(UltraBotError):
    """Base for trading engine errors."""
    error_type = "EngineError"
    severity = "critical"


class EngineCrashError(EngineError):
    """The trading engine crashed."""
    error_type = "EngineCrashError"
    severity = "critical"

    def __init__(self, detail: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Trading engine crashed: {detail}"),
            why_happened=kwargs.pop("why_happened", "Uncaught exception in the engine loop. Could be a null reference, type error, or resource exhaustion."),
            how_to_fix=kwargs.pop("how_to_fix", "Review stack trace. Fix the root cause. Restart the engine. Ensure open positions are monitored."),
            context={"detail": detail, **kwargs.pop("context", {})},
        )


class SessionRecoveryError(EngineError):
    """Failed to recover a trading session."""
    error_type = "SessionRecoveryError"
    severity = "critical"

    def __init__(self, session_id: str = "", detail: str = "", **kwargs):
        super().__init__(
            what_happened=kwargs.pop("what_happened", f"Session recovery failed for {session_id}: {detail}"),
            why_happened=kwargs.pop("why_happened", "Session state in DB is corrupted or incomplete. Could be due to unclean shutdown."),
            how_to_fix=kwargs.pop("how_to_fix", "Manually review session state. Verify open positions against broker. Reconcile and restart."),
            context={"session_id": session_id, "detail": detail, **kwargs.pop("context", {})},
        )
