"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .feedback import FeedbackStore, UsageRecord
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .target_routing import recommend_for_target

__all__ = [
    "ActivityAnalyzer",
    "FeedbackStore",
    "ModelRegistry",
    "RecommendationEngine",
    "UsageRecord",
    "recommend_for_target",
]
__version__ = "0.13.0"
