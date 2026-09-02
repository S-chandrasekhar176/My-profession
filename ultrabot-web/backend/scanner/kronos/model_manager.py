import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ModelManager:
    """Manager for ML model loading, unloading, and inference.

    Currently operates in rule-based mode, returning a SignalScorer.
    When ML models are available, this class will handle loading them
    and using them for scoring instead.
    """

    def __init__(self):
        self._model_loaded = False
        self._model_name: str = ""
        self._model: Any = None
        self._scorer = None  # Rule-based scorer fallback

    def load_model(self, model_name: str, model_path: Optional[str] = None) -> bool:
        """Load an ML model.

        Args:
            model_name: Name/identifier for the model.
            model_path: Path to the model file (if applicable).

        Returns:
            True if model loaded successfully.
        """
        # Try to load a real model if path is provided
        if model_path:
            try:
                import joblib
                import os
                if os.path.exists(model_path):
                    self._model = joblib.load(model_path)
                    self._model_name = model_name
                    self._model_loaded = True
                    logger.info("ML model '%s' loaded from %s", model_name, model_path)
                    return True
                else:
                    logger.warning("Model file not found: %s. Using rule-based scorer.", model_path)
            except ImportError:
                logger.warning("joblib not available. Using rule-based scorer.")
            except Exception as e:
                logger.warning("Failed to load model '%s': %s. Using rule-based scorer.", model_name, e)

        # No model to load, use rule-based scorer
        self._model_name = model_name or "rule_based"
        self._model_loaded = True
        logger.info("Using rule-based scorer '%s'", self._model_name)
        return True

    def unload_model(self) -> bool:
        """Unload the current model."""
        self._model = None
        self._model_name = ""
        self._model_loaded = False
        self._scorer = None
        logger.info("Model unloaded")
        return True

    def is_loaded(self) -> bool:
        """Check if a model/scorer is loaded."""
        return self._model_loaded

    def get_model_name(self) -> str:
        """Get the name of the loaded model."""
        return self._model_name

    def get_scorer(self) -> Any:
        """Get the scoring function/model.

        Returns:
            If ML model is loaded, returns a callable that takes
            (signal, market_context) and returns a score.
            If rule-based, returns a SignalScorer instance.
        """
        if self._scorer is not None:
            return self._scorer

        if self._model is not None:
            # Wrap ML model in a callable interface
            def _ml_scorer(signal: Dict[str, Any], market_context: Dict[str, Any]) -> float:
                try:
                    import numpy as np
                    features = self._extract_features(signal, market_context)
                    prediction = self._model.predict_proba([features])[0]
                    # Get probability of positive class
                    if hasattr(prediction, '__len__') and len(prediction) > 1:
                        return float(max(prediction))
                    return float(prediction)
                except Exception as e:
                    logger.warning("ML model prediction failed: %s", e)
                    return 0.5
            return _ml_scorer

        # Rule-based fallback
        from scanner.kronos.signal_scorer import SignalScorer
        self._scorer = SignalScorer()
        return self._scorer

    @staticmethod
    def _extract_features(signal: Dict[str, Any], market_context: Dict[str, Any]) -> list:
        """Extract feature vector from signal and context for ML model."""
        features = [
            float(signal.get("confidence", 0.5)),
            float(signal.get("volume_ratio", 1.0)),
            float(market_context.get("vix", 15)),
            1.0 if signal.get("direction", "LONG") == "LONG" else 0.0,
            # Regime encoding
            1.0 if market_context.get("regime") == "Bull" else 0.0,
            1.0 if market_context.get("regime") == "Bear" else 0.0,
            1.0 if market_context.get("regime") == "Sideways" else 0.0,
            1.0 if market_context.get("regime") == "Volatile" else 0.0,
            # Trend encoding
            1.0 if market_context.get("trend") == "up" else 0.0,
            1.0 if market_context.get("trend") == "down" else 0.0,
        ]
        return features

    def list_available_models(self) -> list:
        """Return list of available model names.

        Currently returns the rule-based option. In future, will scan
        a models directory for available .pkl/.joblib files.
        """
        return ["rule_based"]
