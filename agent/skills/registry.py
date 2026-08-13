"""Claude Code-compatible Skill discovery and SDK option helpers."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - repository environment includes PyYAML
    yaml = None

SkillSource = Literal["project", "legacy-codex", "plugin"]
SkillsOption = list[str] | Literal["all"] | None

DEFAULT_PLUGIN_NAME = "galatea-skills"
DEFAULT_PLUGIN_DIRNAME = ".claude-plugin"
_SAFE_SKILL_NAME_PATTERN = re.compile(r"^[^\s/(),:*\\\x00-\x1f\x7f-\x9f]+(?::[^\s/(),:*\\\x00-\x1f\x7f-\x9f]+)?$")


@dataclass(frozen=True)
class SkillSpec:
    """One discovered Skill, aligned with Claude Code's SKILL.md directory format."""

    name: str
    description: str
    path: Path
    base_dir: Path
    source: SkillSource
    display_name: Optional[str] = None
    allowed_tools: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    model: Optional[str] = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    context: Optional[str] = None
    agent: Optional[str] = None
    effort: Optional[str | int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_name(self) -> str:
        """Name passed to ``ClaudeAgentOptions.skills`` and ``Skill(name)`` rules."""
        return self.name

    def matches_path(self, relative_path: str) -> bool:
        """Return whether this conditional skill applies to a project path."""
        if not self.paths:
            return True
        normalized = relative_path.strip().lstrip("./")
        return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in self.paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "base_dir": str(self.base_dir),
            "source": self.source,
            "display_name": self.display_name,
            "allowed_tools": list(self.allowed_tools),
            "paths": list(self.paths),
            "when_to_use": self.when_to_use,
            "version": self.version,
            "model": self.model,
            "user_invocable": self.user_invocable,
            "disable_model_invocation": self.disable_model_invocation,
            "context": self.context,
            "agent": self.agent,
            "effort": self.effort,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SkillRuntimeConfig:
    """Resolved Skill runtime configuration for Claude SDK sessions."""

    skills: SkillsOption = None
    plugins: tuple[dict[str, str], ...] = ()
    add_dirs: tuple[Path, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    discovered: tuple[SkillSpec, ...] = ()

    def names(self) -> list[str]:
        if self.skills == "all":
            return [skill.name for skill in self.discovered]
        if self.skills is None:
            return []
        return list(self.skills)


class SkillRegistry:
    """Discover repository Skills without reimplementing the SDK transport."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.project_skills_dir = self.project_root / ".claude" / "skills"
        self.legacy_codex_skills_dir = self.project_root / ".codex" / "skills"
        self.plugin_dir = self.project_root

    def discover(
        self,
        *,
        include_project: bool = True,
        include_legacy_codex: bool = True,
        include_plugin: bool = True,
    ) -> list[SkillSpec]:
        """Discover Skills in Claude Code order, deduplicating identical files."""
        specs: list[SkillSpec] = []
        seen_paths: set[Path] = set()
        if include_project:
            specs.extend(self._load_skills_dir(self.project_skills_dir, "project", seen_paths))
        if include_legacy_codex:
            specs.extend(self._load_legacy_codex_skills(seen_paths))
        if include_plugin:
            specs.extend(self._load_plugin_skills(set()))
        return specs

    def resolve(
        self,
        names: Iterable[str] | Literal["all"] | None = None,
        *,
        include_legacy_codex: bool = True,
        include_plugin: bool = True,
        sync_legacy_codex: bool = False,
    ) -> SkillRuntimeConfig:
        """Build SDK-native ``skills``/``plugins``/``allowed_tools`` configuration."""
        if sync_legacy_codex:
            sync_codex_skills_to_claude(self.project_root)

        discovered = self.discover(
            include_project=True,
            include_legacy_codex=include_legacy_codex,
            include_plugin=include_plugin,
        )
        available_names = [skill.name for skill in discovered]
        if names == "all":
            selected: SkillsOption = "all"
        elif names is None:
            selected = None
        elif isinstance(names, str):
            raise ValueError(
                "skills must be a list of skill names or \"all\"; "
                f"got bare string {names!r}"
            )
        else:
            selected = unique_preserve_order(names)
            missing = [name for name in selected if name not in available_names]
            if missing:
                raise ValueError(
                    "Unknown Skill(s): "
                    + ", ".join(missing)
                    + ". Available Skills: "
                    + ", ".join(available_names)
                )

        plugins: list[dict[str, str]] = []
        if include_plugin and _has_plugin_manifest(self.plugin_dir):
            plugins.append({"type": "local", "path": str(self.plugin_dir)})

        return SkillRuntimeConfig(
            skills=selected,
            plugins=tuple(plugins),
            allowed_tools=tuple(skill_permission_rules(selected, discovered)),
            discovered=tuple(discovered),
        )

    def _load_skills_dir(
        self,
        skills_dir: Path,
        source: SkillSource,
        seen_paths: set[Path],
        *,
        name_prefix: str = "",
    ) -> list[SkillSpec]:
        if not skills_dir.is_dir():
            return []
        specs: list[SkillSpec] = []
        for entry in sorted(skills_dir.iterdir(), key=lambda path: path.name):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.is_file():
                continue
            resolved_file = skill_file.resolve()
            if resolved_file in seen_paths:
                continue
            seen_paths.add(resolved_file)
            specs.append(_read_skill_file(skill_file, name_prefix + entry.name, source))
        return specs

    def _load_legacy_codex_skills(self, seen_paths: set[Path]) -> list[SkillSpec]:
        return self._load_skills_dir(self.legacy_codex_skills_dir, "legacy-codex", seen_paths)

    def _load_plugin_skills(self, seen_paths: set[Path]) -> list[SkillSpec]:
        manifest = _load_plugin_manifest(self.plugin_dir)
        if not manifest:
            return []
        plugin_name = str(manifest.get("name") or DEFAULT_PLUGIN_NAME)
        specs: list[SkillSpec] = []
        default_skills_dir = self.plugin_dir / "skills"
        specs.extend(
            self._load_skills_dir(
                default_skills_dir,
                "plugin",
                seen_paths,
                name_prefix=f"{plugin_name}:",
            )
        )
        for extra_dir in _manifest_skill_dirs(manifest, self.plugin_dir):
            specs.extend(
                self._load_skills_dir(
                    extra_dir,
                    "plugin",
                    seen_paths,
                    name_prefix=f"{plugin_name}:",
                )
            )
        return specs


def resolve_skill_runtime(
    project_root: Path,
    skills: Iterable[str] | Literal["all"] | None,
    *,
    include_legacy_codex: bool = True,
    include_plugin: bool = True,
    sync_legacy_codex: bool = False,
) -> SkillRuntimeConfig:
    """Convenience wrapper for callers that do not need a persistent registry."""
    return SkillRegistry(project_root).resolve(
        skills,
        include_legacy_codex=include_legacy_codex,
        include_plugin=include_plugin,
        sync_legacy_codex=sync_legacy_codex,
    )


def skill_permission_rules(
    skills: SkillsOption,
    discovered: Iterable[SkillSpec] = (),
) -> list[str]:
    """Return Claude Code permission rules for the selected Skills."""
    if skills is None:
        return []
    if skills == "all":
        return ["Skill"]
    return [f"Skill({name})" for name in unique_preserve_order(skills)]


def validate_skill_name(name: str) -> None:
    """Validate names before they become ``Skill(name)`` permission rules."""
    if not isinstance(name, str):
        raise TypeError(f"Skill names must be strings, got {type(name).__name__}: {name!r}")
    if not name:
        raise ValueError("Skill names must be non-empty strings")
    if name.strip() != name:
        raise ValueError(f"Skill name has surrounding whitespace: {name!r}")
    if name == "*":
        raise ValueError('Invalid skill name "*": use skills="all"')
    if not _SAFE_SKILL_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid Skill name for SDK permission rule: {name!r}")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in name):
        raise ValueError(f"Invalid Skill name contains surrogate code point: {name!r}")


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        validate_skill_name(value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def sync_codex_skills_to_claude(project_root: Path) -> list[Path]:
    """Mirror legacy ``.codex/skills`` into ``.claude/skills`` via symlinks.

    Claude Code discovers project Skills from ``.claude/skills/<name>/SKILL.md``.
    The repository still keeps Codex Skills under ``.codex/skills``; this helper
    creates non-destructive symlinks so the SDK's native Skill tool can load them.
    """
    project_root = project_root.resolve()
    source_dir = project_root / ".codex" / "skills"
    target_dir = project_root / ".claude" / "skills"
    if not source_dir.is_dir():
        return []
    target_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for source_skill_dir in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if not (source_skill_dir / "SKILL.md").is_file():
            continue
        target_skill_dir = target_dir / source_skill_dir.name
        if target_skill_dir.exists() or target_skill_dir.is_symlink():
            continue
        target_skill_dir.symlink_to(source_skill_dir.resolve(), target_is_directory=True)
        created.append(target_skill_dir)
    return created


def ensure_local_skill_plugin(
    project_root: Path,
    *,
    plugin_name: str = DEFAULT_PLUGIN_NAME,
    description: str = "Galatea repository Skills bridged from .codex/skills",
) -> Path:
    """Create or update a minimal local plugin manifest for repository Skills."""
    project_root = project_root.resolve()
    manifest_dir = project_root / DEFAULT_PLUGIN_DIRNAME
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "plugin.json"
    payload = {
        "name": plugin_name,
        "description": description,
        "version": "0.1.0",
        "skills": "./.codex/skills",
    }
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        existing.update({key: value for key, value in payload.items() if key not in existing})
        existing["skills"] = existing.get("skills") or payload["skills"]
        payload = existing
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _read_skill_file(path: Path, skill_name: str, source: SkillSource) -> SkillSpec:
    validate_skill_name(skill_name)
    raw = path.read_text(encoding="utf-8")
    frontmatter, markdown = _parse_frontmatter(raw)
    description = _coerce_description(frontmatter.get("description")) or _extract_description(markdown, skill_name)
    return SkillSpec(
        name=skill_name,
        description=description,
        path=path,
        base_dir=path.parent,
        source=source,
        display_name=_optional_str(frontmatter.get("name")),
        allowed_tools=tuple(_parse_allowed_tools(frontmatter.get("allowed-tools"))),
        paths=tuple(_parse_paths(frontmatter.get("paths"))),
        when_to_use=_optional_str(frontmatter.get("when_to_use")),
        version=_optional_str(frontmatter.get("version")),
        model=_optional_str(frontmatter.get("model")),
        user_invocable=_parse_bool(frontmatter.get("user-invocable"), default=True),
        disable_model_invocation=_parse_bool(frontmatter.get("disable-model-invocation"), default=False),
        context=_optional_str(frontmatter.get("context")),
        agent=_optional_str(frontmatter.get("agent")),
        effort=frontmatter.get("effort") if isinstance(frontmatter.get("effort"), int) else _optional_str(frontmatter.get("effort")),
        metadata={"frontmatter": frontmatter},
    )


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n") and not raw.startswith("---\r\n"):
        return {}, raw
    lines = raw.splitlines(keepends=True)
    end_index: Optional[int] = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, raw
    frontmatter_text = "".join(lines[1:end_index])
    markdown = "".join(lines[end_index + 1 :])
    if yaml is None:
        return {}, markdown
    parsed = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(parsed, dict):
        return {}, markdown
    return parsed, markdown


def _coerce_description(value: Any) -> Optional[str]:
    if value is None:
        return None
    description = str(value).strip()
    return description or None


def _extract_description(markdown: str, skill_name: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            continue
        return stripped[:240]
    return f"Skill from {skill_name}"


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_allowed_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _parse_paths(value: Any) -> list[str]:
    if value is None:
        return []
    raw_parts: list[str]
    if isinstance(value, str):
        raw_parts = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    elif isinstance(value, list):
        raw_parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raw_parts = []
    patterns = [part[:-3] if part.endswith("/**") else part for part in raw_parts]
    if not patterns or all(pattern == "**" for pattern in patterns):
        return []
    return patterns


def _has_plugin_manifest(plugin_dir: Path) -> bool:
    return (plugin_dir / DEFAULT_PLUGIN_DIRNAME / "plugin.json").is_file() or (plugin_dir / "plugin.json").is_file()


def _load_plugin_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    candidates = [plugin_dir / DEFAULT_PLUGIN_DIRNAME / "plugin.json", plugin_dir / "plugin.json"]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
    return None


def _manifest_skill_dirs(manifest: dict[str, Any], plugin_root: Path) -> list[Path]:
    value = manifest.get("skills")
    if value is None:
        return []
    raw_paths = value if isinstance(value, list) else [value]
    result: list[Path] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.startswith("./"):
            continue
        full_path = plugin_root / raw_path
        if full_path.is_dir():
            result.append(full_path)
    return result
