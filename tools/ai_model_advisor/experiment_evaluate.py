"""Compatibility shim for the canonical experiment evaluator.

New code should import from :mod:`tools.ai_model_advisor.experiment_eval`.
The old module path remains valid so in-flight scripts created while the
feature branch was evolving do not break.
"""

from .experiment_eval import evaluate_experiment_plan, experiment_evaluation_markdown

__all__ = ["evaluate_experiment_plan", "experiment_evaluation_markdown"]
