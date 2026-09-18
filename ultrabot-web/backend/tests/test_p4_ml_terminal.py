"""Unit tests for Phase P4 Machine Learning Terminal and Shadow Advisor."""
import pytest
from unittest.mock import AsyncMock
from ml.inference import MLInferenceEngine, get_inference_engine
from api.routes.ml import get_ml_metrics, get_ml_evaluations


def test_score_signal_pipeline_steps_and_contributions():
    """Verify that score_signal generates 5 pipeline steps and signed feature contributions."""
    engine = MLInferenceEngine()
    signal = {
        "symbol": "TCS",
        "direction": "LONG",
        "strategy": "ORB",
        "confidence": 0.85,
        "atr": 12.0,
        "entry_price": 3800.0,
        "stop_loss": 3760.0,
    }

    res = engine.score_signal(
        signal=signal,
        vix=14.5,
        regime="trending_bull",
        pcr=1.2,
        iv_rank=35.0,
    )

    assert "score" in res
    assert "win_probability" in res
    assert "action" in res
    assert "pipeline_steps" in res
    assert "contributions" in res

    # Check that 5 pipeline gates exist
    steps = res["pipeline_steps"]
    assert len(steps) == 5
    gate_names = [s["name"] for s in steps]
    assert "Signal Ingestion" in gate_names
    assert "Regime & Volatility" in gate_names
    assert "Option Chain PCR" in gate_names
    assert "Feature Normalization" in gate_names
    assert "Calibrated Verdict" in gate_names

    # Check feature contributions
    contributions = res["contributions"]
    assert isinstance(contributions, dict)
    assert len(contributions) > 0
    assert "atr_pct" in contributions
    assert "vix" in contributions
    assert "pcr" in contributions


def test_recent_evaluations_storage_and_filtering():
    """Verify evaluation buffer storage, limit slicing, and action filtering."""
    engine = MLInferenceEngine()
    engine.recent_evaluations.clear()

    # Score several signals
    for i in range(10):
        engine.score_signal(
            signal={
                "symbol": f"SYM_{i}",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "strategy": "EMA_CROSSOVER",
                "confidence": 0.4 + (i * 0.05),
            },
            vix=15.0 + i,
            regime="sideways",
        )

    all_evals = engine.get_recent_evaluations(limit=20)
    assert len(all_evals) == 10

    # Limit filter
    limit_evals = engine.get_recent_evaluations(limit=4)
    assert len(limit_evals) == 4

    # Filter by action if any exists
    first_action = all_evals[0]["action"]
    filtered = engine.get_recent_evaluations(action=first_action)
    for ev in filtered:
        assert ev["action"] == first_action


def test_scorecard_metrics_calculation():
    """Verify scorecard metric calculations: unvalidated contract vs validated contract."""
    engine = MLInferenceEngine()
    # 1. Honest unvalidated contract on boot without report
    metrics = engine.get_scorecard_metrics()

    assert "total_evaluations" in metrics
    assert "edge_uplift_pct" in metrics
    assert "brier_score" in metrics
    assert "veto_savings_estimate" in metrics
    assert "drift_status" in metrics
    assert metrics["drift_status"] in ("HEALTHY", "MODERATE", "HIGH", "stable")
    assert "calibration_curve" in metrics
    assert metrics["has_validation_data"] is False
    assert metrics["calibration_curve"] == []

    # 2. Validated contract when empirical validation report is loaded
    engine.last_validation_report = {
        "overall_uplift_pct": 12.5,
        "overall_brier_score": 0.165,
        "baseline_win_rate_pct": 51.2,
        "model_win_rate_pct": 63.7,
        "overall_roc_auc": 0.65,
        "calibration_curve": [
            {"bin_range": "0.4-0.5", "predicted_prob": 0.45, "empirical_frequency": 0.44}
        ],
    }
    val_metrics = engine.get_scorecard_metrics()
    assert val_metrics["has_validation_data"] is True
    assert val_metrics["edge_uplift_pct"] == 12.5
    assert len(val_metrics["calibration_curve"]) > 0

    # Check decile structure
    decile = val_metrics["calibration_curve"][0]
    assert "bin" in decile
    assert "predicted_win_rate" in decile
    assert "actual_win_rate" in decile


@pytest.mark.asyncio
async def test_ml_api_metrics_and_evaluations_endpoints():
    """Verify FastAPI route handlers for /api/ml/metrics and /api/ml/evaluations."""
    metrics_res = await get_ml_metrics(_user={"id": "admin"})
    assert "total_evaluations" in metrics_res
    assert "brier_score" in metrics_res
    assert "calibration_curve" in metrics_res

    evals_res = await get_ml_evaluations(limit=10, _user={"id": "admin"})
    assert "count" in evals_res
    assert "evaluations" in evals_res
    assert isinstance(evals_res["evaluations"], list)

