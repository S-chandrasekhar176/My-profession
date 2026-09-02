"""Tests for the dynamic trade-duration engine (P0.5-a).

The estimator must derive expected hold time from LIVE market data
(ATR / SL-distance / % fallback) × time-of-day × regime — never from
hardcoded per-strategy bands. Every branch and edge case is covered here.
"""

from datetime import datetime, timedelta

import pytest

from core.duration import (
    estimate_trade_duration,
    regime_factor,
    resolve_atr,
    time_of_day_factor,
)

_IST = timedelta(hours=5, minutes=30)


def _at(h: int, m: int, day_offset: int = 0):
    """Weekday IST datetime at the given time (Mon 2026-08-31 by default so
    time-of-day windows are exercised on a nominal trading day)."""
    base = datetime(2026, 8, 31, 0, 0)  # Monday
    return (base + timedelta(days=day_offset)).replace(hour=h, minute=m)


# ─────────────────────────────────────────────
# Input factors
# ─────────────────────────────────────────────


def test_time_of_day_factors_match_indian_u_shape():
    assert time_of_day_factor(_at(9, 15)) == 1.8   # opening drive
    assert time_of_day_factor(_at(10, 0)) == 1.8
    assert time_of_day_factor(_at(10, 30)) == 1.3  # late morning
    assert time_of_day_factor(_at(11, 30)) == 0.9  # lunch lull
    assert time_of_day_factor(_at(12, 30)) == 0.9
    assert time_of_day_factor(_at(13, 30)) == 1.2  # afternoon
    assert time_of_day_factor(_at(14, 30)) == 0.9  # close chop
    assert time_of_day_factor(_at(8, 0)) == 1.0    # pre-open
    assert time_of_day_factor(_at(16, 0)) == 1.0   # post-market


def test_regime_factors():
    assert regime_factor("Bull") == 1.15
    assert regime_factor("Bear") == 1.15
    assert regime_factor("Sideways") == 0.85
    assert regime_factor("Volatile") == 1.4
    assert regime_factor(None) == 1.0
    assert regime_factor("weird-regime") == 1.0
    assert regime_factor("bull") == pytest.approx(1.15)  # case-insensitive


def test_resolve_atr_priority_chain():
    # 1. Explicit ATR always wins
    atr, basis = resolve_atr(entry_price=100.0, stop_loss=98.0, atr=0.7)
    assert (atr, basis) == (0.7, "atr")

    # 2. SL proxy: |100-98| / 1.8
    atr, basis = resolve_atr(entry_price=100.0, stop_loss=98.0, atr=None)
    assert basis == "sl_proxy"
    assert atr == pytest.approx(2.0 / 1.8, rel=1e-6)

    # 3. Percent fallback: 0.4% of entry
    atr, basis = resolve_atr(entry_price=250.0, stop_loss=None, atr=None)
    assert basis == "pct_fallback"
    assert atr == pytest.approx(1.0)

    # 4. Nothing usable
    atr, basis = resolve_atr(entry_price=0.0, stop_loss=None, atr=None)
    assert (atr, basis) == (0.0, "none")


# ─────────────────────────────────────────────
# Core estimator — happy paths
# ─────────────────────────────────────────────


def test_atr_based_estimate_arithmetic():
    """Velocity = ATR × tod × regime; minutes = distance/velocity × 5."""
    res = estimate_trade_duration(
        entry_price=100.0,
        target_price=102.0,
        stop_loss=99.0,
        direction="LONG",
        regime="Sideways",
        atr=0.5,
        now_ist=_at(10, 0),  # tod 1.8
    )
    assert res is not None
    assert res["basis"] == "atr"
    # velocity = 0.5 × 1.8 × 0.85 = 0.765 (rounded to 2dp in the payload)
    assert res["velocity_per_5m"] == pytest.approx(0.765, abs=0.006)
    assert res["candles_to_target"] == pytest.approx(2.0 / 0.765, rel=1e-2)
    base_minutes = (2.0 / 0.765) * 5  # ≈ 13.07
    assert res["min_minutes"] == max(5, round(base_minutes / 1.4))
    assert res["max_minutes"] == round(base_minutes * 1.6)


