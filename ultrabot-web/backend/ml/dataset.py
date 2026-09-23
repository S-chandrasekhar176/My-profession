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

    def extract_row_features(
        self,
        outcome: Any,
        require_features: bool = False,
    ) -> Optional[Tuple[List[float], int]]:
        """Convert a single ShadowOutcome object or dictionary into a feature vector and label.

        If require_features is True (used during dataset building / training), returns None
        if features_json / continuous features are missing or unparseable, ensuring no
        fabricated default vectors enter the training set (NF10-A).
        If require_features is False (used during live scoring or test mocks), missing
        features fall back to DEFAULT_FEATURE_VALUES without falsy 0.0 bugs.
        """
        # Normalize dict vs ORM model
        if hasattr(outcome, "__dict__"):
            d = {k: v for k, v in outcome.__dict__.items() if not k.startswith("_")}
        elif isinstance(outcome, dict):
            d = outcome
        else:
            return None

        # Parse features_json if present
        extra_features: Dict[str, Any] = {}
        features_json_raw = d.get("features_json")
        if features_json_raw:
            try:
                parsed = json.loads(features_json_raw)
                if isinstance(parsed, dict):
                    extra_features = parsed
                elif require_features:
                    return None
            except Exception:
                if require_features:
                    return None
        elif require_features and "atr_pct" not in d:
            # If building dataset from DB rows and features_json is missing, exclude row (NF10-A)
            return None

        def _get_num(d_dict: Dict[str, Any], key: str, fallback: Optional[float] = None) -> Optional[float]:
            if key in d_dict and d_dict[key] is not None:
                try:
                    return float(d_dict[key])
                except (ValueError, TypeError):
                    pass
            return fallback

        fb = None if require_features else DEFAULT_FEATURE_VALUES

        # Extract features without 'or' falsy chains that turn legit 0.0 into defaults
        atr_pct = _get_num(extra_features, "atr_pct", _get_num(d, "atr_pct", fb["atr_pct"] if fb else None))
        vwap_dist = _get_num(extra_features, "vwap_distance_pct", _get_num(d, "vwap_distance_pct", fb["vwap_distance_pct"] if fb else None))
        trend_str = _get_num(extra_features, "trend_strength", _get_num(d, "trend_strength", fb["trend_strength"] if fb else None))
        liq_ratio = _get_num(extra_features, "liquidity_ratio", _get_num(d, "liquidity_ratio", fb["liquidity_ratio"] if fb else None))
        vix = _get_num(extra_features, "vix", _get_num(d, "vix_at_signal", fb["vix"] if fb else 15.0))

        # When training, essential continuous features must be present
        if require_features:
            if atr_pct is None or vwap_dist is None or trend_str is None or liq_ratio is None:
                return None

        session_str = str(extra_features.get("session_class") or d.get("session_class") or "MORNING").upper()
        session_code = SESSION_MAP.get(session_str, 2.0)

        htf_str = str(extra_features.get("htf_trend") or d.get("htf_trend") or "flat").lower()
        htf_code = HTF_TREND_MAP.get(htf_str, 0.0)

        regime_str = str(extra_features.get("regime") or d.get("regime_at_signal") or "sideways").lower()
        regime_code = REGIME_MAP.get(regime_str, 0.0)

        direction_str = str(extra_features.get("direction") or d.get("direction") or "BUY").upper()
        dir_code = DIRECTION_MAP.get(direction_str, 1.0)

        pcr = _get_num(extra_features, "pcr", _get_num(d, "pcr", fb["pcr"] if fb else 1.0))
        iv_rank = _get_num(extra_features, "iv_rank", _get_num(d, "iv_rank", fb["iv_rank"] if fb else 50.0))

        features = [
            float(atr_pct if atr_pct is not None else 1.2),
            float(vwap_dist if vwap_dist is not None else 0.0),
            float(trend_str if trend_str is not None else 0.0),
            float(liq_ratio if liq_ratio is not None else 1.0),
            float(vix if vix is not None else 15.0),
            float(session_code),
            float(htf_code),
            float(regime_code),
            float(dir_code),
            float(pcr if pcr is not None else 1.0),
            float(iv_rank if iv_rank is not None else 50.0),
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
        allow_synthetic: Optional[bool] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Build feature matrix X (N, D) and target vector y (N).
        
        Excludes rows lacking real features (NF10-A) and defensively prevents
        synthetic bootstrap samples from mixing into live training datasets (NF10-C).
        """
        if not outcomes:
            return np.empty((0, len(self.feature_names))), np.empty((0,))

        # Determine effective synthetic allowance:
        # If allow_synthetic is not explicitly passed, allow synthetic only if ALL outcomes are synthetic (e.g. test fixtures).
        # If any real outcome is present, synthetic data can NEVER silently mix in.
        has_real = any(
            not (o.get("is_synthetic", False) if isinstance(o, dict) else getattr(o, "is_synthetic", False))
            for o in outcomes
        )
        effective_allow_synthetic = allow_synthetic if allow_synthetic is not None else (not has_real)

        # Sort chronologically by created_at or registered_at if available
        def _get_time(o: Any) -> str:
            if isinstance(o, dict):
                return str(o.get("created_at") or o.get("registered_at") or "")
            return str(getattr(o, "created_at", "") or getattr(o, "registered_at", "") or "")

        sorted_outcomes = sorted(outcomes, key=_get_time)

        x_rows: List[List[float]] = []
        y_rows: List[int] = []
        included_count = 0
        excluded_count = 0
        excluded_synthetic = 0
        schema_1_0 = 0
        schema_1_1 = 0
        schema_unknown = 0

        for out in sorted_outcomes:
            # Defensive synthetic filter (NF10-C)
            is_syn = (
                out.get("is_synthetic", False)
                if isinstance(out, dict)
                else getattr(out, "is_synthetic", False)
            )
            if is_syn and not effective_allow_synthetic:
                excluded_synthetic += 1
                continue

            res = self.extract_row_features(out, require_features=True)
            if res is None:
                excluded_count += 1
                continue

            feats, label = res
            x_rows.append(feats)
            y_rows.append(label)
            included_count += 1

            # Read schema_version from features_json (Task 3)
            schema_ver = None
            if isinstance(out, dict):
                fj = out.get("features_json")
                if isinstance(fj, str) and fj:
                    try:
                        schema_ver = json.loads(fj).get("schema_version")
                    except Exception:
                        schema_ver = None
                elif isinstance(fj, dict):
                    schema_ver = fj.get("schema_version")
                if not schema_ver:
                    schema_ver = out.get("schema_version")
            else:
                fj = getattr(out, "features_json", None)
                if isinstance(fj, str) and fj:
                    try:
                        schema_ver = json.loads(fj).get("schema_version")
                    except Exception:
                        schema_ver = None
                elif isinstance(fj, dict):
                    schema_ver = fj.get("schema_version")
                if not schema_ver:
                    schema_ver = getattr(out, "schema_version", None)

            if schema_ver in ("1.0", "v1"):
                schema_1_0 += 1
            elif schema_ver == "1.1":
                schema_1_1 += 1
            else:
                schema_unknown += 1

        self.last_schema_counts = {
            "1.0": schema_1_0,
            "1.1": schema_1_1,
            "unknown": schema_unknown,
        }

        msg = (
            f"[ML Dataset] Built dataset: included: {included_count}, excluded: {excluded_count}, "
            f"schema_1.0: {schema_1_0}, schema_1.1: {schema_1_1}, schema_unknown: {schema_unknown}"
        )
        if excluded_synthetic > 0:
            msg += f", excluded_synthetic: {excluded_synthetic}"
        logger.info(msg)
        print(msg)

        X = np.array(x_rows, dtype=np.float64)
        y = np.array(y_rows, dtype=np.float64)

        if fit_scaler and len(X) > 0:
            self.means = np.mean(X, axis=0)
            self.stds = np.std(X, axis=0)
            # Avoid division by zero
            self.stds[self.stds < 1e-6] = 1.0

        if self.means is not None and self.stds is not None and len(X) > 0:
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
