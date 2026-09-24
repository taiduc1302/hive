"""AI Model Advisor: current-model tracking and workload-aware routing."""

from .activity import ActivityAnalyzer
from .canary_evaluate import evaluate_promotion_canary
from .empirical_leaderboard import build_empirical_leaderboard
from .feedback import FeedbackStore, UsageRecord
from .hive_config_preview import build_hive_config_preview
from .hive_promotion_checkpoint import (
    build_hive_promotion_checkpoint,
    validate_hive_promotion_checkpoint,
    verify_hive_promotion_checkpoint,
)
from .hive_promotion_gate import build_hive_promotion_gate
from .hive_promotion_journal import (
    append_hive_promotion_journal,
    build_hive_promotion_journal,
    validate_hive_promotion_journal,
)
from .hive_promotion_preview import build_hive_promotion_preview
from .hive_promotion_receipt import build_hive_promotion_receipt
from .hive_promotion_reconcile import build_hive_promotion_reconciliation
from .hive_promotion_registry import build_hive_promotion_registry
from .hive_promotion_rollback import build_hive_promotion_rollback_audit
from .hive_promotion_rollback_plan import build_hive_promotion_rollback_plan
from .hive_promotion_rollback_preflight import (
    build_hive_promotion_rollback_preflight,
)
from .hive_promotion_status import build_hive_promotion_status
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
    "append_hive_promotion_journal",
    "build_empirical_leaderboard",
    "build_hive_config_preview",
    "build_hive_promotion_checkpoint",
    "build_hive_promotion_gate",
    "build_hive_promotion_journal",
    "build_hive_promotion_preview",
    "build_hive_promotion_receipt",
    "build_hive_promotion_reconciliation",
    "build_hive_promotion_registry",
    "build_hive_promotion_status",
    "build_hive_promotion_rollback_audit",
    "build_hive_promotion_rollback_plan",
    "build_hive_promotion_rollback_preflight",
    "build_promotion_plans",
    "build_promotion_review",
    "build_routing_proposals",
    "build_target_experiment_plan",
    "evaluate_promotion_canary",
    "ModelRegistry",
    "RecommendationEngine",
    "recommend_for_target",
    "UsageRecord",
    "validate_hive_promotion_checkpoint",
    "validate_hive_promotion_journal",
    "verify_hive_promotion_checkpoint",
]
__version__ = "0.34.0"
