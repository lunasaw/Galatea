"""
Policy framework for Galatea agents.

Provides budget control, permission management, and quality gates.

Key components:
- BudgetPolicy: Token/cost budget enforcement
- PermissionPolicy: Tool access control
- QualityGatePolicy: Stage validation gates

Reference: Claude SDK's budget and permission systems.
"""

from claude_agent_sdk import PermissionMode

from agent.policies.budget import (
    BudgetPolicy,
    BudgetExceededError,
)
from agent.policies.permission import (
    PermissionPolicy,
    PermissionRule,
    PermissionBehavior,
    PermissionDecision,
    PermissionDeniedError,
)
from agent.policies.quality import (
    QualityGatePolicy,
    QualityGate,
    GateStatus,
    QualityGateFailedError,
)

__all__ = [
    # Budget
    "BudgetPolicy",
    "BudgetExceededError",
    # Permission
    "PermissionPolicy",
    "PermissionRule",
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionMode",
    "PermissionDeniedError",
    # Quality
    "QualityGatePolicy",
    "QualityGate",
    "GateStatus",
    "QualityGateFailedError",
]
