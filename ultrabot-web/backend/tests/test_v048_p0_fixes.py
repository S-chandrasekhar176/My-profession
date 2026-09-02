"""v0.4.8 P0 regression tests — money truthfulness.

Covers the three P0 hotfixes from the 2026-09-01 Telegram-log audit:

  * HF-8  utils/formatters.py — "₹39,900.0.70" double-decimal corruption on
          every amount that carried paise.
  * HF-7  exit classification — "time_stop" contains "stop", so the old
          substring dispatch labeled 5 of 7 live exits as STOP LOSS HIT;
          trades.exit_reason (schema column since day one) is now written.
  * HF-9  accounting reconciliation — partial-booking legs (accumulated on
          position.extra by _execute_partial_booking) are merged into the
          trade row at close so EOD aggregations reconcile to the paisa;
          notifications report the EFFECTIVE (fill) exit price, not the
          requested one; the daily-risk recorder receives only the final
          leg (partials were recorded when they happened).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.exit_taxonomy import (
    EXIT_FAIL_FAST,
    EXIT_MANUAL,
    EXIT_PARTIAL,
    EXIT_SQUAREOFF,
    EXIT_STOP_LOSS,
    EXIT_TARGET,
    EXIT_TIME,
    EXIT_TRAILING_SL,
    EXIT_UNKNOWN,
    classify_exit,
    exit_alert_kind,
)
from utils.formatters import format_currency

from core.engine import UltraBotEngine


# ---------------------------------------------------------------------------
# HF-8 — format_currency double decimal
# ---------------------------------------------------------------------------
class TestFormatCurrencyHf8:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (39900.70, "₹39,900.70"),        # the live AMBUJACEM amount
            (0.5, "₹0.50"),                  # sub-rupee: old code → "₹0.0.50"
            (0.05, "₹0.05"),
            (1234567.89, "₹12,34,567.89"),   # Indian grouping
            (100.00, "₹100"),                # no paise → no decimal tail
            (1000000.0, "₹10,00,000"),
            (-179.27, "-₹179.27"),           # live EICHERMOT loss
            (-0.99, "-₹0.99"),
            (0, "₹0"),
        ],
    )
    def test_formatting(self, value, expected):
        assert format_currency(value) == expected

    def test_show_sign(self):
        assert format_currency(263.27, show_sign=True) == "+₹263.27"
        assert format_currency(-263.27, show_sign=True) == "-₹263.27"

    def test_no_double_decimal_anywhere(self):
        for value in (39900.70, 1234.56, 0.10, 99999.99, 7.01):
            out = format_currency(value)
            assert out.count(".") <= 1, f"double decimal for {value}: {out}"


# ---------------------------------------------------------------------------
# HF-7 — exit taxonomy
# ---------------------------------------------------------------------------
class TestExitTaxonomy:
    @pytest.mark.parametrize(
        "reason,expected",
        [
            ("target", EXIT_TARGET),
            ("time_stop", EXIT_TIME),        # used to render as STOP LOSS!
            ("time", EXIT_TIME),
            ("fail_fast", EXIT_FAIL_FAST),
            ("auto_squareoff", EXIT_SQUAREOFF),
            ("partial_complete", EXIT_PARTIAL),
            ("manual", EXIT_MANUAL),
            ("MANUAL", EXIT_MANUAL),
            ("stop_loss", EXIT_STOP_LOSS),
            ("sl", EXIT_STOP_LOSS),
            ("mysterious_reason", EXIT_UNKNOWN),
            ("", EXIT_UNKNOWN),
            (None, EXIT_UNKNOWN),
        ],
    )
    def test_reason_probes(self, reason, expected):
        assert classify_exit(reason) == expected

    def test_initial_sl_at_loss(self):
        assert classify_exit(
            "stop_loss", direction="BUY", entry_price=100.0,
            exit_price=95.0, stop_loss=95.0,
        ) == EXIT_STOP_LOSS

    def test_trailed_sl_long_locks_profit(self):
        # Live HCLTECH case: SL raised 1336.98 -> 1358.72 while entry 1349.28
        assert classify_exit(
            "stop_loss", direction="BUY", entry_price=1349.28,
            exit_price=1360.10, stop_loss=1358.72,
        ) == EXIT_TRAILING_SL

    def test_trailed_sl_short_locks_profit(self):
        assert classify_exit(
            "stop_loss", direction="SELL", entry_price=100.0,
            exit_price=96.0, stop_loss=96.5,
        ) == EXIT_TRAILING_SL

    def test_profitable_exit_without_stop_evidence_falls_back_to_pnl(self):
        assert classify_exit(
            "stop_loss", direction="BUY", entry_price=100.0, exit_price=103.0,
        ) == EXIT_TRAILING_SL
        assert classify_exit(
            "stop_loss", direction="SELL", entry_price=100.0, exit_price=99.0,
        ) == EXIT_TRAILING_SL

    def test_exit_alert_kind_mapping(self):
        assert exit_alert_kind(EXIT_TARGET) == "target_hit"
        assert exit_alert_kind(EXIT_STOP_LOSS) == "stop_loss_hit"
        assert exit_alert_kind(EXIT_TRAILING_SL) == "stop_loss_hit"
        assert exit_alert_kind(EXIT_TIME) == "position_closed"
        assert exit_alert_kind(EXIT_SQUAREOFF) == "position_closed"
        assert exit_alert_kind(EXIT_MANUAL) == "position_closed"


# ---------------------------------------------------------------------------
# HF-9 — close-path accounting (synthetic round trip)
# ---------------------------------------------------------------------------
def _make_pos(direction, entry=100.0, current=105.0, qty=10, extra=None):
    return SimpleNamespace(
        id="pos-1",
        trade_id="trade-1",
        symbol="TCS",
        strategy="ORB",
        direction=direction,
        quantity=qty,
        entry_price=entry,
        current_price=current,
        stop_loss=95.0,
        target=110.0,
        entry_time="2026-09-01T10:00:00+05:30",
        extra=extra,
    )


def _make_close_engine(broker=None):
    """Same proven pattern as test_squareoff_close_direction_fix.py."""
    engine = MagicMock(spec=UltraBotEngine)
    engine.broker = broker
    engine.session_id = "test-session"
    engine._errors_count = 0
    engine.config = MagicMock()
    engine.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    engine.daily_risk = None
    engine.error_engine = MagicMock()
    engine.error_engine.handle_error = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()

    repo = MagicMock()
    repo.update_position = AsyncMock()
    repo.update_trade = AsyncMock()

    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    engine._repo_context = MagicMock(return_value=RepoCtx())
    # Bind the REAL extra parser — MagicMock's __float__ returns 1.0 by
    # default, which would silently corrupt the partial-leg merge under
    # test (exactly the kind of lie these tests exist to catch).
    engine._position_extra_dict = UltraBotEngine._position_extra_dict
    engine._close_position = UltraBotEngine._close_position.__get__(
        engine, UltraBotEngine
    )
    return engine, repo


def _captured_trade_update(repo):
    args, kwargs = repo.update_trade.await_args
    return args, kwargs


def _close_broadcast(engine):
    for call in engine._broadcast.await_args_list:
        payload = call.args[1] if len(call.args) > 1 else call.args[0]
        if isinstance(payload, dict) and payload.get("type") == "position_closed":
            return payload
    return None


class TestCloseAccountingHf9:
    @pytest.mark.asyncio
    async def test_plain_close_records_exit_reason_and_fill_price(self):
        """time_stop close must record TIME_EXIT — never the SL template."""
        # Broker fill differs from requested → notification must show FILL
        broker = AsyncMock()
        broker.place_order = AsyncMock(
            return_value={"status": "FILLED", "filled_price": 406.10}
        )
        engine, repo = _make_close_engine(broker=broker)
        pos = _make_pos("SELL", entry=407.0, current=406.15, qty=98)

        await engine._close_position(
            position=pos, exit_price=406.15, close_reason="time_stop",
            pnl_amount=83.30, pnl_pct=0.0,
        )
        args, kwargs = _captured_trade_update(repo)
        assert kwargs["exit_reason"] == EXIT_TIME
        assert kwargs["exit_price"] == pytest.approx(406.10)  # fill, not request

        payload = _close_broadcast(engine)
        assert payload["exit_price"] == pytest.approx(406.10)
        assert payload["requested_exit_price"] == pytest.approx(406.15)
        assert payload["exit_reason"] == EXIT_TIME

        # SELL entry 407, fill 406.10, qty 98 → gross +88.20; template routed
        # through position_closed (time exit), NOT stop_loss_hit.
        routed = [c.args[0] for c in engine._route_alert.await_args_list]
        assert "position_closed" in routed
        assert "stop_loss_hit" not in routed

    @pytest.mark.asyncio
    async def test_partial_legs_merged_into_round_trip(self):
        """The HCLTECH 2026-09-01 leak: +90.58 gross / +39.92 net partial leg
        must land inside the trade record, not vanish from the EOD."""
        extra = json.dumps({
            "partial_realized_pnl": 90.58,
            "partial_fees": 50.66,
        })
        pos = _make_pos("BUY", entry=1349.28, current=1366.20, qty=22, extra=extra)

        engine, repo = _make_close_engine(broker=None)
        await engine._close_position(
            position=pos, exit_price=1366.20, close_reason="time_stop",
            pnl_amount=371.36, pnl_pct=0.0,
        )
        args, kwargs = _captured_trade_update(repo)
        # Final leg gross 22*(1366.20-1349.28)=372.24 + partial 90.58
        assert kwargs["pnl"] == pytest.approx(372.24 + 90.58, abs=0.02)
        # Fees = final-leg fees + partial-leg fees (both legs' round trips)
        assert kwargs["fees"] > 50.66
        expected_net = round((372.24 + 90.58) - kwargs["fees"], 2)
        assert kwargs["net_pnl"] == pytest.approx(expected_net, abs=0.01)

        payload = _close_broadcast(engine)
        assert payload["fees"] == pytest.approx(kwargs["fees"])
        assert payload["net_pnl"] == pytest.approx(kwargs["net_pnl"])

    @pytest.mark.asyncio
    async def test_daily_risk_gets_final_leg_only(self):
        """Partial legs were already recorded when they happened — recording
        the merged net here would double-count them."""
        extra = json.dumps({"partial_realized_pnl": 90.58, "partial_fees": 50.66})
        daily_risk = MagicMock()
        pos = _make_pos("BUY", entry=1349.28, current=1366.20, qty=22, extra=extra)

        engine, repo = _make_close_engine(broker=None)
        engine.daily_risk = daily_risk
        daily_risk.check_daily_limits.return_value = None

        await engine._close_position(
            position=pos, exit_price=1366.20, close_reason="time_stop",
            pnl_amount=371.36, pnl_pct=0.0,
        )
        recorded = daily_risk.record_trade_result.call_args.kwargs["pnl"]
        args, kwargs = _captured_trade_update(repo)
        # Final leg only: 372.24 - final-leg fees (< merged fees)
        assert recorded < kwargs["net_pnl"]  # strictly the final leg
        assert recorded == pytest.approx(round(372.24 - (kwargs["fees"] - 50.66), 2), abs=0.05)

    @pytest.mark.asyncio
    async def test_trailing_sl_exit_routes_to_sl_template_with_reason(self):
        pos = _make_pos("BUY", entry=1349.28, current=1360.10, qty=22)
        pos.stop_loss = 1358.72  # trailed past entry

        engine, repo = _make_close_engine(broker=None)
        await engine._close_position(
            position=pos, exit_price=1360.10, close_reason="stop_loss",
            pnl_amount=238.0, pnl_pct=0.0,
        )
        args, kwargs = _captured_trade_update(repo)
        assert kwargs["exit_reason"] == EXIT_TRAILING_SL
        routed = [c.args[0] for c in engine._route_alert.await_args_list]
        assert "stop_loss_hit" in routed
        payload = _close_broadcast(engine)
        assert payload["exit_reason_label"] == "Trailing Stop Exit (Profit Locked)"

    @pytest.mark.asyncio
    async def test_target_exit_routes_to_target_template(self):
        pos = _make_pos("BUY", entry=100.0, current=110.5, qty=10)
        engine, repo = _make_close_engine(broker=None)
        await engine._close_position(
            position=pos, exit_price=110.5, close_reason="target",
            pnl_amount=105.0, pnl_pct=0.0,
        )
        routed = [c.args[0] for c in engine._route_alert.await_args_list]
        assert "target_hit" in routed
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["exit_reason"] == EXIT_TARGET

    @pytest.mark.asyncio
    async def test_no_partials_keeps_legacy_math(self):
        """Without partial legs the numbers must match the v0.4.4 behavior."""
        pos = _make_pos("BUY", entry=100.0, current=105.0, qty=10)
        engine, repo = _make_close_engine(broker=None)
        await engine._close_position(
            position=pos, exit_price=105.0, close_reason="auto_squareoff",
            pnl_amount=-50.0, pnl_pct=-5.0,  # caller-inverted; must be ignored
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(50.0)
        assert kwargs["net_pnl"] == pytest.approx(50.0 - kwargs["fees"])
