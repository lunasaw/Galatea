"""
Common data structures shared across all agent stages.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class StageStatus(str, Enum):
    """Stage execution status."""
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_APPROVAL = "needs_approval"
    SKIPPED = "skipped"


class ArtifactRef(BaseModel):
    """Reference to a versioned artifact."""
    uri: str = Field(..., description="Artifact URI (mlflow-artifacts:// or s3://)")
    digest: Optional[str] = Field(None, description="Content digest (sha256:...)")
    kind: str = Field(..., description="Artifact type")
    created_by: Optional[str] = Field(None, description="Stage run ID or MLflow run ID")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StageEvidence(BaseModel):
    """Evidence from a validation check or quality gate."""
    name: str = Field(..., description="Evidence name")
    status: str = Field(..., description="pass, fail, or warning")
    summary: str = Field(..., description="Human-readable summary")
    artifact: Optional[ArtifactRef] = None
    metrics: Dict[str, float] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ApprovalRequest(BaseModel):
    """Request for human approval of a high-risk action."""
    approval_id: str
    type: str = Field(..., description="Action type requiring approval")
    risk: str = Field(..., description="low, medium, or high")
    summary: str
    proposed_action: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[StageEvidence] = Field(default_factory=list)
    expires_at: Optional[str] = None


class StageResult(BaseModel):
    """Base structure for all stage results."""
    stage: str = Field(..., description="Stage name: data, training, or inference")
    status: StageStatus
    stage_run_id: str
    project_name: str
    objective: Optional[str] = None
    evidence: List[StageEvidence] = Field(default_factory=list)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_request: Optional[ApprovalRequest] = None
    next_action: Optional[str] = None
