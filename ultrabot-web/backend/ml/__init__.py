"""UltraBot Machine Learning (M3a) Engine.

Provides point-in-time feature extraction, walk-forward validation,
calibrated probabilistic models, and sub-millisecond signal inference.
"""

from ml.dataset import MLDatasetBuilder
from ml.models import CalibratedLinearModel, GradientBoostedStumps
from ml.walk_forward import WalkForwardValidator
from ml.inference import MLInferenceEngine

__all__ = [
    "MLDatasetBuilder",
    "CalibratedLinearModel",
    "GradientBoostedStumps",
    "WalkForwardValidator",
    "MLInferenceEngine",
]
