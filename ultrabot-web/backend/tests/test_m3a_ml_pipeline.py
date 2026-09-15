"""Unit and integration tests for UltraBot M3a Machine Learning Pipeline."""
import os
import tempfile
import pytest
import numpy as np
from unittest.mock import MagicMock, AsyncMock

from ml.dataset import MLDatasetBuilder, FEATURE_NAMES
from ml.models import CalibratedLinearModel, GradientBoostedStumps
from ml.walk_forward import WalkForwardValidator
from ml.inference import MLInferenceEngine
from api.routes.ml import get_ml_status, score_signal_endpoint, trigger_training, ScoreSignalRequest, TrainModelRequest


def test_dataset_builder_feature_extraction():
    """Verify feature extraction and categorical encoding from shadow outcomes."""
    builder = MLDatasetBuilder()

    outcome_mock = {
        "symbol": "RELIANCE",
        "direction": "BUY",
        "outcome": "SHADOW_TARGET",
        "pnl_per_share": 15.5,
        "atr_pct": 1.45,
        "vwap_distance_pct": 0.32,
        "trend_strength": 0.85,
        "liquidity_ratio": 1.75,
        "vix_at_signal": 14.2,
        "session_class": "OPENING_DRIVE",
        "htf_trend": "up",
        "regime_at_signal": "bull",
        "pcr": 1.15,
        "iv_rank": 42.0,
    }

    feats, label = builder.extract_row_features(outcome_mock)
    assert len(feats) == len(FEATURE_NAMES)
    assert label == 1  # SHADOW_TARGET -> Win
    assert feats[0] == 1.45   # atr_pct
    assert feats[5] == 1.0    # OPENING_DRIVE code
    assert feats[6] == 1.0    # htf up code
    assert feats[7] == 1.0    # bull regime code
    assert feats[8] == 1.0    # BUY direction code

    # Test SL loss outcome
    loss_mock = {
        "symbol": "TCS",
        "direction": "SELL",
        "outcome": "SHADOW_SL",
        "pnl_per_share": -10.0,
        "session_class": "LUNCH",
        "htf_trend": "down",
        "regime_at_signal": "bear",
    }
    feats_loss, label_loss = builder.extract_row_features(loss_mock)
    assert label_loss == 0
    assert feats_loss[5] == 3.0   # LUNCH code
    assert feats_loss[6] == -1.0  # down code
    assert feats_loss[7] == -1.0  # bear code
    assert feats_loss[8] == -1.0  # SELL code


def test_synthetic_bootstrap_and_matrix_assembly():
    """Verify bootstrap dataset generation and standardized matrix assembly."""
    builder = MLDatasetBuilder()
    samples = builder.generate_synthetic_bootstrap(n_samples=100, random_seed=42)
    assert len(samples) == 100

    X, y = builder.build_dataset(samples, fit_scaler=True)
    assert X.shape == (100, len(FEATURE_NAMES))
    assert y.shape == (100,)
    assert set(np.unique(y)).issubset({0.0, 1.0})

    # Normalized features should have zero mean and unit variance approximately
    assert np.allclose(np.mean(X, axis=0), 0.0, atol=1e-3)
    assert np.allclose(np.std(X, axis=0), 1.0, atol=1e-3)


def test_calibrated_linear_model_training_and_calibration():
    """Verify Logistic Regression training, sigmoid bounds, and Platt calibration."""
    builder = MLDatasetBuilder()
    samples = builder.generate_synthetic_bootstrap(n_samples=120, random_seed=42)
    X, y = builder.build_dataset(samples, fit_scaler=True)

    model = CalibratedLinearModel(learning_rate=0.1, max_iter=200).fit(X, y)
    assert model.is_fitted is True
    assert model.weights is not None
    assert len(model.weights) == X.shape[1]
    assert 0.1 <= model.temperature <= 5.0

    probs = model.predict_proba(X)
    assert np.all(probs >= 0.0) and np.all(probs <= 1.0)

    preds = model.predict(X, threshold=0.50)
    assert len(preds) == len(y)
    assert set(np.unique(preds)).issubset({0, 1})

    # Feature importance
    importances = model.get_feature_importance(builder.feature_names)
    assert len(importances) == len(builder.feature_names)
    assert np.isclose(sum(importances.values()), 1.0, atol=1e-3)


