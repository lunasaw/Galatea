"""Skill capability support for Galatea agents."""

from agent.skills.registry import (
    DEFAULT_PLUGIN_NAME,
    SkillRegistry,
    SkillRuntimeConfig,
    SkillSpec,
    ensure_local_skill_plugin,
    resolve_skill_runtime,
    skill_permission_rules,
    sync_codex_skills_to_claude,
    unique_preserve_order,
    validate_skill_name,
)

__all__ = [
    "DEFAULT_PLUGIN_NAME",
    "SkillRegistry",
    "SkillRuntimeConfig",
    "SkillSpec",
    "ensure_local_skill_plugin",
    "resolve_skill_runtime",
    "skill_permission_rules",
    "sync_codex_skills_to_claude",
    "unique_preserve_order",
    "validate_skill_name",
]
