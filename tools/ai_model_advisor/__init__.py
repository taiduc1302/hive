"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .feedback import FeedbackStore, UsageRecord
from .recommend import RecommendationEngine
from .registry import ModelRegistry

__all__ = [
    "ActivityAnalyzer",
    "FeedbackStore",
    "ModelRegistry",
    "RecommendationEngine",
    "UsageRecord",
]
__version__ = "0.5.0"
