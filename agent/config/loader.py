"""
Galatea Agent 运行时的配置加载器。

从 ~/.claude/settings.json 和环境变量加载设置。
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


def load_claude_settings() -> Dict[str, Any]:
    """
    从 ~/.claude/settings.json 加载设置。

    Returns:
        包含设置的字典，如果文件不存在则返回空字典
    """
    settings_path = Path.home() / ".claude" / "settings.json"

    if not settings_path.exists():
        return {}

    try:
        with open(settings_path) as f:
            return json.load(f)
    except Exception as e:
        print(f"警告：加载 {settings_path} 失败: {e}")
        return {}


def get_anthropic_config() -> Dict[str, Optional[str]]:
    """
    从 settings.json 和环境变量获取 Anthropic API 配置。

    优先级：环境变量 > settings.json

    Returns:
        包含 'api_key' 和 'base_url' 的字典（可能为 None）
    """
    settings = load_claude_settings()
    env_vars = settings.get("env", {})

    return {
        "api_key": os.getenv("ANTHROPIC_API_KEY") or env_vars.get("ANTHROPIC_API_KEY"),
        "base_url": os.getenv("ANTHROPIC_BASE_URL") or env_vars.get("ANTHROPIC_BASE_URL"),
    }


def apply_anthropic_config_to_env():
    """
    将 settings.json 中的 Anthropic 配置应用到环境变量。

    仅设置环境中尚未定义的变量。
    """
    settings = load_claude_settings()
    env_vars = settings.get("env", {})

    for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"]:
        if key in env_vars and key not in os.environ:
            os.environ[key] = env_vars[key]
