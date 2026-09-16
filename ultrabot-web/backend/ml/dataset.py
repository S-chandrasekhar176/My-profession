"""Point-in-time ML Dataset Builder for UltraBot M3a.

Extracts feature matrices (X) and binary outcome targets (y) from
shadow_outcomes records with strict chronological ordering to eliminate
lookahead leakage.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "atr_pct",
    "vwap_distance_pct",
    "trend_strength",
    "liquidity_ratio",
    "vix",
    "session_code",
    "htf_trend_code",
    "regime_code",
    "direction_code",
    "pcr",
    "iv_rank",
]

SESSION_MAP = {
    "OPENING_DRIVE": 1.0,
    "MORNING": 2.0,
    "LUNCH": 3.0,
    "AFTERNOON": 4.0,
    "POWER_CLOSE": 5.0,
}

HTF_TREND_MAP = {
    "up": 1.0,
    "flat": 0.0,
    "down": -1.0,
}

REGIME_MAP = {
    "bull": 1.0,
    "bear": -1.0,
    "sideways": 0.0,
    "volatile": 0.5,
}

DIRECTION_MAP = {
    "BUY": 1.0,
    "LONG": 1.0,
    "SELL": -1.0,
    "SHORT": -1.0,
}

DEFAULT_FEATURE_VALUES = {
    "atr_pct": 1.2,
    "vwap_distance_pct": 0.0,
    "trend_strength": 0.0,
    "liquidity_ratio": 1.0,
    "vix": 15.0,
    "session_code": 2.0,
    "htf_trend_code": 0.0,
    "regime_code": 0.0,
    "direction_code": 1.0,
    "pcr": 1.0,
    "iv_rank": 50.0,
}


class MLDatasetBuilder:
    """Extracts, sanitizes, and normalizes point-in-time features from shadow outcomes."""

    def __init__(self, feature_names: Optional[List[str]] = None):
        self.feature_names = feature_names or list(FEATURE_NAMES)
        self.means: Optional[np.ndarray] = None
        self.stds: Optional[np.ndarray] = None

    def extract_row_features(self, outcome: Any) -> Tuple[List[float], int]:
        """Convert a single ShadowOutcome object or dictionary into a feature vector and label."""
        # Normalize dict vs ORM model
        if hasattr(outcome, "__dict__"):
            d = {k: v for k, v in outcome.__dict__.items() if not k.startswith("_")}
        elif isinstance(outcome, dict):
            d = outcome
        else:
            d = {}

        # Parse features_json if present
        extra_features: Dict[str, Any] = {}
        if d.get("features_json"):
            try:
                extra_features = json.loads(d["features_json"])
            except Exception:
                extra_features = {}

        atr_pct = d.get("atr_pct") or extra_features.get("atr_pct") or DEFAULT_FEATURE_VALUES["atr_pct"]
        vwap_dist = d.get("vwap_distance_pct") or extra_features.get("vwap_distance_pct") or DEFAULT_FEATURE_VALUES["vwap_distance_pct"]
        trend_str = d.get("trend_strength") or extra_features.get("trend_strength") or DEFAULT_FEATURE_VALUES["trend_strength"]
        liq_ratio = d.get("liquidity_ratio") or extra_features.get("liquidity_ratio") or DEFAULT_FEATURE_VALUES["liquidity_ratio"]
        vix = d.get("vix_at_signal") or extra_features.get("vix") or DEFAULT_FEATURE_VALUES["vix"]

        session_str = str(d.get("session_class") or extra_features.get("session_class") or "MORNING").upper()
        session_code = SESSION_MAP.get(session_str, DEFAULT_FEATURE_VALUES["session_code"])

        htf_str = str(d.get("htf_trend") or extra_features.get("htf_trend") or "flat").lower()
        htf_code = HTF_TREND_MAP.get(htf_str, DEFAULT_FEATURE_VALUES["htf_trend_code"])

        regime_str = str(d.get("regime_at_signal") or "sideways").lower()
        regime_code = REGIME_MAP.get(regime_str, DEFAULT_FEATURE_VALUES["regime_code"])

        direction_str = str(d.get("direction") or "BUY").upper()
        dir_code = DIRECTION_MAP.get(direction_str, DEFAULT_FEATURE_VALUES["direction_code"])

        pcr = float(d.get("pcr") or extra_features.get("pcr") or DEFAULT_FEATURE_VALUES["pcr"])
        iv_rank = float(d.get("iv_rank") or extra_features.get("iv_rank") or DEFAULT_FEATURE_VALUES["iv_rank"])

        features = [
            float(atr_pct),
            float(vwap_dist),
            float(trend_str),
            float(liq_ratio),
            float(vix),
            float(session_code),
            float(htf_code),
            float(regime_code),
            float(dir_code),
            float(pcr),
            float(iv_rank),
        ]

        # Target label: 1 = Win (Target Hit or positive PnL), 0 = Loss (SL hit or negative PnL)
        outcome_str = str(d.get("outcome") or "").upper()
        pnl = float(d.get("pnl_per_share") or 0.0)
        label = 1 if (outcome_str == "SHADOW_TARGET" or pnl > 0.0) else 0

        return features, label

    def build_dataset(
        self,
        outcomes: List[Any],
        fit_scaler: bool = True,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Build feature matrix X (N, D) and target vector y (N)."""
        if not outcomes:
            return np.empty((0, len(self.feature_names))), np.empty((0,))

        # Sort chronologically by created_at or registered_at if available
        def _get_time(o: Any) -> str:
            if isinstance(o, dict):
                return str(o.get("created_at") or o.get("registered_at") or "")
            return str(getattr(o, "created_at", "") or getattr(o, "registered_at", "") or "")

        sorted_outcomes = sorted(outcomes, key=_get_time)

        x_rows: List[List[float]] = []
        y_rows: List[int] = []

        for out in sorted_outcomes:
            feats, label = self.extract_row_features(out)
            x_rows.append(feats)
            y_rows.append(label)

        X = np.array(x_rows, dtype=np.float64)
        y = np.array(y_rows, dtype=np.float64)

        if fit_scaler:
            self.means = np.mean(X, axis=0)
            self.stds = np.std(X, axis=0)
            # Avoid division by zero
            self.stds[self.stds < 1e-6] = 1.0

        if self.means is not None and self.stds is not None:
            X = (X - self.means) / self.stds

        return X, y

    def transform(self, raw_features: List[float]) -> np.ndarray:
        """Standardize a single sample feature vector using fitted scaler."""
        arr = np.array(raw_features, dtype=np.float64)
        if self.means is not None and self.stds is not None:
            arr = (arr - self.means) / self.stds
        return arr

    def generate_synthetic_bootstrap(
        self,
        n_samples: int = 150,
        random_seed: int = 42,
    ) -> List[Dict[str, Any]]:
        """Generate realistic synthetic bootstrap shadow outcomes for offline training.

        Allows initializing and calibrating M3a models prior to accumulating
        100+ live sessions, matching empirical Indian equity market statistics.
        """
        rng = np.random.RandomState(random_seed)
        samples = []

        strategies = ["ORB", "MRF", "VC", "SIC"]
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"]
        sessions = ["OPENING_DRIVE", "MORNING", "LUNCH", "AFTERNOON", "POWER_CLOSE"]
        regimes = ["bull", "bear", "sideways", "volatile"]

        for i in range(n_samples):
            strat = strategies[i % len(strategies)]
            sym = symbols[i % len(symbols)]
            direction = "BUY" if rng.rand() > 0.4 else "SELL"
            sess = rng.choice(sessions)
            regime = rng.choice(regimes)

            atr_pct = float(np.clip(rng.normal(1.2, 0.4), 0.3, 3.5))
            vwap_dist = float(rng.normal(0.0, 0.8))
            trend_str = float(rng.normal(0.2, 1.0))
            liq_ratio = float(np.clip(rng.exponential(1.0), 0.2, 5.0))
            vix = float(np.clip(rng.normal(15.0, 3.0), 10.0, 32.0))
            pcr = float(np.clip(rng.normal(1.05, 0.25), 0.5, 2.0))
            iv_rank = float(np.clip(rng.normal(45.0, 20.0), 5.0, 95.0))

            # Ground truth signal edge: higher liquidity + trend alignment + sane vix = higher win prob
            score_latent = (
                0.3 * (liq_ratio - 1.0)
                + 0.4 * (trend_str if direction == "BUY" else -trend_str)
                - 0.2 * abs(vwap_dist)
                - 0.05 * max(0.0, vix - 20.0)
            )
            prob_win = 1.0 / (1.0 + np.exp(-score_latent))
            is_win = rng.rand() < prob_win

            outcome = "SHADOW_TARGET" if is_win else "SHADOW_SL"
            pnl = float(round(rng.uniform(5.0, 25.0) if is_win else -rng.uniform(4.0, 15.0), 2))

            samples.append({
                "id": f"bootstrap-{i}",
                "symbol": sym,
                "strategy": strat,
                "direction": direction,
                "outcome": outcome,
                "pnl_per_share": pnl,
                "atr_pct": atr_pct,
                "vwap_distance_pct": vwap_dist,
                "trend_strength": trend_str,
                "liquidity_ratio": liq_ratio,
                "vix_at_signal": vix,
                "session_class": sess,
                "htf_trend": "up" if trend_str > 0.2 else ("down" if trend_str < -0.2 else "flat"),
                "regime_at_signal": regime,
                "pcr": pcr,
                "iv_rank": iv_rank,
                "is_synthetic": True,
                "created_at": f"2026-09-{(i % 28) + 1:02d}T10:00:00+05:30",
            })

        return samples
