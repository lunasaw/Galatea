#!/usr/bin/env python3
"""
Simple test of Galatea inspection tools without full agent runtime.

Demonstrates that the inspection tools work independently.
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.tools.inspection import (
    list_training_projects,
    inspect_project_structure,
    check_service_health,
    inspect_ray_status,
)


def main():
    """Test inspection tools directly."""
    print("=" * 70)
    print("Galatea Inspection Tools - Direct Test")
    print("=" * 70)
    print()

    project_root = "/data/ai/chenzhangyue/code/galatea"

    # Test 1: List projects
    print("1. List Training Projects")
    print("-" * 70)
    projects = list_training_projects(project_root)
    print(f"Found {len(projects)} projects:")
    for p in projects:
        print(f"  - {p}")
    print()

    # Test 2: Inspect ray-cats-and-dogs
    print("2. Inspect ray-cats-and-dogs Project")
    print("-" * 70)
    result = inspect_project_structure(project_root, "ray-cats-and-dogs")
    print(f"Project path: {result['project_path']}")
    print(f"Has configs: {result['has_configs']}")
    print(f"Has scripts: {result['has_scripts']}")
    print(f"Has tests: {result['has_tests']}")
    print(f"Config files: {', '.join(result['config_files'])}")
    print(f"Script files: {', '.join(result['script_files'])}")
    print()

    # Test 3: Check MLflow service
    print("3. Check MLflow Service Health")
    print("-" * 70)
    result = check_service_health("mlflow", 5000)
    print(f"Service: {result['name']}")
    print(f"Status: {result['status']}")
    print(f"Port: {result['port']}")
    print()

    # Test 4: Check Ray status
    print("4. Check Ray Cluster Status")
    print("-" * 70)
    result = inspect_ray_status()
    if result['is_available']:
        print("✅ Ray cluster is available")
        print(f"Output preview: {result.get('raw_output', 'N/A')[:200]}")
    else:
        print(f"❌ Ray cluster unavailable: {result.get('error', 'Unknown')}")
    print()

    print("=" * 70)
    print("✅ All inspection tools tested successfully")
    print("=" * 70)


if __name__ == "__main__":
    main()
