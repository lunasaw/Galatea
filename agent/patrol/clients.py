"""Protocol-style client interfaces and fakes for offline patrol tests."""

from __future__ import annotations

from typing import Any, Callable, Dict, Protocol


class PatrolToolCallable(Protocol):
    """Callable shape used by deterministic patrol tool adapters."""

    def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        ...


class FakePatrolTools:
    """Small fake tool collection for scripted patrol replay."""

    def __init__(self, **overrides: Callable[..., Dict[str, Any]]) -> None:
        self.overrides = overrides

    def call(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        if tool_name not in self.overrides:
            raise KeyError(f"No fake patrol tool registered: {tool_name}")
        return self.overrides[tool_name](**kwargs)
