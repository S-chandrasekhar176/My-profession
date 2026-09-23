import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, AsyncMock

from db.migrations import Trade
from db.repository import Repository
from notifications.telegram_interactive import compute_pnl_view

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.asyncio
async def test_multi_timeframe_fee_summary_and_gross_win_rate():
    """Verify that get_multi_timeframe_fee_summary properly computes:
    1. Gross Strategy Win Rate (marked on gross pnl > 0, decoupled from fee drag)
    2. Net Realized Win Rate (net_pnl > 0)
    3. Exact Gross P&L, Total Fees Paid, Net P&L, and Fee Drag Percentage.
    """
    today_str = datetime.now(IST).date().isoformat()
    now_iso = datetime.now(IST).isoformat()

    # Create 4 mock trades mimicking the live trading session incident:
    # Trade 1: MARUTI (+₹90.14 gross, ₹107.90 fees -> -₹17.76 net) => Gross Win, Net Loss
    # Trade 2: MOTHERSON (+₹26.66 gross, ₹105.96 fees -> -₹79.30 net) => Gross Win, Net Loss
    # Trade 3: IRFC (-₹76.20 gross, ₹61.73 fees -> -₹137.93 net) => Gross Loss, Net Loss
    # Trade 4: BPCL (+₹140.00 gross, ₹58.00 fees -> +₹82.00 net) => Gross Win, Net Win
    t1 = Trade(
        id="t-maruti-1",
        symbol="MARUTI",
        direction="SELL",
        strategy="VR",
        status="CLOSED",
        pnl=90.14,
        fees=107.90,
        net_pnl=-17.76,
        entry_time=f"{today_str}T10:15:00",
        exit_time=f"{today_str}T10:45:00",
    )
    t2 = Trade(
        id="t-motherson-2",
        symbol="MOTHERSON",
        direction="SELL",
        strategy="BBR",
        status="CLOSED",
        pnl=26.66,
        fees=105.96,
        net_pnl=-79.30,
        entry_time=f"{today_str}T11:00:00",
        exit_time=f"{today_str}T11:30:00",
    )
    t3 = Trade(
        id="t-irfc-3",
        symbol="IRFC",
        direction="SELL",
        strategy="SIC",
        status="CLOSED",
        pnl=-76.20,
        fees=61.73,
        net_pnl=-137.93,
        entry_time=f"{today_str}T12:00:00",
        exit_time=f"{today_str}T12:15:00",
    )
    t4 = Trade(
        id="t-bpcl-4",
        symbol="BPCL",
        direction="BUY",
        strategy="ORB",
        status="CLOSED",
        pnl=140.00,
        fees=58.00,
        net_pnl=82.00,
        entry_time=f"{today_str}T13:00:00",
        exit_time=f"{today_str}T13:45:00",
    )

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [t1, t2, t3, t4]
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = Repository(mock_session)
    summary = await repo.get_multi_timeframe_fee_summary()

    assert "today" in summary
    assert "week" in summary
    assert "month" in summary
    assert "year" in summary
    assert "overall" in summary

    today_bucket = summary["today"]
    assert today_bucket["total_trades"] == 4

    # Technical Strategy Accuracy: 3 trades (MARUTI, MOTHERSON, BPCL) moved in strategy favor
    assert today_bucket["gross_wins"] == 3
    assert today_bucket["gross_losses"] == 1
    assert today_bucket["gross_win_rate"] == 75.0

    # Net Capital Realization: Only 1 trade cleared the transaction fee hurdle
    assert today_bucket["net_wins"] == 1
    assert today_bucket["net_losses"] == 3
    assert today_bucket["net_win_rate"] == 25.0

    # Financial totals
    expected_gross = round(90.14 + 26.66 - 76.20 + 140.00, 2)
    expected_fees = round(107.90 + 105.96 + 61.73 + 58.00, 2)
    expected_net = round(expected_gross - expected_fees, 2)

    assert today_bucket["gross_pnl"] == expected_gross
    assert today_bucket["total_fees"] == expected_fees
    assert today_bucket["net_pnl"] == expected_net
    assert today_bucket["fee_drag_pct"] > 0
    assert today_bucket["avg_trade_fee"] == round(expected_fees / 4, 2)


def test_telegram_pnl_view_computation():
    """Verify compute_pnl_view produces consistent gross, fee and net numbers."""
    pnl_data = {
        "gross_pnl": 180.60,
        "total_fees": 333.59,
        "net_pnl": -152.99,
        "pnl": -152.99,
        "win_rate": 75.0,
        "net_win_rate": 25.0,
        "total_trades": 4,
    }
    view = compute_pnl_view(pnl_data, [])
    assert view["realized"] == -152.99
    assert view["total"] == -152.99
