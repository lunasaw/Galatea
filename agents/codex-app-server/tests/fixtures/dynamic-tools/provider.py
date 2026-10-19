"""Synthetic tool handlers for generic Host regression tests; no platform imports."""
import json
from pathlib import Path
from codex_agent.providers import ToolProvider


def create_provider(options):
    names = json.loads((Path(__file__).parent / 'tools.json').read_text())['tools']
    return ToolProvider(handlers={name: lambda context, arguments: {'observed': True} for name in names})