def test_short_direction_uses_absolute_distance():
    res_short = estimate_trade_duration(
        entry_price=100.0, target_price=98.0, stop_loss=101.0,
        direction="SHORT", regime="Bull", atr=0.5, now_ist=_at(10, 0),
    )
    res_long = estimate_trade_duration(
        entry_price=98.0, target_price=100.0, stop_loss=97.0,
        direction="LONG", regime="Bull", atr=0.5, now_ist=_at(10, 0),
    )
    assert res_short and res_long
    assert res_short["target_distance"] == res_long["target_distance"]
    assert res_short["min_minutes"] == res_long["min_minutes"]
    assert res_short["max_minutes"] == res_long["max_minutes"]


def test_lunch_lull_takes_longer_than_opening_drive():
    """Identical setup at 12:00 must estimate SLOWER than at 09:30."""
    opening = estimate_trade_duration(
        entry_price=100.0, target_price=103.0, stop_loss=99.0,
        atr=0.5, regime="Bull", now_ist=_at(9, 30),
    )
    lunch = estimate_trade_duration(
        entry_price=100.0, target_price=103.0, stop_loss=99.0,
        atr=0.5, regime="Bull", now_ist=_at(12, 0),
    )
    assert opening and lunch
    # lunch velocity = 0.5×0.9×1.15 < opening velocity = 0.5×1.8×1.15
    assert lunch["velocity_per_5m"] < opening["velocity_per_5m"]
    assert lunch["candles_to_target"] > opening["candles_to_target"]


def test_volatile_regime_resolves_faster_than_sideways():
    volatile = estimate_trade_duration(
        entry_price=100.0, target_price=102.0, stop_loss=99.0,
        atr=0.5, regime="Volatile", now_ist=_at(12, 0),
    )
    sideways = estimate_trade_duration(
        entry_price=100.0, target_price=102.0, stop_loss=99.0,
        atr=0.5, regime="Sideways", now_ist=_at(12, 0),
    )
    assert volatile["candles_to_target"] < sideways["candles_to_target"]


def test_far_target_longs_the_estimate_monotonically():
    near = estimate_trade_duration(
        entry_price=100.0, target_price=100.5, stop_loss=99.5,
        atr=0.25, regime="Sideways", now_ist=_at(12, 0),
    )
    far = estimate_trade_duration(
        entry_price=100.0, target_price=104.0, stop_loss=99.5,
        atr=0.25, regime="Sideways", now_ist=_at(12, 0),
    )
    assert far["max_minutes"] > near["max_minutes"]
    assert far["min_minutes"] >= near["min_minutes"]


# ─────────────────────────────────────────────
# Band sanity + square-off cap
# ─────────────────────────────────────────────


def test_band_is_ordered_and_bounded():
    res = estimate_trade_duration(
        entry_price=100.0, target_price=103.0, stop_loss=99.0,
        atr=0.3, regime="Sideways", now_ist=_at(10, 0),
    )
    assert res["min_minutes"] >= 5
    assert res["max_minutes"] >= res["min_minutes"]


def test_estimate_capped_at_square_off():
    """At 15:00 (15 min to square-off) the max can never exceed 15 min."""
    res = estimate_trade_duration(
        entry_price=100.0, target_price=110.0, stop_loss=99.0,
        atr=0.3, regime="Sideways", now_ist=_at(15, 0),
    )
    assert res is not None
    assert res["max_minutes"] <= 15
    assert res["min_minutes"] <= 15


def test_after_hours_no_artificial_cap():
    """Post-close (no trading time left today) the estimate stays uncapped."""
    res = estimate_trade_duration(
        entry_price=100.0, target_price=110.0, stop_loss=99.0,
        atr=0.3, regime="Sideways", now_ist=_at(15, 30),
    )
    assert res is not None
    # 10 distance / (0.3 velocity) ≈ 33 candles ≈ 167 min → band ~119-267
    assert res["max_minutes"] > 60


def test_tiny_estimate_clamps_to_5_minute_floor():
    """Target one tick away must still report >= 5 minutes (band floor)."""
    res = estimate_trade_duration(
        entry_price=100.0, target_price=100.05, stop_loss=99.0,
        atr=1.0, regime="Volatile", now_ist=_at(9, 30),
    )
    assert res["min_minutes"] >= 5


