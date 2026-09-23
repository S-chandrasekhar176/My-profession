"""UltraBot M3a Probabilistic Classifiers (Pure NumPy).

Vectorized Logistic Regression and Gradient Boosted Decision Stumps with
Platt / Sigmoid probability calibration. Requires zero external C-compilers
and delivers sub-millisecond inference.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid function."""
    z_clipped = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z_clipped))


class CalibratedLinearModel:
    """L2-Regularized Logistic Regression with Platt Probability Calibration."""

    def __init__(
        self,
        learning_rate: float = 0.05,
        l2_reg: float = 0.01,
        max_iter: int = 400,
        tolerance: float = 1e-5,
    ):
        self.lr = learning_rate
        self.l2_reg = l2_reg
        self.max_iter = max_iter
        self.tol = tolerance
        self.weights: Optional[np.ndarray] = None
        self.bias: float = 0.0
        self.temperature: float = 1.0  # Calibration scaling factor
        self.is_fitted: bool = False
        self.means: Optional[np.ndarray] = None
        self.stds: Optional[np.ndarray] = None
        self.trained_at: Optional[str] = None
        self.samples_count: Optional[int] = None
        self.synthetic_share: Optional[float] = None
        self.feature_names: Optional[List[str]] = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "CalibratedLinearModel":
        """Train logistic regression model via gradient descent."""
        N, D = X.shape
        if N == 0:
            raise ValueError("Cannot fit on empty dataset")

        self.weights = np.zeros(D, dtype=np.float64)
        self.bias = float(np.mean(y) - 0.5)

        prev_loss = float("inf")
        for iteration in range(self.max_iter):
            # Forward pass
            logits = np.dot(X, self.weights) + self.bias
            preds = _sigmoid(logits)

            # Binary cross entropy loss with L2
            eps = 1e-9
            bce = -np.mean(y * np.log(preds + eps) + (1.0 - y) * np.log(1.0 - preds + eps))
            l2_loss = 0.5 * self.l2_reg * np.sum(self.weights ** 2)
            loss = bce + l2_loss

            if abs(prev_loss - loss) < self.tol:
                break
            prev_loss = loss

            # Gradients
            err = preds - y
            grad_w = (np.dot(X.T, err) / N) + (self.l2_reg * self.weights)
            grad_b = float(np.mean(err))

            # Update
            self.weights -= self.lr * grad_w
            self.bias -= self.lr * grad_b

        # Platt Calibration: optimize temperature parameter on logits
        logits = np.dot(X, self.weights) + self.bias
        self.temperature = self._calibrate_temperature(logits, y)
        self.is_fitted = True
        return self

    def _calibrate_temperature(self, logits: np.ndarray, y: np.ndarray) -> float:
        """Find optimal temperature scaling factor to minimize calibration error."""
        best_t = 1.0
        best_loss = float("inf")
        for t in np.linspace(0.5, 2.5, 21):
            scaled_p = _sigmoid(logits / t)
            loss = float(np.mean((scaled_p - y) ** 2))  # Brier score
            if loss < best_loss:
                best_loss = loss
                best_t = float(t)
        return best_t

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return calibrated probabilities P(win)."""
        if not self.is_fitted or self.weights is None:
            return np.full(X.shape[0], 0.5)
        logits = np.dot(X, self.weights) + self.bias
        calibrated_logits = logits / max(self.temperature, 0.1)
        return _sigmoid(calibrated_logits)

    def predict(self, X: np.ndarray, threshold: float = 0.50) -> np.ndarray:
        """Return binary classifications."""
        return (self.predict_proba(X) >= threshold).astype(int)

    def get_feature_importance(self, feature_names: List[str]) -> Dict[str, float]:
        """Return normalized absolute feature weights."""
        if self.weights is None:
            return {f: 0.0 for f in feature_names}
        abs_w = np.abs(self.weights)
        total = np.sum(abs_w) if np.sum(abs_w) > 0 else 1.0
        return {
            name: round(float(abs_w[i] / total), 4)
            for i, name in enumerate(feature_names[:len(abs_w)])
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize model parameters and provenance metadata to JSON-safe dictionary."""
        d = {
            "model_type": "CalibratedLinearModel",
            "weights": self.weights.tolist() if self.weights is not None else [],
            "bias": float(self.bias),
            "temperature": float(self.temperature),
            "is_fitted": self.is_fitted,
        }
        if getattr(self, "means", None) is not None:
            d["means"] = self.means.tolist() if hasattr(self.means, "tolist") else list(self.means)
        if getattr(self, "stds", None) is not None:
            d["stds"] = self.stds.tolist() if hasattr(self.stds, "tolist") else list(self.stds)
        if getattr(self, "trained_at", None) is not None:
            d["trained_at"] = str(self.trained_at)
        if getattr(self, "samples_count", None) is not None:
            d["samples_count"] = int(self.samples_count)
        if getattr(self, "synthetic_share", None) is not None:
            d["synthetic_share"] = float(self.synthetic_share)
        if getattr(self, "feature_names", None) is not None:
            d["feature_names"] = list(self.feature_names)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CalibratedLinearModel":
        """Reconstruct model from dictionary."""
        model = cls()
        model.weights = np.array(d.get("weights", []), dtype=np.float64)
        model.bias = float(d.get("bias", 0.0))
        model.temperature = float(d.get("temperature", 1.0))
        model.is_fitted = bool(d.get("is_fitted", True))
        if "means" in d and d["means"]:
            model.means = np.array(d["means"], dtype=np.float64)
        else:
            model.means = None
        if "stds" in d and d["stds"]:
            model.stds = np.array(d["stds"], dtype=np.float64)
        else:
            model.stds = None
        model.trained_at = d.get("trained_at")
        model.samples_count = d.get("samples_count")
        model.synthetic_share = d.get("synthetic_share")
        model.feature_names = d.get("feature_names")
        return model

    def save(self, filepath: str) -> None:
        """Save model to JSON file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "CalibratedLinearModel":
        """Load model from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d)


