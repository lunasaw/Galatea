"""Bounded public errors; backend exception strings never cross this boundary."""


class DomainError(Exception):
    def __init__(self, category: str, *, retryable: bool = False,
                 state_changed: bool | str = False, operation_id: str | None = None,
                 next_action: str = "correct-request"):
        super().__init__(category)
        self.details = dict(category=category, retryable=retryable,
                            state_changed=state_changed, operation_id=operation_id,
                            next_action=next_action)
