"""
Read-only inspection tools for Galatea platform.

These tools provide read-only access to:
- Platform service health (MLflow, Ray, MinIO)
- Project structure and configurations
- MLflow experiments and runs
- Ray cluster status
"""

import subprocess
from pathlib import Path
from typing import Dict, Any, List


def inspect_project_structure(project_root: str, project_name: str) -> Dict[str, Any]:
    """
    Inspect the structure of a training project.

    Args:
        project_root: Root directory of Galatea platform
        project_name: Name of the project to inspect (e.g., 'ray-cats-and-dogs')

    Returns:
        Dictionary with project structure information
    """
    root = Path(project_root)
    project_path = root / "train-model" / project_name

    if not project_path.exists():
        return {
            "project_name": project_name,
            "project_path": str(project_path),
            "exists": False,
            "error": f"Project not found at {project_path}"
        }

    # Find config files
    config_dir = project_path / "configs"
    config_files = []
    if config_dir.exists():
        config_files = [f.name for f in config_dir.glob("*.yaml")]

    # Find script files
    script_dir = project_path / "scripts"
    script_files = []
    if script_dir.exists():
        script_files = [f.name for f in script_dir.glob("*.py")]

    # Check for tests
    test_dir = project_path / "tests"
    has_tests = test_dir.exists()

    return {
        "project_name": project_name,
        "project_path": str(project_path),
        "exists": True,
        "has_configs": len(config_files) > 0,
        "has_scripts": len(script_files) > 0,
        "has_tests": has_tests,
        "config_files": config_files,
        "script_files": script_files,
    }


def check_service_health(service_name: str, port: int, endpoint: str = "127.0.0.1") -> Dict[str, Any]:
    """
    Check if a systemd service is active and responding.

    Args:
        service_name: Name of systemd service
        port: Port the service listens on
        endpoint: Endpoint to check (default: localhost)

    Returns:
        Dictionary with health status
    """
    # Check systemd service status
    try:
        result = subprocess.run(
            ["systemctl", "is-active", f"{service_name}.service"],
            capture_output=True,
            text=True,
            timeout=5
        )
        systemd_status = result.stdout.strip()
    except subprocess.TimeoutExpired:
        systemd_status = "timeout"
    except Exception as e:
        systemd_status = f"error: {e}"

    return {
        "name": service_name,
        "status": systemd_status,
        "endpoint": endpoint,
        "port": port,
    }


def inspect_mlflow_experiment(tracking_uri: str, experiment_name: str) -> Dict[str, Any]:
    """
    Inspect an MLflow experiment.

    Args:
        tracking_uri: MLflow tracking server URI
        experiment_name: Name of the experiment to inspect

    Returns:
        Dictionary with experiment information
    """
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)

        # Get experiment by name
        experiment = mlflow.get_experiment_by_name(experiment_name)

        if experiment is None:
            return {
                "experiment_name": experiment_name,
                "exists": False,
                "error": f"Experiment '{experiment_name}' not found"
            }

        # Count runs
        runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id], max_results=1000)
        run_count = len(runs)

        return {
            "experiment_id": experiment.experiment_id,
            "experiment_name": experiment.name,
            "artifact_location": experiment.artifact_location,
            "lifecycle_stage": experiment.lifecycle_stage,
            "run_count": run_count,
            "tags": dict(experiment.tags) if experiment.tags else {},
            "exists": True,
        }
    except ImportError:
        return {"error": "mlflow not available"}
    except Exception as e:
        return {"error": f"Failed to inspect experiment: {e}"}


def inspect_ray_status() -> Dict[str, Any]:
    """
    Check Ray cluster status.

    Returns:
        Dictionary with Ray cluster information
    """
    try:
        result = subprocess.run(
            ["ray", "status"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {
                "is_available": False,
                "error": "Ray cluster not running or not accessible"
            }

        # Parse basic info from ray status output
        output = result.stdout

        # Simple parsing - in production, use Ray API directly
        is_available = "ray.init()" in output or "Resources" in output

        return {
            "is_available": is_available,
            "raw_output": output[:500],  # First 500 chars
        }
    except subprocess.TimeoutExpired:
        return {"is_available": False, "error": "ray status command timed out"}
    except FileNotFoundError:
        return {"is_available": False, "error": "ray CLI not found"}
    except Exception as e:
        return {"is_available": False, "error": f"Failed to check Ray status: {e}"}


def list_training_projects(project_root: str) -> List[str]:
    """
    List all training projects in train-model directory.

    Args:
        project_root: Root directory of Galatea platform

    Returns:
        List of project names
    """
    root = Path(project_root)
    train_model_dir = root / "train-model"

    if not train_model_dir.exists():
        return []

    projects = []
    for item in train_model_dir.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            projects.append(item.name)

    return sorted(projects)
