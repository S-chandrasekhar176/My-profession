"""v0.4.12 — point-in-time feature snapshot tests (shadow/features.py).

Covers: session bucketing, ATR/ATR% math, VWAP distance, trend strength,
HTF trend, liquidity ratio, snapshot assembly honesty (None = not observed,
never zero) and the never-raises guarantee.
"""
from datetime import datetime

import pandas as pd
import pytest

import shadow.features as feats
from shadow.features import (
    FEATURES_SCHEMA_VERSION,
    SESSION_AFTERNOON,
    SESSION_LUNCH,
    SESSION_MORNING,
    SESSION_OPENING_DRIVE,
    SESSION_POWER_CLOSE,
    classify_session,
    compute_atr,
    compute_atr_pct,
    compute_feature_snapshot,
    compute_htf_trend,
    compute_liquidity_ratio,
    compute_trend_strength,
    compute_vwap_distance_pct,
)
from utils.candle_utils import candles_to_dataframe


def _df(rows):
    """rows: list of dicts with ts/h/l/c/v — via the PRODUCTION converter."""
    candles = [
        {
            "timestamp": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for (ts, o, h, l, c, v) in rows
    ]
    return candles_to_dataframe(candles)


def _flat_rows(n, base=100.0, vol=100):
    """Constant 100 +/-1 range: true range = 2.0 every bar."""
    out = []
    for i in range(n):
        ts = f"2026-09-04 09:15:{i:02d}"
        out.append((ts, base - 1, base + 1, base - 1, base, vol))
    return out


# ────────────────────────────────────────
# Session buckets (IST intraday)
# ────────────────────────────────────────
class TestClassifySession:
    def test_opening_drive(self):
        assert classify_session(datetime(2026, 9, 7, 9, 15)) == SESSION_OPENING_DRIVE
        assert classify_session(datetime(2026, 9, 7, 9, 59)) == SESSION_OPENING_DRIVE

    def test_morning(self):
        assert classify_session(datetime(2026, 9, 7, 10, 0)) == SESSION_MORNING
        assert classify_session(datetime(2026, 9, 7, 11, 29)) == SESSION_MORNING

    def test_lunch(self):
        assert classify_session(datetime(2026, 9, 7, 12, 0)) == SESSION_LUNCH

    def test_afternoon(self):
        assert classify_session(datetime(2026, 9, 7, 14, 0)) == SESSION_AFTERNOON

    def test_power_close(self):
        assert classify_session(datetime(2026, 9, 7, 15, 0)) == SESSION_POWER_CLOSE
        assert classify_session(datetime(2026, 9, 7, 15, 30)) == SESSION_POWER_CLOSE

    def test_outside_market_is_none(self):
        assert classify_session(datetime(2026, 9, 7, 9, 0)) is None
        assert classify_session(datetime(2026, 9, 7, 16, 0)) is None
        assert classify_session(datetime(2026, 9, 7, 8, 0)) is None

    def test_garbage_input_none(self):
        assert classify_session(None) is None
        assert classify_session("not-a-datetime") is None


# ────────────────────────────────────────
# ATR math
# ────────────────────────────────────────
class TestATR:
    def test_constant_range_atr_is_two(self):
        df = _df(_flat_rows(20))
        assert compute_atr(df) == pytest.approx(2.0, abs=1e-9)

    def test_too_few_candles_none(self):
        assert compute_atr(_df(_flat_rows(10))) is None

    def test_empty_and_none(self):
        assert compute_atr(None) is None
        assert compute_atr(pd.DataFrame()) is None

    def test_atr_pct_scale_free(self):
        df = _df(_flat_rows(20, base=100.0))
        assert compute_atr_pct(df) == pytest.approx(2.0, abs=1e-6)  # 2/100*100
        df_hi = _df(_flat_rows(20, base=200.0))
        assert compute_atr_pct(df_hi) == pytest.approx(1.0, abs=1e-6)  # 2/200*100


# ────────────────────────────────────────
# VWAP distance
# ────────────────────────────────────────
class TestVWAP:
    def test_hand_computed_distance(self):
        # 5 bars, typical = (h+l+c)/3 = (11+9+10)/3 = 10.0 constant,
        # volumes 100..140 -> VWAP = 10.0; last close 10.0 -> distance 0%
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 100 + i) for i in range(5)]
        assert compute_vwap_distance_pct(_df(rows)) == pytest.approx(0.0, abs=1e-9)

    def test_close_above_vwap_positive(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 100) for i in range(4)]
        rows.append(("2026-09-04 09:15:04", 9, 11, 9, 12.0, 100))
        d = compute_vwap_distance_pct(_df(rows))
        assert d is not None and d > 0

    def test_zero_volume_none(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 0) for i in range(6)]
        assert compute_vwap_distance_pct(_df(rows)) is None

    def test_missing_volume_column_none(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 0) for i in range(6)]
        df = candles_to_dataframe(
            [{"timestamp": t, "open": o, "high": h, "low": l, "close": c}
             for (t, o, h, l, c, v) in rows]
        )
        assert compute_vwap_distance_pct(df) is None


