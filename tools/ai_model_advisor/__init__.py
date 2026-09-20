"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .canary_evaluate import evaluate_promotion_canary
from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore, UsageRecord
from .promotion_plan import build_promotion_plans
from .promotion_review import build_promotion_review
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
    "build_promotion_plans",
    "build_promotion_review",
    "build_routing_proposals",
    "build_target_experiment_plan",
    "evaluate_promotion_canary",
    "recommend_for_target",
]
__version__ = "0.20.0"
