"""v0.4.12 — shadow analytics, feature persistence, and migration tests.

Covers: bucket statistics math (hand-computed), get_shadow_analytics
(including the honest insufficient_data state), weekly roll-ups, feature
coverage, the idempotent shadow_outcomes column migration (old rows must
survive untouched), the API endpoints' contract, and the engine's
registration feature-flow (signal_data -> registry -> row kwargs).
"""
import sqlite3
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from db.migrations import Base, ShadowOutcome, ensure_shadow_feature_columns, IST
from db.repository import Repository


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(async_session):
    return Repository(async_session)


def _resolved_row(i: int, strategy="STRAT_A", outcome="SHADOW_TARGET", **kw) -> dict:
    """kwargs for create_shadow_outcome — realistic realtime geometry."""
    base = dict(
        signal_id=f"sig-{i}",
        symbol=kw.pop("symbol", "TRENT"),
        direction=kw.pop("direction", "LONG"),
        strategy=strategy,
        kind=kw.pop("kind", "gate_blocked"),
        never_traded_reason=kw.pop("never_traded_reason", "GATE_BLOCKED"),
        entry_price=100.0,
        stop_loss=95.0,
        target=110.0,
        exit_price=110.0 if outcome == "SHADOW_TARGET" else 95.0,
        outcome=outcome,
        pnl_per_share=10.0 if outcome == "SHADOW_TARGET" else -5.0,
        mfe=10.0 if outcome == "SHADOW_TARGET" else 2.0,
        mae=1.0 if outcome == "SHADOW_TARGET" else 5.0,
        feed_realtime_registered=kw.pop("realtime", True),
        feed_realtime_resolved=kw.pop("realtime", True),
        regime_at_signal=kw.pop("regime", "Bull"),
        vix_at_signal=13.5,
        blocking_gates=["G2"],
        registered_at=(datetime.now(IST) - timedelta(hours=3)).isoformat(),
        resolved_at=(datetime.now(IST) - timedelta(minutes=10 - i)).isoformat(),
    )
    base.update(kw)
    return base


def _model_row(**kw) -> ShadowOutcome:
    """Direct model instance (no DB) for the pure bucket-stats math."""
    return ShadowOutcome(**_resolved_row(0, **kw))


