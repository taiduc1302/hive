"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .recommend import RecommendationEngine
from .registry import ModelRegistry

__all__ = ["ActivityAnalyzer", "ModelRegistry", "RecommendationEngine"]
__version__ = "0.1.0"