# ─────────────────────────────────────────────
# Degenerate / hostile inputs
# ─────────────────────────────────────────────


def test_no_target_returns_none():
    assert estimate_trade_duration(100.0, None, 99.0, atr=0.5) is None
    assert estimate_trade_duration(100.0, 0, 99.0, atr=0.5) is None


def test_zero_entry_returns_none():
    assert estimate_trade_duration(0, 102.0, 99.0, atr=0.5) is None
    assert estimate_trade_duration(-5, 102.0, 99.0, atr=0.5) is None


def test_target_equal_to_entry_returns_none():
    assert estimate_trade_duration(100.0, 100.0, 99.0, atr=0.5) is None


def test_zero_atr_and_no_sl_falls_back_to_pct():
    res = estimate_trade_duration(
        entry_price=100.0, target_price=102.0, stop_loss=None,
        atr=None, regime=None, now_ist=_at(12, 0),
    )
    assert res is not None
    assert res["basis"] == "pct_fallback"
    assert res["velocity_per_5m"] == pytest.approx(100.0 * 0.004 * 0.9)


def test_all_inputs_unusable_returns_none():
    assert estimate_trade_duration(0.0, None, None) is None


def test_negative_atr_ignored_falls_to_sl_proxy():
    res = estimate_trade_duration(
        entry_price=100.0, target_price=102.0, stop_loss=98.2,
        atr=-1.0, now_ist=_at(12, 0),
    )
    assert res["basis"] == "sl_proxy"


# ─────────────────────────────────────────────
# Integration: opportunity payload carries the estimate
# ─────────────────────────────────────────────


def test_build_opportunity_includes_duration():
    """_build_opportunity must attach expected_duration to every opportunity."""
    import asyncio
    from unittest.mock import MagicMock

    from core.engine import UltraBotEngine

    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.current_regime = "Sideways"
    engine.vix = 14.0
    engine.partial_booker = None
    engine.config = MagicMock()
    engine.config.get_partial_booking_config.return_value = {}
    engine.config.get_risk_config.return_value = {"opportunity_ttl_seconds": 300, "strategy_ttl_seconds": {}}

    signal = {
        "direction": "LONG",
        "confidence": 0.72,
        "entry_price": 100.0,
        "sl_price": 99.0,
        "target_price": 102.0,
        "atr": 0.5,
    }
    sizing = {"quantity": 10, "position_size": 1000, "method": "test"}
    risk = {"passed": True, "all_gates": [{"passed": True}, {"passed": True}]}

    opp = engine._build_opportunity(
        signal=signal,
        strategy_name="ORB",
        symbol="RELIANCE",
        current_price=100.0,
        sizing=sizing,
        risk_result=risk,
    )

    assert "expected_duration" in opp
    assert opp["expected_duration"] is not None
    assert opp["expected_duration"]["basis"] == "atr"
    assert opp["expected_duration"]["min_minutes"] >= 5
    assert opp["expected_duration"]["max_minutes"] >= opp["expected_duration"]["min_minutes"]


def test_build_opportunity_duration_never_crashes_on_bad_signal():
    """A signal with no ATR/SL/target must still build an opportunity."""
    from unittest.mock import MagicMock

    from core.engine import UltraBotEngine

    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.current_regime = None
    engine.vix = 0.0
    engine.partial_booker = None
    engine.config = MagicMock()
    engine.config.get_partial_booking_config.return_value = {}
    engine.config.get_risk_config.return_value = {"opportunity_ttl_seconds": 300, "strategy_ttl_seconds": {}}

    signal = {"direction": "LONG", "confidence": 0.5, "entry_price": 100.0, "sl_price": 0, "target_price": 0}
    opp = engine._build_opportunity(
        signal=signal,
        strategy_name="MB",
        symbol="TCS",
        current_price=100.0,
        sizing={"quantity": 5, "position_size": 500, "method": "test"},
        risk_result={"passed": True, "all_gates": []},
    )
    assert opp is not None
    assert opp["expected_duration"] is None  # no target → no estimate, no crash