class DecisionStump:
    """1-level binary decision tree component."""

    def __init__(self):
        self.feature_idx: int = 0
        self.threshold: float = 0.0
        self.val_left: float = 0.0
        self.val_right: float = 0.0

    def fit(self, X: np.ndarray, residuals: np.ndarray) -> "DecisionStump":
        """Find feature and split threshold that minimizes mean squared error of residuals."""
        N, D = X.shape
        best_loss = float("inf")

        for d in range(D):
            col = X[:, d]
            thresholds = np.percentile(col, [25, 50, 75])
            for th in thresholds:
                left_mask = col <= th
                right_mask = ~left_mask
                if not np.any(left_mask) or not np.any(right_mask):
                    continue
                v_l = float(np.mean(residuals[left_mask]))
                v_r = float(np.mean(residuals[right_mask]))
                loss = float(np.sum((residuals[left_mask] - v_l) ** 2) + np.sum((residuals[right_mask] - v_r) ** 2))
                if loss < best_loss:
                    best_loss = loss
                    self.feature_idx = d
                    self.threshold = float(th)
                    self.val_left = v_l
                    self.val_right = v_r

        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict residual adjustment."""
        col = X[:, self.feature_idx]
        return np.where(col <= self.threshold, self.val_left, self.val_right)


class GradientBoostedStumps:
    """Lightweight Gradient Boosting on Decision Stumps with Logistic Loss."""

    def __init__(self, n_estimators: int = 25, learning_rate: float = 0.1):
        self.n_estimators = n_estimators
        self.lr = learning_rate
        self.base_pred: float = 0.0
        self.stumps: List[DecisionStump] = []
        self.is_fitted: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GradientBoostedStumps":
        """Train gradient boosting sequence."""
        N = X.shape[0]
        if N == 0:
            raise ValueError("Empty dataset")

        self.base_pred = float(np.log(max(np.mean(y), 0.01) / max(1.0 - np.mean(y), 0.01)))
        f_m = np.full(N, self.base_pred)
        self.stumps = []

        for _ in range(self.n_estimators):
            p = _sigmoid(f_m)
            residuals = y - p
            stump = DecisionStump().fit(X, residuals)
            self.stumps.append(stump)
            f_m += self.lr * stump.predict(X)

        self.is_fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predict win probabilities."""
        if not self.is_fitted:
            return np.full(X.shape[0], 0.5)
        f_m = np.full(X.shape[0], self.base_pred)
        for s in self.stumps:
            f_m += self.lr * s.predict(X)
        return _sigmoid(f_m)

    def predict(self, X: np.ndarray, threshold: float = 0.50) -> np.ndarray:
        """Predict binary labels."""
        return (self.predict_proba(X) >= threshold).astype(int)
