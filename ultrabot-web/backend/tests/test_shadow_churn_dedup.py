"""#19 shadow churn dedup regression tests.

Mirrors the 2026-09-24 forensics: 3x identical-geometry shadow signals fired
~90s apart (scan cadence), all resolving at the same second. Raw counts said
n=3; the verdict gate must see effective_n=1.

Covers all three layers:
  * core.shadow_dedup.cluster_effective_setups — pure clustering logic
  * Repository.compute_shadow_signal_stats — effective_n/resolved_raw/
    churn_ratio/effective_win_rate reported alongside raw counts
  * core.strategy_verdict.evaluate_strategy_verdicts — MIN_SAMPLE consumes
    effective_n, not raw resolved
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from core.shadow_dedup import cluster_effective_setups, setup_key
from core.strategy_verdict import MIN_SAMPLE, evaluate_strategy_verdicts
from db.migrations import Base
from db.repository import Repository


@pytest_asyncio.fixture
async def async_session():
    """In-memory SQLite async session for repository tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


class _Row:
    """Minimal stand-in for a resolved Signal row."""

    def __init__(self, strategy, symbol, direction, entry, sl, target, status, created_at):
        self.strategy = strategy
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry
        self.stop_loss = sl
        self.target = target
        self.status = status
        self.created_at = created_at


# --- setup_key -------------------------------------------------------------

def test_setup_key_rounds_geometry():
    """Float jitter between scan cycles must not split one setup."""
    a = setup_key("VR", "ASHOKLEY", "SELL", 158.8300001, 160.10, 155.40)
    b = setup_key("vr", "ashokley", "sell", 158.8299999, 160.0999, 155.4001)
    assert a == b


def test_setup_key_distinguishes_different_geometry():
    a = setup_key("VR", "ASHOKLEY", "SELL", 158.83, 160.10, 155.40)
    b = setup_key("VR", "ASHOKLEY", "SELL", 159.00, 160.10, 155.40)
    assert a != b


# --- cluster_effective_setups ---------------------------------------------

def test_three_identical_signals_ninety_seconds_apart_is_one_setup():
    """The 09-24 forensics fixture: 3x identical, ~90s cadence, same resolve."""
    t0 = datetime(2026, 9, 24, 13, 42, 33)
    rows = [
        _Row("VR", "ASHOKLEY", "SELL", 158.83, 160.10, 155.40, "SHADOW_SL",
             t0 + timedelta(seconds=90 * i))
        for i in range(3)
    ]
    out = cluster_effective_setups(rows)
    assert out["VR"]["effective_n"] == 1
    assert out["VR"]["effective_losses"] == 1


def test_same_setup_resolved_later_is_a_new_setup():
    """A re-fire hours later is a genuinely new sample, not churn."""
    t0 = datetime(2026, 9, 24, 10, 0, 0)
    rows = [
        _Row("VR", "X", "BUY", 100.0, 98.0, 104.0, "SHADOW_TARGET", t0),
        _Row("VR", "X", "BUY", 100.0, 98.0, 104.0, "SHADOW_TARGET",
             t0 + timedelta(hours=3)),
    ]
    out = cluster_effective_setups(rows)
    assert out["VR"]["effective_n"] == 2
    assert out["VR"]["effective_wins"] == 2


def test_different_geometry_is_never_clustered():
    t0 = datetime(2026, 9, 24, 10, 0, 0)
    rows = [
        _Row("VR", "X", "BUY", 100.0, 98.0, 104.0, "SHADOW_SL", t0),
        _Row("VR", "X", "BUY", 101.0, 98.0, 104.0, "SHADOW_SL", t0),
    ]
    out = cluster_effective_setups(rows)
    assert out["VR"]["effective_n"] == 2


def test_unresolved_rows_are_ignored():
    rows = [
        _Row("VR", "X", "BUY", 100.0, 98.0, 104.0, "SHADOW", datetime(2026, 9, 24, 10, 0, 0)),
        _Row("VR", "X", "BUY", 100.0, 98.0, 104.0, "SHADOW_SL", datetime(2026, 9, 24, 10, 1, 0)),
    ]
    out = cluster_effective_setups(rows)
    assert out["VR"]["effective_n"] == 1


# --- Repository.compute_shadow_signal_stats --------------------------------

