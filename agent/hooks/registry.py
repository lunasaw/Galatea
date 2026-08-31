"""Thin registration helper for Claude SDK ``HookMatcher`` objects."""

from __future__ import annotations

from claude_agent_sdk import HookCallback, HookMatcher
from claude_agent_sdk.types import HookEvent


class HookManager:
    """Collect SDK-native callbacks without translating their input or output."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookMatcher]] = {}

    def add_hook(
        self,
        event: HookEvent,
        callback: HookCallback,
        matcher: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._hooks.setdefault(event, []).append(
            HookMatcher(matcher=matcher, hooks=[callback], timeout=timeout)
        )

    def clear_hooks(self, event: HookEvent | None = None) -> None:
        if event is None:
            self._hooks.clear()
        else:
            self._hooks.pop(event, None)

    def extend(self, other: "HookManager") -> None:
        """Append SDK matchers while retaining the runtime's safety hooks."""
        for event, matchers in other.to_sdk_hooks().items():
            self._hooks.setdefault(event, []).extend(matchers)

    def to_sdk_hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """Return a detached container holding the original SDK matchers."""
        return {event: list(matchers) for event, matchers in self._hooks.items()}
