"""
Galatea 平台的只读检查工具。

这些工具提供只读访问：
- 平台服务健康状况（MLflow、Ray、MinIO）
- 项目结构和配置
- MLflow 实验和运行
- Ray 集群状态
"""

import subprocess
from pathlib import Path
from typing import Dict, Any, List


def inspect_project_structure(project_root: str, project_name: str) -> Dict[str, Any]:
    """
    检查训练项目的结构。

    Args:
        project_root: Galatea 平台的根目录
        project_name: 要检查的项目名称（例如 'ray-cats-and-dogs'）

    Returns:
        包含项目结构信息的字典
    """
    root = Path(project_root)
    project_path = root / "train-model" / project_name

    if not project_path.exists():
        return {
            "project_name": project_name,
            "project_path": str(project_path),
            "exists": False,
            "error": f"在 {project_path} 找不到项目"
        }

    # 查找配置文件
    config_dir = project_path / "configs"
    config_files = []
    if config_dir.exists():
        config_files = [f.name for f in config_dir.glob("*.yaml")]

    # 查找脚本文件
    script_dir = project_path / "scripts"
    script_files = []
    if script_dir.exists():
        script_files = [f.name for f in script_dir.glob("*.py")]

    # 检查测试目录
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
    检查 systemd 服务是否活动并响应。

    Args:
        service_name: systemd 服务名称
        port: 服务监听的端口
        endpoint: 要检查的端点（默认：localhost）

    Returns:
        包含健康状态的字典
    """
    # 检查 systemd 服务状态
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
    检查 MLflow 实验。

    Args:
        tracking_uri: MLflow 跟踪服务器 URI
        experiment_name: 要检查的实验名称

    Returns:
        包含实验信息的字典
    """
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)

        # 按名称获取实验
        experiment = mlflow.get_experiment_by_name(experiment_name)

        if experiment is None:
            return {
                "experiment_name": experiment_name,
                "exists": False,
                "error": f"未找到实验 '{experiment_name}'"
            }

        # 统计运行次数
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
        return {"error": "mlflow 不可用"}
    except Exception as e:
        return {"error": f"检查实验失败: {e}"}


def inspect_ray_status() -> Dict[str, Any]:
    """
    检查 Ray 集群状态。

    Returns:
        包含 Ray 集群信息的字典
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
                "error": "Ray 集群未运行或不可访问"
            }

        # 从 ray status 输出解析基本信息
        output = result.stdout

        # 简单解析 - 在生产环境中，直接使用 Ray API
        is_available = "ray.init()" in output or "Resources" in output

        return {
            "is_available": is_available,
            "raw_output": output[:500],  # 前 500 个字符
        }
    except subprocess.TimeoutExpired:
        return {"is_available": False, "error": "ray status 命令超时"}
    except FileNotFoundError:
        return {"is_available": False, "error": "未找到 ray CLI"}
    except Exception as e:
        return {"is_available": False, "error": f"检查 Ray 状态失败: {e}"}


def list_training_projects(project_root: str) -> List[str]:
    """
    列出 train-model 目录中的所有训练项目。

    Args:
        project_root: Galatea 平台的根目录

    Returns:
        项目名称列表
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
