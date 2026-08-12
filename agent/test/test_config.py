#!/usr/bin/env python3
"""
Test configuration loading from ~/.claude/settings.json
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.config import load_claude_settings, get_anthropic_config, apply_anthropic_config_to_env


def test_load_settings():
    """Test loading settings.json"""
    print("=" * 70)
    print("Test 1: Load Claude Settings")
    print("=" * 70)

    settings = load_claude_settings()

    if settings:
        print(f"✅ Loaded settings from ~/.claude/settings.json")
        print(f"   - Model: {settings.get('model', 'not set')}")
        print(f"   - Theme: {settings.get('theme', 'not set')}")
        print(f"   - Effort Level: {settings.get('effortLevel', 'not set')}")

        env_vars = settings.get("env", {})
        if env_vars:
            print(f"   - Environment variables defined: {len(env_vars)}")
            for key in env_vars.keys():
                if "KEY" in key or "TOKEN" in key:
                    print(f"     • {key}: ****** (hidden)")
                else:
                    print(f"     • {key}: {env_vars[key]}")
    else:
        print("⚠️  No settings.json found")

    print()


def test_get_config():
    """Test getting Anthropic configuration"""
    print("=" * 70)
    print("Test 2: Get Anthropic Configuration")
    print("=" * 70)

    config = get_anthropic_config()

    if config["api_key"]:
        print(f"✅ API Key found: {config['api_key'][:20]}... (length: {len(config['api_key'])})")
    else:
        print("❌ API Key not found")

    if config["base_url"]:
        print(f"✅ Base URL found: {config['base_url']}")
    else:
        print("⚠️  Base URL not set (will use default Anthropic API)")

    print()


def test_apply_to_env():
    """Test applying config to environment"""
    print("=" * 70)
    print("Test 3: Apply Configuration to Environment")
    print("=" * 70)

    import os

    # Save original values
    original_key = os.environ.get("ANTHROPIC_API_KEY")
    original_url = os.environ.get("ANTHROPIC_BASE_URL")

    # Clear environment
    if "ANTHROPIC_API_KEY" in os.environ:
        del os.environ["ANTHROPIC_API_KEY"]
    if "ANTHROPIC_BASE_URL" in os.environ:
        del os.environ["ANTHROPIC_BASE_URL"]

    print("Before apply:")
    print(f"  ANTHROPIC_API_KEY in env: {'ANTHROPIC_API_KEY' in os.environ}")
    print(f"  ANTHROPIC_BASE_URL in env: {'ANTHROPIC_BASE_URL' in os.environ}")
    print()

    # Apply from settings.json
    apply_anthropic_config_to_env()

    print("After apply:")
    print(f"  ANTHROPIC_API_KEY in env: {'ANTHROPIC_API_KEY' in os.environ}")
    if "ANTHROPIC_API_KEY" in os.environ:
        print(f"    Value: {os.environ['ANTHROPIC_API_KEY'][:20]}...")

    print(f"  ANTHROPIC_BASE_URL in env: {'ANTHROPIC_BASE_URL' in os.environ}")
    if "ANTHROPIC_BASE_URL" in os.environ:
        print(f"    Value: {os.environ['ANTHROPIC_BASE_URL']}")

    # Restore original values
    if original_key:
        os.environ["ANTHROPIC_API_KEY"] = original_key
    if original_url:
        os.environ["ANTHROPIC_BASE_URL"] = original_url

    print()


def main():
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 18 + "GALATEA AGENT CONFIG TEST" + " " * 25 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    test_load_settings()
    test_get_config()
    test_apply_to_env()

    print("=" * 70)
    print("✅ All configuration tests completed!")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
