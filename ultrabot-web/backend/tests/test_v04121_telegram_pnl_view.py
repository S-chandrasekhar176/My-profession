"""v0.4.12.1 hotfix regression tests — Telegram /pnl mapping.

Live incident (2026-09-07): the /pnl handler read ``realized_pnl`` /
``unrealized_pnl`` keys from the dict returned by ``get_todays_pnl()``,
but that method returns ``net_pnl`` / ``gross_pnl`` / ``total_trades`` …
The .get() chain always fell through to the default 0, so /pnl printed
₹0.00 / ₹0.00 all session regardless of trading activity.
"""
import pytest

from notifications.telegram_interactive import compute_pnl_view


class _Pos:
    """Minimal stand-in for a Position ORM row."""

    def __init__(self, symbol, direction, qty, entry, current):
        self.symbol = symbol
        self.direction = direction
        self.quantity = qty
        self.entry_price = entry
        self.current_price = current


# Real shape returned by Repository.get_todays_pnl()
REAL_PNL_SHAPE = {
    "date": "2026-09-07",
    "total_trades": 2,
    "wins": 1,
    "losses": 1,
    "win_rate": 50.0,
    "gross_pnl": 85.75,
    "total_fees": 121.8,
    "net_pnl": -36.05,
    "best_trade": 48.82,
    "worst_trade": -84.87,
}


def test_realized_mapped_from_net_pnl():
    """net_pnl (closed trades) must surface as Realized — not ₹0.00."""
    view = compute_pnl_view(REAL_PNL_SHAPE, [])
    assert view["realized"] == pytest.approx(-36.05)


def test_unrealized_direction_aware_mtm():
    """BUY profits on price up; SELL profits on price down (live numbers)."""
    # Live 10:20 IST snapshot: SUPREMEIND BUY 8 @3570.89 → 3570.00 (−7.12)
    # BDL SELL 31 @1261.87 → 1259.70 (+67.27)
    positions = [
        _Pos("SUPREMEIND", "BUY", 8, 3570.89, 3570.00),
        _Pos("BDL", "SELL", 31, 1261.87, 1259.70),
    ]
    view = compute_pnl_view(REAL_PNL_SHAPE, positions)
    assert view["unrealized"] == pytest.approx(60.15, abs=0.02)
    assert view["total"] == pytest.approx(view["realized"] + view["unrealized"], abs=0.02)


def test_long_direction_alias_supported():
    view = compute_pnl_view(None, [_Pos("X", "LONG", 2, 100.0, 110.0)])
    assert view["unrealized"] == pytest.approx(20.0)


def test_degenerate_inputs_do_not_raise():
    view = compute_pnl_view(None, None)
    assert view == {"realized": 0.0, "unrealized": 0.0, "total": 0.0}
    # zero entry / zero qty must be skipped, not divide or raise
    view = compute_pnl_view({}, [_Pos("Y", "BUY", 0, 0, 0)])
    assert view["unrealized"] == 0.0


def test_legacy_keys_still_honored():
    """If a future repo change adds realized_pnl, prefer it over nothing."""
    view = compute_pnl_view({"realized_pnl": 12.34}, [])
    assert view["realized"] == pytest.approx(12.34)