# ────────────────────────────────────────
# Pure bucket statistics (hand-computed)
# ────────────────────────────────────────
class TestBucketStats:
    def test_hand_computed_mixed_bucket(self):
        rows = [
            _model_row(strategy="S", outcome="SHADOW_TARGET", mfe=10.0, mae=1.0),
            _model_row(strategy="S", outcome="SHADOW_TARGET", mfe=6.0, mae=2.0),
            _model_row(strategy="S", outcome="SHADOW_SL", mfe=2.0, mae=5.0),
            _model_row(strategy="S", outcome="SHADOW_TARGET", mfe=8.0, mae=1.0, realtime=False),
        ]
        stats = Repository._shadow_bucket_stats(rows)
        # realtime rows = 3 (the backup-flagged one is excluded): 2W/1L
        assert stats["resolved"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["expired"] == 0
        assert stats["win_rate_pct"] == pytest.approx(66.67)
        assert stats["avg_mfe"] == pytest.approx((10 + 6 + 2) / 3, abs=1e-6)
        assert stats["avg_mae"] == pytest.approx(round((1 + 2 + 5) / 3, 4), abs=1e-6)
        # R-multiples vs risk = |100 - 95| = 5
        assert stats["avg_r_mfe"] == pytest.approx((2.0 + 1.2 + 0.4) / 3, abs=1e-6)
        assert stats["avg_r_mae"] == pytest.approx(round((0.2 + 0.4 + 1.0) / 3, 4), abs=1e-6)
        assert stats["total_rows"] == 4  # honesty: non-realtime still counted here

    def test_zero_risk_geometry_skips_r_multiples(self):
        row = _model_row(strategy="S", entry_price=0.0, stop_loss=0.0)
        stats = Repository._shadow_bucket_stats([row])
        assert stats["avg_r_mfe"] is None
        assert stats["avg_r_mae"] is None

    def test_empty_bucket(self):
        stats = Repository._shadow_bucket_stats([])
        assert stats["resolved"] == 0
        assert stats["win_rate_pct"] == 0.0
        assert stats["avg_mfe"] is None


# ────────────────────────────────────────
# get_shadow_analytics
# ────────────────────────────────────────
class TestShadowAnalytics:
    @pytest.mark.asyncio
    async def test_ok_status_with_buckets(self, repo):
        for i in range(12):
            await repo.create_shadow_outcome(
                **_resolved_row(
                    i,
                    strategy="STRAT_A" if i % 2 == 0 else "STRAT_B",
                    session_class="MORNING" if i % 3 else "LUNCH",
                )
            )
        out = await repo.get_shadow_analytics(group_by="strategy", days=7)
        assert out["status"] == "ok"
        assert out["resolved_in_window"] == 12
        assert set(out["buckets"].keys()) == {"STRAT_A", "STRAT_B"}
        assert out["buckets"]["STRAT_A"]["resolved"] == 6
        assert out["buckets"]["STRAT_A"]["win_rate_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_groups_by_session_column(self, repo):
        for i in range(12):
            await repo.create_shadow_outcome(
                **_resolved_row(i, session_class="MORNING" if i % 2 else "LUNCH")
            )
        out = await repo.get_shadow_analytics(group_by="session", days=7)
        assert out["status"] == "ok"
        assert set(out["buckets"].keys()) == {"MORNING", "LUNCH"}

    @pytest.mark.asyncio
    async def test_insufficient_data_is_honest(self, repo):
        for i in range(5):  # below MIN_RESOLVED = 10
            await repo.create_shadow_outcome(**_resolved_row(i))
        out = await repo.get_shadow_analytics(group_by="strategy", days=7)
        assert out["status"] == "insufficient_data"
        assert out["resolved_in_window"] == 5
        assert out["min_required"] == 10
        assert out["buckets"] == {}  # no flattering percentages

    @pytest.mark.asyncio
    async def test_realtime_only_excludes_backup_rows(self, repo):
        for i in range(12):
            await repo.create_shadow_outcome(
                **_resolved_row(i, realtime=(i % 4 != 0))  # 3 backup rows
            )
        strict = await repo.get_shadow_analytics(group_by="strategy", days=7)
        assert strict["overall"]["total_rows"] == 9
        loose = await repo.get_shadow_analytics(group_by="strategy", days=7, realtime_only=False)
        assert loose["overall"]["total_rows"] == 12

    @pytest.mark.asyncio
    async def test_invalid_group(self, repo):
        out = await repo.get_shadow_analytics(group_by="nonsense")
        assert out["status"] == "invalid_group"
        assert "strategy" in out["allowed_groups"]

    @pytest.mark.asyncio
    async def test_old_rows_outside_window_excluded(self, repo):
        for i in range(11):
            await repo.create_shadow_outcome(**_resolved_row(i))
        # one row resolved 40 days ago — outside the 7-day window
        stale = _resolved_row(99)
        stale["resolved_at"] = (datetime.now(IST) - timedelta(days=40)).isoformat()
        await repo.create_shadow_outcome(**stale)
        out = await repo.get_shadow_analytics(group_by="strategy", days=7)
        assert out["resolved_in_window"] == 11


# ────────────────────────────────────────
# Weekly roll-up
# ────────────────────────────────────────
class TestWeekly:
    @pytest.mark.asyncio
    async def test_week_bucketing(self, repo):
        for i in range(6):
            row = _resolved_row(i)
            row["resolved_at"] = "2026-09-04T10:00:00+05:30"
            await repo.create_shadow_outcome(**row)
        for i in range(4):
            row = _resolved_row(100 + i, outcome="SHADOW_SL")
            row["resolved_at"] = "2026-08-27T10:00:00+05:30"
            await repo.create_shadow_outcome(**row)
        out = await repo.get_shadow_weekly(group_by="strategy", weeks=8)
        assert out["status"] == "ok"
        expected = {
            f"{datetime.strptime(d, '%Y-%m-%d').isocalendar().year}"
            f"-W{datetime.strptime(d, '%Y-%m-%d').isocalendar().week:02d}"
            for d in ("2026-09-04", "2026-08-27")
        }
        assert set(out["weeks"].keys()) == expected
        this_week = out["weeks"][sorted(out["weeks"])[-1]]
        assert this_week["resolved"] == 6
        assert this_week["win_rate_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_empty_weekly_honest(self, repo):
        out = await repo.get_shadow_weekly(group_by="strategy")
        assert out["status"] == "insufficient_data"
        assert out["weeks"] == {}


# ────────────────────────────────────────
# Feature coverage
# ────────────────────────────────────────
class TestFeatureCoverage:
    @pytest.mark.asyncio
    async def test_coverage_counts(self, repo):
        for i in range(6):
            kw = {}
            if i < 4:
                kw = dict(
                    session_class="MORNING",
                    atr=2.0,
                    atr_pct=2.0,
                    vwap_distance_pct=0.5,
                    trend_strength=1.2,
                    htf_trend="up",
                    liquidity_ratio=1.5,
                    features_json='{"schema_version": "v1"}',
                    features_schema_version="v1",
                )
            await repo.create_shadow_outcome(**_resolved_row(i, **kw))
        cov = await repo.get_feature_coverage()
        assert cov["rows_total"] == 6
        assert cov["rows_with_features"] == 4
        assert cov["coverage_pct"] == pytest.approx(66.67)
        assert cov["rows_with_session"] == 4
        assert cov["by_kind"]["gate_blocked"]["total"] == 6
        assert cov["by_kind"]["gate_blocked"]["with_features"] == 4

    @pytest.mark.asyncio
    async def test_coverage_empty_db_never_raises(self, repo):
        cov = await repo.get_feature_coverage()
        assert cov["rows_total"] == 0
        assert cov["coverage_pct"] == 0.0


# ────────────────────────────────────────
# Migration: old rows survive, idempotent
# ────────────────────────────────────────
class TestMigrationPreservesRows:
    def _create_v0411_schema(self, path):
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE shadow_outcomes (
                id TEXT PRIMARY KEY, signal_id TEXT, session_id TEXT,
                symbol TEXT NOT NULL, direction TEXT NOT NULL,
                strategy TEXT NOT NULL, kind TEXT NOT NULL,
                never_traded_reason TEXT,
                entry_price REAL NOT NULL DEFAULT 0, stop_loss REAL NOT NULL DEFAULT 0,
                target REAL NOT NULL DEFAULT 0, exit_price REAL NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL, pnl_per_share REAL NOT NULL DEFAULT 0,
                mfe REAL NOT NULL DEFAULT 0, mae REAL NOT NULL DEFAULT 0,
                feed_realtime_registered BOOLEAN NOT NULL DEFAULT 1,
                feed_realtime_resolved BOOLEAN NOT NULL DEFAULT 1,
                regime_at_signal TEXT, vix_at_signal REAL,
                blocking_gates TEXT NOT NULL DEFAULT '[]',
                registered_at TEXT, resolved_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO shadow_outcomes (id, symbol, direction, strategy, kind,
                outcome, entry_price, created_at)
            VALUES ('legacy-1', 'TRENT', 'LONG', 'MRF', 'gate_blocked',
                'SHADOW_TARGET', 100.0, '2026-09-05T10:00:00')
            """
        )
        conn.commit()
        conn.close()

    def test_migration_adds_columns_and_preserves_row(self, tmp_path):
        db = str(tmp_path / "old.db")
        self._create_v0411_schema(db)
        added = ensure_shadow_feature_columns(db)
        assert len(added) == 9
        assert "features_json" in added and "session_class" in added

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT id, symbol, outcome, entry_price, features_json, session_class "
            "FROM shadow_outcomes WHERE id='legacy-1'"
        ).fetchone()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_outcomes)")}
        conn.close()
        assert row == ("legacy-1", "TRENT", "SHADOW_TARGET", 100.0, None, None)
        assert {"features_json", "session_class", "atr", "htf_trend"}.issubset(cols)

    def test_migration_idempotent(self, tmp_path):
        db = str(tmp_path / "again.db")
        self._create_v0411_schema(db)
        assert len(ensure_shadow_feature_columns(db)) == 9
        assert ensure_shadow_feature_columns(db) == []

    def test_migration_noop_on_fresh_create_all_schema(self, tmp_path):
        # a create_all-built DB already has every column -> nothing to add
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        from db.migrations import Base

        db = str(tmp_path / "fresh.db")

        async def build():
            eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await eng.dispose()

        asyncio.run(build())
        assert ensure_shadow_feature_columns(db) == []


# ────────────────────────────────────────
# API endpoints (handlers called directly)
# ────────────────────────────────────────
class TestAnalyticsEndpoints:
    @pytest.mark.asyncio
    async def test_shadow_endpoint_contract(self, repo):
        from api.routes.analytics import get_shadow_analytics

        for i in range(11):
            await repo.create_shadow_outcome(**_resolved_row(i))
        out = await get_shadow_analytics(
            group_by="strategy", days=7, realtime_only=True,
            username=None, repo=repo,
        )
        assert out["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_group_raises_400(self, repo):
        from api.routes.analytics import get_shadow_analytics

        with pytest.raises(HTTPException) as exc:
            await get_shadow_analytics(
                group_by="nope", days=7, realtime_only=True,
                username=None, repo=repo,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_weekly_endpoint(self, repo):
        from api.routes.analytics import get_shadow_weekly

        out = await get_shadow_weekly(
            group_by="strategy", weeks=4, username=None, repo=repo
        )
        assert out["status"] in ("ok", "insufficient_data")

    @pytest.mark.asyncio
    async def test_coverage_endpoint(self, repo):
        from api.routes.analytics import get_feature_coverage

        out = await get_feature_coverage(username=None, repo=repo)
        assert "coverage_pct" in out


# ────────────────────────────────────────
# Engine feature flow (registration stub — never constructs the engine)
# ────────────────────────────────────────
class TestEngineFeatureFlow:
    def _stub(self, enabled=True):
        from core.engine import UltraBotEngine

        stub = types.SimpleNamespace()
        stub._shadow_signals = {}
        stub._shadow_recorder_enabled = enabled
        stub._shadow_realtime = lambda: True
        stub._register_shadow = types.MethodType(UltraBotEngine._register_shadow, stub)
        return stub

    def test_features_ride_from_signal_data(self):
        stub = self._stub()
        feats = {"schema_version": "v1", "atr": 2.0, "session_class": "MORNING"}
        stub._register_shadow(
            signal_id="s1", symbol="TRENT", direction="LONG", strategy="MRF",
            entry_price=100.0, stop_loss=95.0, target=110.0,
            kind="gate_blocked", blocking_gates=["G2"],
            signal_data={"features_snapshot": feats},
        )
        assert stub._shadow_signals["s1"]["features"] is feats  # by reference

    def test_no_signal_data_features_none(self):
        stub = self._stub()
        stub._register_shadow(
            signal_id="s2", symbol="TRENT", direction="LONG", strategy="MRF",
            entry_price=100.0, stop_loss=95.0, target=110.0, kind="never_traded",
        )
        assert stub._shadow_signals["s2"]["features"] is None

    def test_garbage_signal_data_never_raises(self):
        stub = self._stub()
        stub._register_shadow(
            signal_id="s3", symbol="TRENT", direction="LONG", strategy="MRF",
            entry_price=100.0, stop_loss=95.0, target=110.0, kind="never_traded",
            signal_data="not-a-dict",
        )
        assert stub._shadow_signals["s3"]["features"] is None

    def test_kill_switch_stops_registration(self):
        stub = self._stub(enabled=False)
        stub._register_shadow(
            signal_id="s4", symbol="TRENT", direction="LONG", strategy="MRF",
            entry_price=100.0, stop_loss=95.0, target=110.0, kind="never_traded",
        )
        assert stub._shadow_signals == {}
