"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore, UsageRecord
from .recommend import RecommendationEngine
from .registry import ModelRegistry
from .routing_proposals import build_routing_proposals
from .target_experiment_plan import build_target_experiment_plan
from .target_routing import recommend_for_target

__all__ = [
    "ActivityAnalyzer",
    "FeedbackStore",
    "ModelRegistry",
    "RecommendationEngine",
    "UsageRecord",
    "build_empirical_leaderboard",
    "build_routing_proposals",
    "build_target_experiment_plan",
    "recommend_for_target",
]
__version__ = "0.16.0"
