"""Display and preflight helpers for SDK/Claude Code Skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - repository environment includes PyYAML
    yaml = None

SkillSource = Literal["project", "legacy-codex", "plugin"]
DEFAULT_PLUGIN_NAME = "galatea-skills"
DEFAULT_PLUGIN_DIRNAME = ".claude-plugin"


@dataclass(frozen=True)
class SkillSpec:
    """One discovered Skill, aligned with Claude Code's SKILL.md directory format."""

    name: str
    description: str
    path: Path
    base_dir: Path
    source: SkillSource
    display_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_name(self) -> str:
        """Display the SDK/Claude Code name represented by this local file."""
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "base_dir": str(self.base_dir),
            "source": self.source,
            "display_name": self.display_name,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SkillPreflightReport:
    """Local filesystem evidence; never an authorization decision."""

    requested: tuple[str, ...] | Literal["all"]
    discovered: tuple[SkillSpec, ...]
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.missing


class SkillRegistry:
    """Inspect Skill files for UI display and optional startup preflight.

    Claude SDK ``skills`` and ``plugins`` remain the runtime source of truth.
    Discovery here must never be converted into ``allowed_tools`` rules.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.project_skills_dir = self.project_root / ".claude" / "skills"
        self.legacy_codex_skills_dir = self.project_root / ".codex" / "skills"
        self.plugin_dir = self.project_root

    def discover(
        self,
        *,
        include_project: bool = True,
        include_legacy_codex: bool = False,
        include_plugin: bool = False,
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

    def preflight(
        self,
        names: Iterable[str] | Literal["all"],
        *,
        include_legacy_codex: bool = False,
        include_plugin: bool = False,
    ) -> SkillPreflightReport:
        """Check local discovery prerequisites without granting runtime access."""
        discovered = self.discover(
            include_project=True,
            include_legacy_codex=include_legacy_codex,
            include_plugin=include_plugin,
        )
        available_names = {skill.name for skill in discovered}
        if names == "all":
            requested: tuple[str, ...] | Literal["all"] = "all"
            missing: tuple[str, ...] = ()
        elif isinstance(names, str):
            raise ValueError(
                "skills must be a list of skill names or \"all\"; "
                f"got bare string {names!r}"
            )
        else:
            requested = tuple(dict.fromkeys(names))
            missing = tuple(name for name in requested if name not in available_names)

        warnings: list[str] = []
        if include_legacy_codex:
            warnings.append(
                "Legacy .codex/skills discovery is display-only; expose Skills through "
                ".claude/skills or an explicit SDK plugin."
            )
        return SkillPreflightReport(
            requested=requested,
            discovered=tuple(discovered),
            missing=missing,
            warnings=tuple(warnings),
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


def _read_skill_file(path: Path, skill_name: str, source: SkillSource) -> SkillSpec:
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
