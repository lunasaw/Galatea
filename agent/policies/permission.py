"""Permission policy for SDK and direct tool access control."""

from __future__ import annotations

import fnmatch
import json
from typing import Dict, Any, Optional, List, Literal
from dataclasses import dataclass

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from claude_agent_sdk import ToolPermissionContext


PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]


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

        candidates = _input_match_candidates(tool_input)
        pattern = self.rule_content
        return any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates)


class PermissionPolicy:
    """
    Permission policy for tool access control.

    Manages permission rules and enforces access control.
    """

    def __init__(
        self,
        mode: PermissionMode = "default",
        default_behavior: PermissionBehavior = "ask",
    ):
        """
        Initialize permission policy.

        Args:
            mode: Permission mode
            default_behavior: Default behavior when no rule matches
        """
        self.mode = mode
        self.default_behavior = default_behavior
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
    ) -> PermissionBehavior:
        """
        Check if tool use is permitted.

        Args:
            tool_name: Tool being used
            tool_input: Tool input parameters

        Returns:
            Permission behavior (allow/deny/ask)

        """
        if self.mode == "bypassPermissions":
            return "allow"
        if self.mode == "plan":
            return "deny"

        # Deny rules win over allow rules.
        matching = [rule for rule in self.rules if rule.matches(tool_name, tool_input)]
        for rule in matching:
            if rule.behavior == "deny":
                return "deny"
        for rule in matching:
            if rule.behavior == "allow":
                return "allow"
        for rule in matching:
            if rule.behavior == "ask":
                return "ask"

        if self.mode == "dontAsk":
            return "deny"
        if self.mode == "acceptEdits" and tool_name in {"Edit", "Write", "MultiEdit"}:
            return "allow"
        return self.default_behavior

    def set_mode(self, mode: PermissionMode) -> None:
        """
        Set permission mode.

        Args:
            mode: New permission mode

        """
        self.mode = mode

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
        if behavior == "ask":
            return f"{tool_name} requires explicit approval by Galatea policy."
        if self.mode == "plan":
            return "Plan mode blocks tool execution."
        if self.mode == "dontAsk":
            return f"{tool_name} is not pre-approved and dontAsk mode denies prompts."
        return f"{tool_name} is denied by Galatea permission policy."

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Claude SDK ``can_use_tool`` adapter for ask-mode permission requests."""
        behavior = self.check_permission(tool_name, tool_input)
        reason = self.explain_permission(tool_name, tool_input)
        if behavior == "allow":
            return PermissionResultAllow()
        return PermissionResultDeny(message=reason, interrupt=False)

    @classmethod
    def for_galatea(
        cls,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        mode: PermissionMode = "dontAsk",
        default_behavior: PermissionBehavior = "deny",
    ) -> "PermissionPolicy":
        """Create a safe Galatea default policy with exact allow and deny rules."""
        policy = cls(mode=mode, default_behavior=default_behavior)
        effective_disallowed_tools = (
            list(DEFAULT_DISALLOWED_TOOLS)
            if disallowed_tools is None
            else disallowed_tools
        )
        for tool_name in effective_disallowed_tools:
            policy.add_rule(PermissionRule(tool_name=tool_name, behavior="deny"))
        for tool_name in allowed_tools or []:
            policy.add_rule(PermissionRule(tool_name=tool_name, behavior="allow"))
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


def _input_match_candidates(tool_input: Dict[str, Any]) -> List[str]:
    """Return stable string candidates for rule_content matching."""
    candidates: List[str] = []
    for key in (
        "command",
        "file_path",
        "path",
        "notebook_path",
        "project_root",
        "uri",
        "source_uri",
        "target_uri",
    ):
        value = tool_input.get(key)
        if isinstance(value, str):
            candidates.append(value)
    try:
        candidates.append(json.dumps(tool_input, sort_keys=True, ensure_ascii=False))
    except TypeError:
        candidates.append(str(tool_input))
    return candidates
