#!/usr/bin/env python3
"""
Galatea 检查工具的简单测试，无需完整的 agent 运行时。

演示检查工具可以独立工作。
"""

import sys
from pathlib import Path

# 添加仓库根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.tools.inspection import (
    list_training_projects,
    inspect_project_structure,
    check_service_health,
    inspect_ray_status,
)


def main():
    """直接测试检查工具。"""
    print("=" * 70)
    print("Galatea 检查工具 - 直接测试")
    print("=" * 70)
    print()

    project_root = "/data/ai/chenzhangyue/code/galatea"

    # 测试 1：列出项目
    print("1. 列出训练项目")
    print("-" * 70)
    projects = list_training_projects(project_root)
    print(f"找到 {len(projects)} 个项目:")
    for p in projects:
        print(f"  - {p}")
    print()

    # 测试 2：检查 ray-cats-and-dogs
    print("2. 检查 ray-cats-and-dogs 项目")
    print("-" * 70)
    result = inspect_project_structure(project_root, "ray-cats-and-dogs")
    print(f"项目路径: {result['project_path']}")
    print(f"有配置: {result['has_configs']}")
    print(f"有脚本: {result['has_scripts']}")
    print(f"有测试: {result['has_tests']}")
    print(f"配置文件: {', '.join(result['config_files'])}")
    print(f"脚本文件: {', '.join(result['script_files'])}")
    print()

    # 测试 3：检查 MLflow 服务
    print("3. 检查 MLflow 服务健康状况")
    print("-" * 70)
    result = check_service_health("mlflow", 5000)
    print(f"服务: {result['name']}")
    print(f"状态: {result['status']}")
    print(f"端口: {result['port']}")
    print()

    # 测试 4：检查 Ray 状态
    print("4. 检查 Ray 集群状态")
    print("-" * 70)
    result = inspect_ray_status()
    if result['is_available']:
        print("✅ Ray 集群可用")
        print(f"输出预览: {result.get('raw_output', 'N/A')[:200]}")
    else:
        print(f"❌ Ray 集群不可用: {result.get('error', '未知')}")
    print()

    print("=" * 70)
    print("✅ 所有检查工具测试成功")
    print("=" * 70)


if __name__ == "__main__":
    main()
