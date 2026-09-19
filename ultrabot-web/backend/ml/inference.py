"""Runtime Sub-Millisecond ML Inference Engine for UltraBot M3a.

Provides real-time scoring of strategy signals using point-in-time features,
calibrated probability estimates, G21_ML advisory outputs, and explainable AI.
"""
from collections import deque
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo
import numpy as np

from ml.dataset import MLDatasetBuilder
from ml.models import CalibratedLinearModel
from ml.walk_forward import WalkForwardValidator
from shadow.features import compute_feature_snapshot

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "persist" / "models"
DEFAULT_MODEL_FILE = DEFAULT_MODEL_DIR / "m3a_model.json"


class MLInferenceEngine:
    """Production inference engine for real-time signal scoring, explainability, and G21 advisory."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        veto_threshold: float = 0.40,
        favorable_threshold: float = 0.55,
    ):
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_FILE
        self.veto_threshold = veto_threshold
        self.favorable_threshold = favorable_threshold
        self.builder = MLDatasetBuilder()
        self.model = CalibratedLinearModel()
        self.last_validation_report: Optional[Dict[str, Any]] = None
        self.version = "m3a-v1.0"
        self.recent_evaluations: deque = deque(maxlen=100)

        # Attempt to load saved model if exists, otherwise initialize baseline
        self._load_or_initialize()

    def _load_or_initialize(self) -> None:
        """Load trained weights from disk or bootstrap an initial prior model."""
        if self.model_path.exists():
            try:
                self.model = CalibratedLinearModel.load(str(self.model_path))
                logger.info("Loaded M3a ML model from %s", self.model_path)
                if getattr(self.model, "means", None) is not None:
                    self.builder.means = self.model.means
                    self.builder.stds = self.model.stds
                else:
                    bootstrap_samples = self.builder.generate_synthetic_bootstrap(n_samples=150)
                    self.builder.build_dataset(bootstrap_samples, fit_scaler=True)
                    self.model.means = self.builder.means
                    self.model.stds = self.builder.stds

                val_path = self.model_path.parent / "m3a_validation_report.json"
                if val_path.exists():
                    try:
                        with open(val_path, "r", encoding="utf-8") as vf:
                            self.last_validation_report = json.load(vf)
                    except Exception as e:
                        logger.warning("Failed to load validation report from %s: %s", val_path, e)
                        self.last_validation_report = None
                else:
                    self.last_validation_report = None

                return
            except Exception as e:
                logger.warning("Could not load model from %s: %s; initializing prior.", self.model_path, e)

        # Initialize with synthetic bootstrap prior ONLY for weights if no model on disk
        self.train_on_bootstrap(n_samples=150, save_to_disk=False)
        self.last_validation_report = None

    def _seed_initial_evaluations_if_empty(self) -> None:
        """No synthetic demo signals injected into production evaluation cache."""
        return

    def train_on_bootstrap(self, n_samples: int = 150, save_to_disk: bool = True) -> Dict[str, Any]:
        """Pre-train the model on synthetic bootstrap samples representing market distributions."""
        bootstrap_samples = self.builder.generate_synthetic_bootstrap(n_samples=n_samples)
        return self.train(bootstrap_samples, save_to_disk=save_to_disk)

    def train(self, outcomes: List[Any], save_to_disk: bool = True) -> Dict[str, Any]:
        """Train model, run walk-forward validation, and optionally save to disk."""
        if not outcomes:
            raise ValueError("No outcomes provided for training")

        X, y = self.builder.build_dataset(outcomes, fit_scaler=True)
        if len(X) == 0:
            raise ValueError("Failed to extract features from outcomes")

        # 1. Walk-forward validation
        validator = WalkForwardValidator(n_splits=4, min_train_size=max(20, int(len(X) * 0.3)), veto_threshold=self.veto_threshold)
        val_report = validator.evaluate(X, y)
        self.last_validation_report = val_report

        # 2. Final fit on all available data
        self.model.fit(X, y)
        self.model.means = self.builder.means
        self.model.stds = self.builder.stds
        self.model.trained_at = datetime.now(IST).isoformat()
        self.model.samples_count = len(X)
        self.model.feature_names = list(self.builder.feature_names)
        synthetic_count = sum(
            1 for o in outcomes
            if (isinstance(o, dict) and o.get("is_synthetic")) or getattr(o, "is_synthetic", False)
        )
        self.model.synthetic_share = round(float(synthetic_count / max(1, len(outcomes))), 4)

        # 3. Persist model
        if save_to_disk:
            self.save()

        logger.info(
            "Trained M3a model: Base WR %.1f%% -> Model WR %.1f%% (Uplift %+.1f%%, Brier %.4f)",
            val_report["baseline_win_rate_pct"],
            val_report["model_win_rate_pct"],
            val_report["overall_uplift_pct"],
            val_report["overall_brier_score"],
        )

        return val_report

    def score_signal(
        self,
        signal: Dict[str, Any],
        candles_df: Optional[Any] = None,
        vix: float = 15.0,
        regime: str = "sideways",
        pcr: float = 1.0,
        iv_rank: float = 50.0,
    ) -> Dict[str, Any]:
        """Score a live signal and return calibrated probability, rating, and feature contributions."""
        # 1. Extract point-in-time features from candle frame or signal snapshots
        now = signal.get("timestamp")
        df_to_use = candles_df if candles_df is not None else (signal.get("candles_df") or signal.get("_df_candles"))
        if df_to_use is not None:
            pit_snapshot = compute_feature_snapshot(df_to_use, now=now)
        elif signal.get("features_snapshot"):
            pit_snapshot = signal["features_snapshot"]
        elif signal.get("features"):
            pit_snapshot = signal["features"]
        else:
            pit_snapshot = compute_feature_snapshot(None, now=now)

        # 2. Build complete feature dictionary
        row_dict = {
            **pit_snapshot,
            "vix_at_signal": vix,
            "regime_at_signal": regime,
            "direction": signal.get("direction", "BUY"),
            "pcr": pcr,
            "iv_rank": iv_rank,
            "outcome": "UNKNOWN",
        }

        # 3. Transform to normalized feature array
        raw_feats, _ = self.builder.extract_row_features(row_dict)
        X_norm = self.builder.transform(raw_feats).reshape(1, -1)

        # 4. Predict calibrated probability
        prob = float(self.model.predict_proba(X_norm)[0])
        score = round(prob, 4)

        # 5. Determine action classification
        if score >= self.favorable_threshold:
            action = "FAVORABLE"
            veto = False
        elif score < self.veto_threshold:
            action = "VETO"
            veto = True
        else:
            action = "NEUTRAL"
            veto = False

        # 6. Feature importances & attribution waterfall
        importances = self.model.get_feature_importance(self.builder.feature_names)

        # Signed attribution weights
        w = getattr(self.model, "weights", None)
        contributions = {}
        if w is not None and len(w) == len(self.builder.feature_names):
            signed_impacts = w * X_norm[0]
            total_abs = np.sum(np.abs(signed_impacts)) + 1e-6
            deviation = score - 0.50
            for i, name in enumerate(self.builder.feature_names):
                pct_contrib = round(float((signed_impacts[i] / total_abs) * deviation * 100.0), 1)
                contributions[name] = pct_contrib
        else:
            for name in self.builder.feature_names:
                contributions[name] = 0.0

        # 7. Pipeline Stepper Progress
        sym = signal.get("symbol", "NIFTY")
        strat = signal.get("strategy", "ORB")
        dir_str = signal.get("direction", "BUY")

        pipeline_steps = [
            {
                "id": "ingestion",
                "name": "Signal Ingestion",
                "status": "pass",
                "summary": f"{strat} {dir_str} on {sym}",
            },
            {
                "id": "regime",
                "name": "Regime & Volatility",
                "status": "warning" if vix > 22.0 else "pass",
                "summary": f"VIX {vix:.1f} • {regime.capitalize()} Regime",
            },
            {
                "id": "fno",
                "name": "Option Chain PCR",
                "status": "pass",
                "summary": f"PCR {pcr:.2f} • IVR {iv_rank:.0f}%",
            },
            {
                "id": "scaling",
                "name": "Feature Normalization",
                "status": "pass",
                "summary": f"{len(self.builder.feature_names)} normalized factors",
            },
            {
                "id": "verdict",
                "name": "Calibrated Verdict",
                "status": "favorable" if action == "FAVORABLE" else ("veto" if veto else "neutral"),
                "summary": f"{round(score * 100.0, 1)}% Win Probability ({action})",
            },
        ]

        now_ist = datetime.now(IST)
        eval_id = f"eval_{int(now_ist.timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"
        eval_record = {
            "id": eval_id,
            "evaluation_id": eval_id,
            "timestamp": now_ist.strftime("%H:%M:%S"),
            "date": now_ist.strftime("%Y-%m-%d"),
            "symbol": sym,
            "strategy": strat,
            "direction": dir_str,
            "score": score,
            "win_probability": round(score * 100.0, 1),
            "action": action,
            "veto": veto,
            "veto_threshold": self.veto_threshold,
            "favorable_threshold": self.favorable_threshold,
            "features": {
                name: round(float(raw_feats[i]), 4)
                for i, name in enumerate(self.builder.feature_names)
            },
            "contributions": contributions,
            "top_features": importances,
            "pipeline_steps": pipeline_steps,
        }

        self.recent_evaluations.appendleft(eval_record)
        return eval_record

    def get_recent_evaluations(self, limit: int = 50, action: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recent scored evaluations with optional action filter."""
        items = list(self.recent_evaluations)
        if action:
            items = [item for item in items if item.get("action", "").upper() == action.upper()]
        return items[:limit]

    def get_scorecard_metrics(self) -> Dict[str, Any]:
        """Compute institutional scorecard metrics, drift, and calibration curve from real validation."""
        rep = self.last_validation_report or {}
        has_val = bool(rep and "overall_uplift_pct" in rep)
        edge_uplift = round(float(rep["overall_uplift_pct"]), 2) if has_val else None
        brier = round(float(rep["overall_brier_score"]), 4) if has_val else None
        base_wr = round(float(rep["baseline_win_rate_pct"]), 2) if has_val else None
        model_wr = round(float(rep["model_win_rate_pct"]), 2) if has_val else None
        auc = round(float(rep.get("overall_roc_auc", 0.0)), 3) if ("overall_roc_auc" in rep) else None

        # Recent evaluations counts
        evals = list(self.recent_evaluations)
        total_evals = len(evals)
        favorable_count = sum(1 for e in evals if e.get("action") == "FAVORABLE")
        veto_count = sum(1 for e in evals if e.get("action") == "VETO")
        neutral_count = sum(1 for e in evals if e.get("action") == "NEUTRAL")

        # Realized / estimated avoided losses: ₹1,250 only for actual vetoed trades
        veto_savings = veto_count * 1250.0 if veto_count > 0 else 0.0

        # Drift assessment
        recent_vixes = [e["features"].get("vix", 15.0) for e in evals[:20] if "features" in e]
        avg_recent_vix = float(np.mean(recent_vixes)) if recent_vixes else 15.0
        drift_status = "HEALTHY" if avg_recent_vix < 23.0 else ("MONITOR" if avg_recent_vix < 28.0 else "HIGH_DRIFT")

        # Empirical calibration curve directly from walk-forward validation (empty if unvalidated)
        curve_points = []
        for pt in rep.get("calibration_curve", []):
            pt_copy = dict(pt)
            pt_copy.setdefault("bin", pt.get("bin_range", ""))
            pt_copy.setdefault("predicted_win_rate", pt.get("predicted_prob", 0.0))
            pt_copy.setdefault("actual_win_rate", pt.get("empirical_frequency", 0.0))
            curve_points.append(pt_copy)

        return {
            "has_validation_data": has_val,
            "edge_uplift_pct": edge_uplift,
            "brier_score": brier,
            "baseline_win_rate_pct": base_wr,
            "model_win_rate_pct": model_wr,
            "roc_auc": auc,
            "total_evaluations": total_evals,
            "favorable_count": favorable_count,
            "veto_count": veto_count,
            "neutral_count": neutral_count,
            "veto_savings_estimate": veto_savings,
            "drift_status": drift_status,
            "drift_metric_value": round(float(abs(avg_recent_vix - 15.0) / 100.0), 2) if avg_recent_vix else 0.04,
            "avg_vix": round(avg_recent_vix, 1),
            "calibration_curve": curve_points,
            "veto_threshold": self.veto_threshold,
            "favorable_threshold": self.favorable_threshold,
            "model_version": self.version,
            "is_fitted": self.model.is_fitted,
        }

    def save(self) -> None:
        """Save current model weights and scaler to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(self.model_path))
        if self.last_validation_report:
            val_path = self.model_path.parent / "m3a_validation_report.json"
            try:
                with open(val_path, "w", encoding="utf-8") as vf:
                    json.dump(self.last_validation_report, vf, indent=2)
            except Exception as e:
                logger.warning("Could not persist validation report to %s: %s", val_path, e)

    def get_status(self) -> Dict[str, Any]:
        """Return runtime diagnostic telemetry for API and health monitoring."""
        return {
            "version": self.version,
            "is_fitted": self.model.is_fitted,
            "model_path": str(self.model_path),
            "veto_threshold": self.veto_threshold,
            "favorable_threshold": self.favorable_threshold,
            "features_count": len(self.builder.feature_names),
            "temperature_calibration": round(float(self.model.temperature), 3),
            "trained_at": getattr(self.model, "trained_at", None),
            "samples_count": getattr(self.model, "samples_count", None),
            "synthetic_share": getattr(self.model, "synthetic_share", None),
            "validation_report": self.last_validation_report,
            "scorecard": self.get_scorecard_metrics(),
        }


# Global singleton instance
_inference_engine: Optional[MLInferenceEngine] = None


def get_inference_engine() -> MLInferenceEngine:
    """Retrieve or initialize the global singleton inference engine."""
    global _inference_engine
    if _inference_engine is None:
        try:
            from config.settings import settings
            vt = float((settings._raw_config.get("risk", {}) or {}).get("ml_veto_threshold", 0.40))
        except Exception:
            vt = 0.40
        _inference_engine = MLInferenceEngine(veto_threshold=vt)
    return _inference_engine
