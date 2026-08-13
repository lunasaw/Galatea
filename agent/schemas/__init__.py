"""
Structured schemas for agent stage inputs and outputs.
"""

from .common import StageStatus, ArtifactRef, StageEvidence, StageResult
from .inspection import InspectionResult, ProjectStructure, ServiceHealth
from .patrol import (
    ActionLevel,
    AuditEvent,
    EvidenceRecord,
    Finding,
    PatrolFailure,
    PatrolMemory,
    PatrolRunResult,
    RawRef,
    Recommendation,
)

__all__ = [
    "StageStatus",
    "ArtifactRef",
    "StageEvidence",
    "StageResult",
    "InspectionResult",
    "ProjectStructure",
    "ServiceHealth",
    "ActionLevel",
    "AuditEvent",
    "EvidenceRecord",
    "Finding",
    "PatrolFailure",
    "PatrolMemory",
    "PatrolRunResult",
    "RawRef",
    "Recommendation",
]
