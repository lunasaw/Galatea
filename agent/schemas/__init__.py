"""
Structured schemas for agent stage inputs and outputs.
"""

from .common import StageStatus, ArtifactRef, StageEvidence, StageResult
from .inspection import InspectionResult, ProjectStructure, ServiceHealth

__all__ = [
    "StageStatus",
    "ArtifactRef",
    "StageEvidence",
    "StageResult",
    "InspectionResult",
    "ProjectStructure",
    "ServiceHealth",
]
