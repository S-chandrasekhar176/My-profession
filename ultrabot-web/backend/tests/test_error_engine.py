"""Tests for error engine handling and auto-recovery."""
import pytest
from errors.error_engine import ErrorEngine
from errors.error_types import (
    UltraBotError,
    BrokerError,
    TokenExpiredError,
    FeedError,
    WebSocketDisconnectedError,
    StaleDataError,
    PriceMismatchError,
    EngineCrashError,
)


@pytest.fixture
def engine():
    # Reset singleton for each test
    ErrorEngine._instance = None
    return ErrorEngine()


class TestErrorEngineInit:
    def test_singleton(self):
        e1 = ErrorEngine()
        e2 = ErrorEngine()
        assert e1 is e2

    def test_auto_recovery_available(self, engine):
        assert engine.auto_recovery is not None


class TestErrorCodeGeneration:
    def test_generates_code(self, engine):
        code = engine._generate_error_code()
        assert code.startswith("ERR-")
        assert len(code) > 10

    def test_increments_counter(self, engine):
        code1 = engine._generate_error_code()
        code2 = engine._generate_error_code()
        assert code1 != code2


class TestSeverityDetermination:
    def test_valid_severity(self, engine):
        err = UltraBotError(what_happened="test")
        err.severity = "warning"
        assert engine._determine_severity(err) == "warning"

    def test_invalid_severity_defaults_to_error(self, engine):
        err = UltraBotError(what_happened="test")
        err.severity = "something_else"
        assert engine._determine_severity(err) == "error"


@pytest.mark.asyncio
class TestHandleError:
    async def test_handle_ultrabot_error(self, engine):
        err = UltraBotError(
            what_happened="Something went wrong",
            why_happened="Unknown cause",
            how_to_fix="Check logs",
        )
        result = await engine.handle_error(err)
        assert "error_code" in result
        assert result["severity"] == "error"
        assert result["saved_to_db"] is False  # No db_session_getter set

    async def test_handle_raw_exception(self, engine):
        result = await engine.handle_error(ValueError("raw error"))
        assert result["error_code"].startswith("ERR-")
        assert result["severity"] == "error"

    async def test_handle_broker_error(self, engine):
        err = BrokerError(
            what_happened="Connection failed",
            context={"broker": "angel_one"},
        )
        result = await engine.handle_error(err)
        assert result["severity"] in ("error", "warning")

    async def test_handle_critical_error(self, engine):
        err = EngineCrashError(detail="Main loop crashed")
        result = await engine.handle_error(err)
        assert result["severity"] == "critical"

    async def test_handle_with_session_id(self, engine):
        err = UltraBotError(what_happened="test")
        result = await engine.handle_error(err, session_id="test-session-123")
        assert result["error_code"].startswith("ERR-")


class TestCallbacks:
    def test_set_ws_callback(self, engine):
        engine.set_ws_callback(lambda x: None)
        assert engine._ws_callback is not None

    def test_set_telegram_callback(self, engine):
        engine.set_telegram_callback(lambda x: None)
        assert engine._telegram_callback is not None

    def test_set_db_session_getter(self, engine):
        async def getter():
            return None
        engine.set_db_session_getter(getter)
        assert engine._db_session_getter is not None


@pytest.mark.asyncio
class TestAutoRecoveryDispatch:
    async def test_token_expired_no_refresh_fn(self, engine):
        err = TokenExpiredError(broker="angel_one")
        result = await engine.handle_error(err)
        assert result["recovery_result"]["action"] == "prompt_relogin"

    async def test_price_mismatch_no_recovery(self, engine):
        err = PriceMismatchError(
            symbol="RELIANCE",
            signal_price=2450.0,
            market_price=2435.0,
            mismatch_pct=0.6,
        )
        result = await engine.handle_error(err, threshold_pct=0.5)
        # Should attempt recovery but fail (mismatch too large)
        assert result["recovery_result"]["action"] == "skip_signal"