# ────────────────────────────────────────
# Trend strength / HTF trend
# ────────────────────────────────────────
class TestTrend:
    def test_rising_closes_positive_strength(self):
        rows = []
        for i in range(30):
            c = 100.0 + i * 0.5
            rows.append((f"2026-09-04 09:15:{i:02d}", c - 1, c + 1, c - 1, c, 100))
        strength = compute_trend_strength(_df(rows))
        assert strength is not None and strength > 0.5

    def test_falling_closes_negative_strength(self):
        rows = []
        for i in range(30):
            c = 115.0 - i * 0.5
            rows.append((f"2026-09-04 09:15:{i:02d}", c - 1, c + 1, c - 1, c, 100))
        strength = compute_trend_strength(_df(rows))
        assert strength is not None and strength < -0.5

    def test_htf_trend_up(self):
        rows = []
        for i in range(45):  # ~3.75h of 5m bars -> >= 15 resampled 15m bars
            c = 100.0 + i * 1.0
            rows.append((f"2026-09-04 09:{15 if i < 9 else 15}:{i:02d}", c - 1, c + 1, c - 1, c, 100))
        df = candles_to_dataframe(
            [{"timestamp": f"2026-09-04 {(9 + i // 12):02d}:{(i * 5) % 60:02d}:00",
              "open": c - 1, "high": c + 1, "low": c - 1, "close": c, "volume": 100}
             for i, c in enumerate([100.0 + i * 1.0 for i in range(45)])]
        )
        assert compute_htf_trend(df) == "up"

    def test_htf_trend_too_short_none(self):
        assert compute_htf_trend(_df(_flat_rows(10))) is None


# ────────────────────────────────────────
# Liquidity
# ────────────────────────────────────────
class TestLiquidity:
    def test_relative_volume(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 100) for i in range(5)]
        rows.append(("2026-09-04 09:15:05", 9, 11, 9, 10.0, 300))
        df = _df(rows)
        # mean of [100]*5 + [300] = 133.33; last=300 -> ~2.25
        assert compute_liquidity_ratio(df) == pytest.approx(2.25, abs=1e-3)

    def test_insufficient_rows_none(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 100) for i in range(3)]
        assert compute_liquidity_ratio(_df(rows)) is None