@pytest.mark.asyncio
async def test_stats_report_raw_and_effective_side_by_side(async_session):
    """3 identical + 1 distinct setup: raw resolved=4, effective_n=2."""
    repo = Repository(async_session)
    t0 = datetime(2026, 9, 24, 13, 42, 33)
    # churn: 3 identical ASHOKLEY SELLs, ~90s apart (matches the forensics)
    for i in range(3):
        await repo.create_signal(
            symbol="ASHOKLEY",
            direction="SELL",
            strategy="VR",
            confidence=0.7,
            entry_price=158.83,
            stop_loss=160.10,
            target=155.40,
            status="SHADOW",
            created_at=(t0 + timedelta(seconds=90 * i)).isoformat(),
        )
    # a genuinely different setup (different symbol) that resolves as a win
    await repo.create_signal(
        symbol="AXISBANK",
        direction="BUY",
        strategy="VR",
        confidence=0.7,
        entry_price=1178.0,
        stop_loss=1170.0,
        target=1194.0,
        status="SHADOW",
        created_at=(t0 + timedelta(minutes=30)).isoformat(),
    )

    # resolve all four rows the way the engine does (same second, per forensics)
    resolve_at = t0 + timedelta(hours=1, minutes=23)
    from db.migrations import Signal
    from sqlalchemy import select
    rows = (await async_session.execute(select(Signal))).scalars().all()
    for row in rows:
        await repo.update_signal(
            row.id,
            status="SHADOW_SL" if row.symbol == "ASHOKLEY" else "SHADOW_TARGET",
            updated_at=resolve_at.isoformat(),
        )

    stats = await repo.compute_shadow_signal_stats()
    vr = stats["VR"]
    assert vr["resolved_raw"] == 4
    assert vr["effective_n"] == 2
    assert vr["churn_ratio"] == 2.0
    assert vr["effective_losses"] == 1
    assert vr["effective_wins"] == 1
    # raw win-rate counts every churn row; effective rate counts setups
    # (ASHOKLEY cluster = 1 loss, AXISBANK = 1 win -> 50%)
    assert vr["signal_win_rate"] == 25.0
    assert vr["effective_win_rate"] == 50.0


@pytest.mark.asyncio
async def test_stats_survive_rows_with_model_default_created_at(async_session):
    """A row created without an explicit timestamp gets the model default
    and is still clusterable — the stats call must not crash either way."""
    repo = Repository(async_session)
    await repo.create_signal(
        symbol="X", direction="BUY", strategy="VR",
        entry_price=100.0, stop_loss=98.0, target=104.0, status="SHADOW_SL",
    )
    stats = await repo.compute_shadow_signal_stats()
    assert stats["VR"]["resolved_raw"] == 1
    assert stats["VR"]["effective_n"] == 1
    assert stats["VR"]["churn_ratio"] == 1.0


# --- verdict gate ----------------------------------------------------------

def test_verdict_gate_consumes_effective_n_not_raw():
    """Raw resolved >= MIN_SAMPLE but effective_n < MIN_SAMPLE -> KEEP_COLLECTING."""
    stats = {
        "FAKE": {
            "total_signals": 400,
            "resolved": 300,          # churn-inflated raw
            "effective_n": 90,        # below the gate
            "churn_ratio": 3.33,
            "effective_win_rate": 55.0,
            "wins": 120,
            "losses": 180,
            "expired": 0,
            "pending": 100,
            "signal_win_rate": 40.0,
        }
    }
    verdicts = evaluate_strategy_verdicts(stats)
    assert verdicts[0]["verdict"] == "KEEP_COLLECTING"
    assert verdicts[0]["effective_n"] == 90
    assert verdicts[0]["resolved_raw"] == 300


def test_verdict_passes_when_effective_n_clears_gate():
    stats = {
        "FAKE": {
            "total_signals": 400,
            "resolved": 300,
            "effective_n": 150,
            "churn_ratio": 2.0,
            "effective_win_rate": 55.0,
            "wins": 180,
            "losses": 120,
            "expired": 0,
            "pending": 100,
            "signal_win_rate": 60.0,
        }
    }
    verdicts = evaluate_strategy_verdicts(stats)
    assert verdicts[0]["verdict"] in ("PROMOTE_CANDIDATE", "BORDERLINE", "RETIRE_CANDIDATE")
    assert verdicts[0]["effective_n"] == 150
    # decision win-rate is the deduped one, not the churn-inflated raw
    assert verdicts[0]["effective_win_rate"] == 55.0


def test_verdict_falls_back_to_raw_when_effective_missing():
    """Callers predating effective_n still get verdicts (back-compat)."""
    stats = {
        "FAKE": {
            "total_signals": 150,
            "resolved": 120,
            "wins": 90,
            "losses": 30,
            "expired": 0,
            "pending": 30,
            "signal_win_rate": 75.0,
        }
    }
    verdicts = evaluate_strategy_verdicts(stats)
    assert verdicts[0]["verdict"] == "PROMOTE_CANDIDATE"
    assert verdicts[0]["effective_n"] == 120
