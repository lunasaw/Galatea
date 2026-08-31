"""Galatea-specific permission rules layered under the Claude SDK."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
import json
from typing import Any, Dict, List, Literal, Optional

PermissionBehavior = Literal["allow", "deny"]
PermissionDecision = Literal["allow", "deny", "defer"]


@dataclass
class PermissionRule:
    """
    Permission rule for tool access.

    Reference: Claude SDK's PermissionRuleValue.
    """

    tool_name: str
    behavior: PermissionBehavior
    rule_content: Optional[str] = None  # e.g., file path pattern

    def matches(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """
        Check if rule matches tool use.

        Args:
            tool_name: Tool being used
            tool_input: Tool input parameters

        Returns:
            True if rule matches

        """
        if not _matches_tool_pattern(self.tool_name, tool_name):
            return False
        if self.rule_content is None:
            return True

        if tool_name in {"Bash", "PowerShell"}:
            command = tool_input.get("command")
            return isinstance(command, str) and _matches_shell_rule(self.rule_content, command)
        if tool_name == "Skill":
            skill_name = tool_input.get("skill")
            return isinstance(skill_name, str) and _matches_skill_rule(self.rule_content, skill_name)

        candidates = _input_match_candidates(tool_input)
        pattern = self.rule_content
        return any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates)


class PermissionPolicy:
    """Evaluate Galatea allow/deny rules without emulating SDK modes."""

    def __init__(self) -> None:
        self.rules: List[PermissionRule] = []

    def add_rule(self, rule: PermissionRule) -> None:
        """
        Add permission rule.

        Args:
            rule: Permission rule to add

        """
        self.rules.append(rule)

    def remove_rule(self, tool_name: str, rule_content: Optional[str] = None) -> None:
        """
        Remove permission rule.

        Args:
            tool_name: Tool name
            rule_content: Optional rule content to match

        """
        self.rules = [
            rule
            for rule in self.rules
            if not (
                rule.tool_name == tool_name
                and (rule_content is None or rule.rule_content == rule_content)
            )
        ]

    def check_permission(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> PermissionDecision:
        """
        Check if tool use is permitted.

        Args:
            tool_name: Tool being used
            tool_input: Tool input parameters

        Returns:
            Explicit Galatea decision, or ``defer`` for SDK evaluation.

        """
        # Deny rules win over allow rules.
        matching = [rule for rule in self.rules if rule.matches(tool_name, tool_input)]
        for rule in matching:
            if rule.behavior == "deny":
                return "deny"
        for rule in matching:
            if rule.behavior == "allow":
                return "allow"
        return "defer"

    def get_rules(self, tool_name: Optional[str] = None) -> List[PermissionRule]:
        """
        Get permission rules.

        Args:
            tool_name: Optional tool name filter

        Returns:
            List of matching rules

        """
        if tool_name is None:
            return list(self.rules)
        return [rule for rule in self.rules if _matches_tool_pattern(rule.tool_name, tool_name)]

    def explain_permission(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> str:
        """Return a concise reason for the current permission decision."""
        behavior = self.check_permission(tool_name, tool_input)
        if behavior == "allow":
            return f"{tool_name} is allowed by Galatea permission policy."
        if behavior == "deny":
            return f"{tool_name} is denied by Galatea permission policy."
        return f"{tool_name} has no Galatea rule; defer to Claude SDK permissions."

    @classmethod
    def for_galatea(
        cls,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> "PermissionPolicy":
        """Create a safe Galatea default policy with exact allow and deny rules."""
        policy = cls()
        effective_disallowed_tools = (
            list(DEFAULT_DISALLOWED_TOOLS)
            if disallowed_tools is None
            else disallowed_tools
        )
        for tool_name in effective_disallowed_tools:
            parsed_tool, rule_content = _parse_permission_rule_value(tool_name)
            policy.add_rule(
                PermissionRule(
                    tool_name=parsed_tool,
                    behavior="deny",
                    rule_content=rule_content,
                )
            )
        for tool_name in allowed_tools or []:
            parsed_tool, rule_content = _parse_permission_rule_value(tool_name)
            policy.add_rule(
                PermissionRule(
                    tool_name=parsed_tool,
                    behavior="allow",
                    rule_content=rule_content,
                )
            )
        return policy


class PermissionDeniedError(Exception):
    """Raised when permission is denied."""

    def __init__(
        self,
        tool_name: str,
        reason: str,
    ):
        """
        Initialize permission denied error.

        Args:
            tool_name: Tool that was denied
            reason: Denial reason
        """
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Permission denied for {tool_name}: {reason}")


DEFAULT_DISALLOWED_TOOLS = ["Bash", "Write", "Edit", "MultiEdit"]


def _matches_tool_pattern(pattern: str, tool_name: str) -> bool:
    """Match exact, pipe-separated, and shell-style wildcard tool patterns."""
    for part in pattern.split("|"):
        normalized = part.strip()
        if not normalized:
            continue
        if normalized == tool_name:
            return True
        if fnmatch.fnmatchcase(tool_name, normalized):
            return True
    return False


def _parse_permission_rule_value(rule_string: str) -> tuple[str, Optional[str]]:
    """Parse Claude Code permission specs like ``Bash(git push:*)``."""
    open_index = _find_first_unescaped(rule_string, "(")
    if open_index == -1:
        return rule_string, None

    close_index = _find_last_unescaped(rule_string, ")")
    if close_index == -1 or close_index <= open_index or close_index != len(rule_string) - 1:
        return rule_string, None

    tool_name = rule_string[:open_index].strip()
    if not tool_name:
        return rule_string, None

    raw_content = rule_string[open_index + 1 : close_index]
    if raw_content in {"", "*"}:
        return tool_name, None
    return tool_name, _unescape_rule_content(raw_content)


def _find_first_unescaped(value: str, char: str) -> int:
    for index, current in enumerate(value):
        if current == char and not _is_escaped(value, index):
            return index
    return -1


def _find_last_unescaped(value: str, char: str) -> int:
    for index in range(len(value) - 1, -1, -1):
        if value[index] == char and not _is_escaped(value, index):
            return index
    return -1


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _unescape_rule_content(value: str) -> str:
    result: List[str] = []
    escaping = False
    for char in value:
        if escaping:
            result.append(char)
            escaping = False
        elif char == "\\":
            escaping = True
        else:
            result.append(char)
    if escaping:
        result.append("\\")
    return "".join(result)


def _matches_shell_rule(rule_content: str, command: str) -> bool:
    command = command.strip()
    pattern = rule_content.strip()
    if not command or not pattern:
        return False

    prefix = _permission_rule_extract_prefix(pattern)
    if prefix is not None:
        return command == prefix or command.startswith(f"{prefix} ")

    if _has_unescaped_wildcards(pattern):
        return _matches_wildcard_rule(pattern, command)

    return command == pattern


def _matches_skill_rule(rule_content: str, skill_name: str) -> bool:
    skill_name = skill_name.strip().lstrip("/")
    pattern = rule_content.strip().lstrip("/")
    if not skill_name or not pattern:
        return False
    prefix = _permission_rule_extract_prefix(pattern)
    if prefix is not None:
        return skill_name == prefix or skill_name.startswith(prefix)
    if _has_unescaped_wildcards(pattern):
        return _matches_wildcard_rule(pattern, skill_name)
    return skill_name == pattern


def _permission_rule_extract_prefix(rule_content: str) -> Optional[str]:
    if rule_content.endswith(":*") and len(rule_content) > 2:
        return rule_content[:-2]
    return None


def _has_unescaped_wildcards(pattern: str) -> bool:
    if pattern.endswith(":*"):
        return False
    return any(char == "*" and not _is_escaped(pattern, index) for index, char in enumerate(pattern))


def _matches_wildcard_rule(pattern: str, command: str) -> bool:
    wildcard_count = sum(
        1
        for index, char in enumerate(pattern)
        if char == "*" and not _is_escaped(pattern, index)
    )
    if wildcard_count == 1 and pattern.endswith(" *") and command == pattern[:-2]:
        return True
    return fnmatch.fnmatchcase(command, _unescape_rule_content(pattern))


def _input_match_candidates(tool_input: Dict[str, Any]) -> List[str]:
    """Return stable string candidates for rule_content matching."""
    candidates: List[str] = []
    for key in (
        "command",
        "file_path",
        "path",
        "notebook_path",
        "project_root",
        "skill",
        "uri",
        "source_uri",
        "target_uri",
    ):
        value = tool_input.get(key)
        if isinstance(value, str):
            candidates.append(value)
            if key == "skill" and value.startswith("/"):
                candidates.append(value[1:])
    try:
        candidates.append(json.dumps(tool_input, sort_keys=True, ensure_ascii=False))
    except TypeError:
        candidates.append(str(tool_input))
    return candidates
