"""Tests for the P3 strategy verdict engine.

The verdict rules are deterministic: sample size gates every verdict,
breakevens are fee-adjusted (measured for v2, derived from avg RR otherwise),
and MRF's economics bar is surfaced explicitly.
"""

import pytest

from core.strategy_verdict import (
    DEFAULT_BREAKEVEN,
    FEE_ADJUSTED_BREAKEVEN,
    MIN_SAMPLE,
    evaluate_strategy_verdicts,
)


def _stat(total, resolved, wins, losses, wr, expired=0, pending=0, rr=None):
    return {
        "total_signals": total,
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "pending": pending,
        "signal_win_rate": wr,
        "avg_risk_reward": rr,
    }


def test_no_data_when_nothing_recorded():
    out = evaluate_strategy_verdicts({"GapFill": _stat(0, 0, 0, 0, 0.0)})
    assert out[0]["verdict"] == "NO_DATA"


def test_keep_collecting_below_min_sample():
    """99 resolved → still collecting; 100 unlocks verdicts."""
    out = evaluate_strategy_verdicts({
        "GapFill": _stat(120, 99, 60, 39, 60.6),
    })
    assert out[0]["verdict"] == "KEEP_COLLECTING"
    assert str(MIN_SAMPLE) in out[0]["rationale"]

    out2 = evaluate_strategy_verdicts({
        "GapFill": _stat(130, 100, 60, 40, 60.0),
    })
    assert out2[0]["verdict"] != "KEEP_COLLECTING"


def test_promote_candidate_clears_breakeven_with_margin():
    # GapFill has no measured breakeven; avg RR 1.0 → BE = 50%
    out = evaluate_strategy_verdicts({
        "GapFill": _stat(120, 110, 62, 48, 56.4, rr=1.0),
    })
    v = out[0]
    assert v["verdict"] == "PROMOTE_CANDIDATE"
    assert v["breakeven_wr"] == 50.0
    assert v["signal_win_rate"] == 56.4


def test_retire_candidate_falls_below_breakeven():
    out = evaluate_strategy_verdicts({
        "GapFill": _stat(120, 110, 40, 70, 36.4, rr=1.0),  # BE 50 − 3 → retire
    })
    assert out[0]["verdict"] == "RETIRE_CANDIDATE"


def test_borderline_within_margin_of_breakeven():
    # 51% vs BE 50% — inside ±3pp → not decisive
    out = evaluate_strategy_verdicts({
        "GapFill": _stat(120, 110, 56, 54, 50.9, rr=1.0),
    })
    assert out[0]["verdict"] == "BORDERLINE"


def test_measured_breakevens_used_for_v2_names():
    """ORB's measured 36.8% beats the RR-derived default."""
    out = evaluate_strategy_verdicts({
        "ORB": _stat(120, 110, 45, 65, 40.9, rr=1.0),  # BE=36.8 (not 50)
    })
    v = out[0]
    assert v["breakeven_wr"] == FEE_ADJUSTED_BREAKEVEN["ORB"]
    # 40.9 ≥ 36.8 + 3 → promote-grade for a live strategy
    assert v["verdict"] == "PROMOTE_CANDIDATE"


def test_mrf_economics_note_present():
    out = evaluate_strategy_verdicts(
        {"MRF": _stat(120, 110, 50, 60, 45.5)},
        live_strategies=["ORB", "MRF"],
    )
    mrf = next(v for v in out if v["strategy"] == "MRF")
    assert mrf["is_live"] is True
    assert "50.2%" in mrf.get("economics_note", "")


def test_default_breakeven_without_rr():
    out = evaluate_strategy_verdicts({
        "TrendExhaustion": _stat(120, 110, 60, 50, 54.5),  # no rr → default
    })
    assert out[0]["breakeven_wr"] == DEFAULT_BREAKEVEN


def test_verdicts_sorted_promote_first():
    out = evaluate_strategy_verdicts({
        "Zebra": _stat(0, 0, 0, 0, 0.0),
        "Alpha": _stat(120, 110, 70, 40, 63.6, rr=1.0),   # promote
        "Mid": _stat(120, 110, 56, 54, 50.9, rr=1.0),     # borderline
    })
    assert [v["strategy"] for v in out] == ["Alpha", "Mid", "Zebra"]


def test_empty_stats_returns_empty_list():
    assert evaluate_strategy_verdicts({}) == []
    assert evaluate_strategy_verdicts(None) == []


# ─────────────────────────────────────────────
# API endpoint
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verdicts_endpoint_requires_auth(client=None):
    import pytest_asyncio  # noqa: F401
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=__import__("app").app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategies/verdicts")
        assert resp.status_code == 401
