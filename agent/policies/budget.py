"""
Budget policy for cost control.

Implements token/cost budgets and budget enforcement.
Reference: Claude SDK's max_budget_usd.
"""

from typing import Optional


class BudgetPolicy:
    """
    Budget policy for controlling API costs.

    Reference: Claude SDK's max_budget_usd option.
    """

    def __init__(
        self,
        max_budget_usd: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Initialize budget policy.

        Args:
            max_budget_usd: Maximum USD budget
            max_tokens: Maximum token budget
        """
        self.max_budget_usd = max_budget_usd
        self.max_tokens = max_tokens

        self.current_cost_usd = 0.0
        self.current_tokens = 0

    def check_budget(self) -> bool:
        """
        Check if budget is exceeded.

        Returns:
            True if budget allows more calls, False if exceeded

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Budget checking")

    def record_usage(
        self,
        cost_usd: float,
        tokens: int,
    ) -> None:
        """
        Record API usage.

        Args:
            cost_usd: Cost in USD
            tokens: Token count

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Usage recording")

    def remaining_budget_usd(self) -> Optional[float]:
        """
        Get remaining USD budget.

        Returns:
            Remaining budget or None if unlimited

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Budget calculation")

    def remaining_tokens(self) -> Optional[int]:
        """
        Get remaining token budget.

        Returns:
            Remaining tokens or None if unlimited

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Token calculation")

    def reset(self) -> None:
        """
        Reset budget counters.

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Budget reset")


class BudgetExceededError(Exception):
    """Raised when budget is exceeded."""

    def __init__(
        self,
        max_budget_usd: float,
        current_cost_usd: float,
    ):
        """
        Initialize budget exceeded error.

        Args:
            max_budget_usd: Maximum budget
            current_cost_usd: Current cost
        """
        self.max_budget_usd = max_budget_usd
        self.current_cost_usd = current_cost_usd
        super().__init__(
            f"Budget exceeded: ${current_cost_usd:.4f} / ${max_budget_usd:.4f}"
        )
