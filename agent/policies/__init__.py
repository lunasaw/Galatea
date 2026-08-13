"""
Policy framework for Galatea agents.

Provides budget control, permission management, and quality gates.

Key components:
- BudgetPolicy: Token/cost budget enforcement
- PermissionPolicy: Tool access control
- QualityGatePolicy: Stage validation gates

Reference: Claude SDK's budget and permission systems.
"""

from agent.policies.budget import (
    BudgetPolicy,
    BudgetExceededError,
)
from agent.policies.permission import (
    PermissionPolicy,
    PermissionRule,
    PermissionBehavior,
    PermissionMode,
    PermissionDeniedError,
)
from agent.policies.quality import (
    QualityGatePolicy,
    QualityGate,
    GateStatus,
    QualityGateFailedError,
)
from agent.policies.patrol import (
    ActionDecision,
    PatrolActionPolicy,
    PatrolLifecyclePolicy,
)

__all__ = [
    # Budget
    "BudgetPolicy",
    "BudgetExceededError",
    # Permission
    "PermissionPolicy",
    "PermissionRule",
    "PermissionBehavior",
    "PermissionMode",
    "PermissionDeniedError",
    # Quality
    "QualityGatePolicy",
    "QualityGate",
    "GateStatus",
    "QualityGateFailedError",
    # Patrol
    "ActionDecision",
    "PatrolActionPolicy",
    "PatrolLifecyclePolicy",
]