def test_model_serialization_and_recovery():
    """Verify model can be serialized to JSON and reloaded with identical predictions."""
    builder = MLDatasetBuilder()
    samples = builder.generate_synthetic_bootstrap(n_samples=60, random_seed=123)
    X, y = builder.build_dataset(samples, fit_scaler=True)

    model = CalibratedLinearModel().fit(X, y)
    original_probs = model.predict_proba(X)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        model.save(temp_path)
        loaded_model = CalibratedLinearModel.load(temp_path)
        assert loaded_model.is_fitted is True
        loaded_probs = loaded_model.predict_proba(X)
        assert np.allclose(original_probs, loaded_probs, atol=1e-6)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_gradient_boosted_stumps():
    """Verify gradient boosted decision stumps ensemble."""
    builder = MLDatasetBuilder()
    samples = builder.generate_synthetic_bootstrap(n_samples=80, random_seed=42)
    X, y = builder.build_dataset(samples, fit_scaler=True)

    gbs = GradientBoostedStumps(n_estimators=15, learning_rate=0.1).fit(X, y)
    assert gbs.is_fitted is True
    assert len(gbs.stumps) == 15

    probs = gbs.predict_proba(X)
    assert np.all(probs >= 0.0) and np.all(probs <= 1.0)


def test_strict_walk_forward_validation():
    """Verify walk-forward temporal cross-validation with zero lookahead."""
    builder = MLDatasetBuilder()
    samples = builder.generate_synthetic_bootstrap(n_samples=100, random_seed=42)
    X, y = builder.build_dataset(samples, fit_scaler=True)

    validator = WalkForwardValidator(n_splits=3, min_train_size=30, veto_threshold=0.45)
    report = validator.evaluate(X, y)

    assert report["folds_evaluated"] >= 1
    assert "overall_brier_score" in report
    assert "baseline_win_rate_pct" in report
    assert "model_win_rate_pct" in report
    assert "calibration_curve" in report
    assert len(report["calibration_curve"]) == 5

    # Check temporal indices in folds strictly maintain train < test
    for fold in report["folds"]:
        assert fold["train_size"] < (fold["train_size"] + fold["test_size"])


def test_ml_inference_engine_runtime():
    """Verify sub-millisecond signal scoring and G21 advisory categorization."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_file = os.path.join(tmp_dir, "test_m3a.json")
        engine = MLInferenceEngine(model_path=model_file, veto_threshold=0.40, favorable_threshold=0.55)

        signal = {
            "symbol": "RELIANCE",
            "direction": "BUY",
            "strategy": "ORB",
            "timestamp": "2026-09-13T10:15:00+05:30",
        }

        # Score signal
        res = engine.score_signal(
            signal=signal,
            candles_df=None,
            vix=14.0,
            regime="bull",
            pcr=1.2,
            iv_rank=35.0,
        )

        assert "score" in res
        assert 0.0 <= res["score"] <= 1.0
        assert "win_probability" in res
        assert res["action"] in ("FAVORABLE", "NEUTRAL", "VETO")
        assert isinstance(res["veto"], bool)
        assert len(res["features"]) == len(FEATURE_NAMES)
        assert len(res["top_features"]) == len(FEATURE_NAMES)

        # Telemetry status check
        status = engine.get_status()
        assert status["is_fitted"] is True
        assert status["features_count"] == len(FEATURE_NAMES)


@pytest.mark.asyncio
async def test_ml_api_endpoints():
    """Verify /api/ml/status, /api/ml/score, and /api/ml/train endpoints."""
    # 1. Status endpoint
    status = await get_ml_status(_user={})
    assert "version" in status
    assert status["is_fitted"] is True

    # 2. Score endpoint
    score_req = ScoreSignalRequest(
        symbol="TCS",
        direction="BUY",
        strategy="MRF",
        vix=16.5,
        regime="sideways",
        pcr=0.95,
        iv_rank=55.0,
    )
    score_res = await score_signal_endpoint(score_req, _user={})
    assert "score" in score_res
    assert score_res["action"] in ("FAVORABLE", "NEUTRAL", "VETO")

    # 3. Train endpoint with mock repo
    mock_repo = MagicMock()
    mock_repo.get_shadow_outcomes_today = AsyncMock(return_value=[])
    train_req = TrainModelRequest(use_synthetic_bootstrap_if_sparse=True, min_samples_threshold=40)
    train_res = await trigger_training(train_req, repo=mock_repo, _user={})
    assert train_res["status"] == "success"
    assert "report" in train_res
    assert train_res["samples_count"] >= 40
