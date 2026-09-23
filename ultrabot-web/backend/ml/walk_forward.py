"""Strict Walk-Forward Temporal Cross-Validation for UltraBot M3a.

Ensures zero lookahead leakage by training strictly on chronological past
windows and evaluating on out-of-sample future windows.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from ml.models import CalibratedLinearModel

logger = logging.getLogger(__name__)


class WalkForwardValidator:
    """Performs expanding or rolling chronological walk-forward validation."""

    def __init__(
        self,
        n_splits: int = 4,
        min_train_size: int = 40,
        veto_threshold: float = 0.40,
    ):
        self.n_splits = n_splits
        self.min_train_size = min_train_size
        self.veto_threshold = veto_threshold

    def evaluate(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> Dict[str, Any]:
        """Execute walk-forward evaluation across temporal folds."""
        N = X.shape[0]
        if N < self.min_train_size + 10:
            # Not enough samples for multi-fold; evaluate train-test split
            split_idx = max(int(N * 0.7), self.min_train_size // 2)
            if split_idx >= N or split_idx <= 0:
                split_idx = N // 2
            train_idx = np.arange(split_idx)
            test_idx = np.arange(split_idx, N)
            folds = [(train_idx, test_idx)]
        else:
            folds = self._generate_temporal_folds(N)

        fold_metrics: List[Dict[str, float]] = []
        all_y_true: List[float] = []
        all_y_pred_proba: List[float] = []

        for fold_i, (train_idx, test_idx) in enumerate(folds):
            X_train, y_train = X[train_idx], y[train_idx]
            X_test, y_test = X[test_idx], y[test_idx]

            if len(X_train) == 0 or len(X_test) == 0:
                continue

            model = CalibratedLinearModel().fit(X_train, y_train)
            probs = model.predict_proba(X_test)

            all_y_true.extend(y_test.tolist())
            all_y_pred_proba.extend(probs.tolist())

            # Metrics for this fold
            brier = float(np.mean((probs - y_test) ** 2))
            baseline_win_rate = float(np.mean(y_test))

            # Filtered trades above veto threshold
            accepted_mask = probs >= self.veto_threshold
            if np.any(accepted_mask):
                model_win_rate = float(np.mean(y_test[accepted_mask]))
                uplift = float(model_win_rate - baseline_win_rate)
            else:
                model_win_rate = baseline_win_rate
                uplift = 0.0

            fold_metrics.append({
                "fold": fold_i + 1,
                "train_size": len(train_idx),
                "test_size": len(test_idx),
                "brier_score": round(brier, 4),
                "baseline_win_rate": round(baseline_win_rate * 100.0, 2),
                "model_win_rate": round(model_win_rate * 100.0, 2),
                "win_rate_uplift": round(uplift * 100.0, 2),
                "veto_rate": round(float(np.mean(~accepted_mask)) * 100.0, 2),
            })

        # Overall summary
        arr_true = np.array(all_y_true)
        arr_probs = np.array(all_y_pred_proba)

        overall_brier = float(np.mean((arr_probs - arr_true) ** 2)) if len(arr_true) > 0 else 0.25
        base_wr = float(np.mean(arr_true)) if len(arr_true) > 0 else 0.50

        acc_mask = arr_probs >= self.veto_threshold
        filtered_wr = float(np.mean(arr_true[acc_mask])) if np.any(acc_mask) else base_wr
        overall_uplift = filtered_wr - base_wr

        calibration_curve = self._compute_calibration_curve(arr_true, arr_probs)

        return {
            "folds_evaluated": len(fold_metrics),
            "total_test_samples": len(all_y_true),
            "overall_brier_score": round(overall_brier, 4),
            "baseline_win_rate_pct": round(base_wr * 100.0, 2),
            "model_win_rate_pct": round(filtered_wr * 100.0, 2),
            "overall_uplift_pct": round(overall_uplift * 100.0, 2),
            "veto_rate_pct": round(float(np.mean(~acc_mask)) * 100.0, 2) if len(acc_mask) > 0 else 0.0,
            "calibration_curve": calibration_curve,
            "folds": fold_metrics,
        }

    def _generate_temporal_folds(self, N: int) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Generate expanding chronological train/test indices."""
        folds = []
        step = (N - self.min_train_size) // self.n_splits
        if step < 5:
            step = max(1, (N - self.min_train_size))

        for i in range(self.n_splits):
            train_end = self.min_train_size + (i * step)
            test_end = min(N, train_end + step)
            if train_end >= N:
                break
            train_idx = np.arange(train_end)
            test_idx = np.arange(train_end, test_end)
            if len(test_idx) > 0:
                folds.append((train_idx, test_idx))

        return folds

    def _compute_calibration_curve(
        self,
        y_true: np.ndarray,
        y_prob: np.ndarray,
        n_bins: int = 5,
    ) -> List[Dict[str, Any]]:
        """Compute empirical frequency vs predicted probability across bins."""
        if len(y_true) == 0:
            return []

        bins = np.linspace(0.0, 1.0, n_bins + 1)
        curve = []

        for i in range(n_bins):
            bin_lower = bins[i]
            bin_upper = bins[i + 1]
            mask = (y_prob >= bin_lower) & (y_prob < bin_upper if i < n_bins - 1 else y_prob <= bin_upper)
            count = int(np.sum(mask))
            if count > 0:
                mean_pred = float(np.mean(y_prob[mask]))
                empirical_freq = float(np.mean(y_true[mask]))
            else:
                mean_pred = (bin_lower + bin_upper) / 2.0
                empirical_freq = 0.0

            curve.append({
                "bin_range": f"{round(bin_lower, 2)}-{round(bin_upper, 2)}",
                "predicted_prob": round(mean_pred, 4),
                "empirical_frequency": round(empirical_freq, 4),
                "samples_count": count,
            })

        return curve