# ────────────────────────────────────────
# Snapshot assembly (honesty contract)
# ────────────────────────────────────────
class TestSnapshotAssembly:
    def test_full_snapshot_shape(self):
        snap = compute_feature_snapshot(_df(_flat_rows(20)), now=datetime(2026, 9, 4, 9, 20))
        assert snap["schema_version"] == FEATURES_SCHEMA_VERSION == "1.1"
        assert snap["session_class"] == SESSION_OPENING_DRIVE
        assert snap["n_candles"] == 20
        assert snap["has_volume"] is True
        assert snap["atr"] == pytest.approx(2.0, abs=1e-6)
        assert snap["computed_at"] is not None
        assert "vix" in snap
        assert "pcr" in snap
        assert "iv_rank" in snap

    def test_schema_v1_1_new_keys(self):
        # (a) snapshot returns 3 new keys, None when not passed, correct values when passed
        snap_default = compute_feature_snapshot(_df(_flat_rows(20)), now=datetime(2026, 9, 4, 9, 20))
        assert snap_default["schema_version"] == "1.1"
        assert snap_default["vix"] is None
        assert snap_default["pcr"] is None
        assert snap_default["iv_rank"] is None

        snap_passed = compute_feature_snapshot(
            _df(_flat_rows(20)),
            now=datetime(2026, 9, 4, 9, 20),
            vix=14.5,
            pcr=1.25,
            iv_rank=45.0,
        )
        assert snap_passed["schema_version"] == "1.1"
        assert snap_passed["vix"] == 14.5
        assert snap_passed["pcr"] == 1.25
        assert snap_passed["iv_rank"] == 45.0

    def test_features_schema_version_is_1_1(self):
        # (b) FEATURES_SCHEMA_VERSION == "1.1"
        assert FEATURES_SCHEMA_VERSION == "1.1"

    def test_empty_inputs_all_none_never_zero(self):
        for bad in (None, pd.DataFrame(), "junk", [1, 2, 3]):
            snap = compute_feature_snapshot(bad, now=datetime(2026, 9, 4, 12, 0))
            assert snap["atr"] is None
            assert snap["vwap_distance_pct"] is None
            assert snap["trend_strength"] is None
            assert snap["htf_trend"] is None
            assert snap["liquidity_ratio"] is None
            assert snap["session_class"] == SESSION_LUNCH  # time is still known
            assert snap["n_candles"] == 0
            assert snap["vix"] is None
            assert snap["pcr"] is None
            assert snap["iv_rank"] is None

    def test_no_volume_is_flagged(self):
        rows = [(f"2026-09-04 09:15:{i:02d}", 9, 11, 9, 10.0, 0) for i in range(20)]
        df = candles_to_dataframe(
            [{"timestamp": t, "open": o, "high": h, "low": l, "close": c}
             for (t, o, h, l, c, v) in rows]
        )
        snap = compute_feature_snapshot(df, now=datetime(2026, 9, 4, 10, 0))
        assert snap["has_volume"] is False
        assert snap["vwap_distance_pct"] is None
        assert snap["liquidity_ratio"] is None

    def test_feature_failure_recorded_not_raised(self, monkeypatch):
        def boom(df, period=14):
            raise RuntimeError("boom")
        monkeypatch.setattr(feats, "compute_atr", boom)
        snap = feats.compute_feature_snapshot(_df(_flat_rows(20)), now=datetime(2026, 9, 4, 10, 0))
        assert "atr" in snap.get("snapshot_error", "")
        assert snap["atr"] is None  # honest absence, not a fabricated number

    def test_json_serializable(self):
        import json

        snap = compute_feature_snapshot(_df(_flat_rows(20)), now=datetime(2026, 9, 4, 10, 0))
        assert json.loads(json.dumps(snap, sort_keys=True, default=str)) == snap

    def test_build_dataset_per_schema_counters(self):
        # (c) build_dataset per-schema counters on a mixed 1.0/1.1 fixture
        import json
        from ml.dataset import MLDatasetBuilder
        builder = MLDatasetBuilder()

        row_v1_0 = {
            "features_json": json.dumps({
                "schema_version": "1.0",
                "atr_pct": 1.2,
                "vwap_distance_pct": 0.5,
                "trend_strength": 0.8,
                "liquidity_ratio": 1.1,
            }),
            "outcome": "SHADOW_TARGET",
            "pnl_per_share": 10.0,
            "is_synthetic": False,
        }
        row_v1_1 = {
            "features_json": json.dumps({
                "schema_version": "1.1",
                "atr_pct": 1.5,
                "vwap_distance_pct": -0.2,
                "trend_strength": 1.0,
                "liquidity_ratio": 1.3,
                "vix": 16.0,
                "pcr": 1.1,
                "iv_rank": 40.0,
            }),
            "outcome": "SHADOW_SL",
            "pnl_per_share": -5.0,
            "is_synthetic": False,
        }
        row_unknown = {
            "features_json": json.dumps({
                "atr_pct": 1.1,
                "vwap_distance_pct": 0.1,
                "trend_strength": 0.2,
                "liquidity_ratio": 1.0,
            }),
            "outcome": "SHADOW_TARGET",
            "pnl_per_share": 8.0,
            "is_synthetic": False,
        }
        row_excluded = {
            "outcome": "SHADOW_TARGET",
            "is_synthetic": False,
        }

        outcomes = [row_v1_0, row_v1_1, row_unknown, row_excluded]
        X, y = builder.build_dataset(outcomes, fit_scaler=False)

        assert len(X) == 3
        assert len(y) == 3
        assert builder.last_schema_counts == {"1.0": 1, "1.1": 1, "unknown": 1}

    @pytest.mark.asyncio
    async def test_engine_call_site_degrades_safely(self):
        # (d) engine call site degrades safely when pcr/iv_rank sources are absent
        from core.engine import UltraBotEngine
        from unittest.mock import MagicMock, AsyncMock

        engine = UltraBotEngine.__new__(UltraBotEngine)
        engine.vix = 15.5
        engine.current_regime = "bull"
        engine._shadow_feature_snapshot_enabled = True
        engine.strategy_registry = MagicMock()
        mock_strat = MagicMock()
        mock_strat.scan = AsyncMock(return_value={"direction": "BUY", "entry_price": 100.0, "stop_loss": 98.0, "target": 104.0})
        engine.strategy_registry.get = MagicMock(return_value=mock_strat)

        candles = [
            {"timestamp": f"2026-09-04 09:15:{i:02d}", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000}
            for i in range(20)
        ]
        res = await engine._execute_strategy_scan(
            symbol="RELIANCE",
            candles=candles,
            strategy_name="ORB",
            regime="bull",
            vix=15.5,
        )
        assert res is not None
        assert "features_snapshot" in res
        snap = res["features_snapshot"]
        assert snap["schema_version"] == "1.1"
        assert snap["vix"] == 15.5
        assert snap["pcr"] is None
        assert snap["iv_rank"] is None

    @pytest.mark.asyncio
    async def test_engine_call_site_rejects_stale_option_recorder(self):
        # (NF12-A) engine call site rejects stale option metrics and records None for pcr/iv_rank
        from core.engine import UltraBotEngine
        from unittest.mock import MagicMock, AsyncMock

        engine = UltraBotEngine.__new__(UltraBotEngine)
        engine.vix = 14.2
        engine.current_regime = "sideways"
        engine._shadow_feature_snapshot_enabled = True
        engine.strategy_registry = MagicMock()
        mock_strat = MagicMock()
        mock_strat.scan = AsyncMock(return_value={"direction": "BUY", "entry_price": 100.0, "stop_loss": 98.0, "target": 104.0})
        engine.strategy_registry.get = MagicMock(return_value=mock_strat)

        # Mock option_recorder returning stale metrics (e.g. feed outage fallback)
        mock_opt_rec = MagicMock()
        mock_opt_rec.get_latest_metrics = MagicMock(return_value={
            "pcr": 1.0,
            "iv": 0.15,
            "iv_rank": 50.0,
            "stale": True,
        })
        engine.option_recorder = mock_opt_rec

        candles = [
            {"timestamp": f"2026-09-04 09:15:{i:02d}", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000}
            for i in range(20)
        ]
        res = await engine._execute_strategy_scan(
            symbol="TCS",
            candles=candles,
            strategy_name="ORB",
            regime="sideways",
            vix=14.2,
        )
        assert res is not None
        assert "features_snapshot" in res
        snap = res["features_snapshot"]
        assert snap["schema_version"] == "1.1"
        assert snap["vix"] == 14.2
        # Must be None, never fabricated 1.0 / 50.0
        assert snap["pcr"] is None
        assert snap["iv_rank"] is None
