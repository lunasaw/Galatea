"""
Schemas for inspection and read-only operations.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class ServiceHealth(BaseModel):
    """Health status of a platform service."""
    name: str
    status: str = Field(..., description="active, inactive, or unknown")
    endpoint: Optional[str] = None
    port: Optional[int] = None
    checked_at: str


class ProjectStructure(BaseModel):
    """Structure of a training project."""
    project_name: str
    project_path: str
    has_configs: bool
    has_scripts: bool
    has_tests: bool
    config_files: List[str] = Field(default_factory=list)
    script_files: List[str] = Field(default_factory=list)


class MLflowExperimentInfo(BaseModel):
    """MLflow experiment metadata."""
    experiment_id: str
    experiment_name: str
    artifact_location: str
    lifecycle_stage: str
    run_count: int
    tags: Dict[str, str] = Field(default_factory=dict)


class RayClusterStatus(BaseModel):
    """Ray cluster status."""
    is_available: bool
    head_node_id: Optional[str] = None
    total_nodes: int = 0
    total_cpus: int = 0
    total_gpus: int = 0
    total_memory_gb: float = 0.0
    dashboard_url: Optional[str] = None


class InspectionResult(BaseModel):
    """Result of platform inspection."""
    platform_root: str
    services: List[ServiceHealth] = Field(default_factory=list)
    projects: List[ProjectStructure] = Field(default_factory=list)
    mlflow_experiments: List[MLflowExperimentInfo] = Field(default_factory=list)
    ray_status: Optional[RayClusterStatus] = None
    summary: str
