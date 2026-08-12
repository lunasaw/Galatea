"""
Event Hooks

Hook system for monitoring and validation:
- logging_hooks: Logging and telemetry
- validation_hooks: Pre/post validation
- notification_hooks: Alerts and notifications
"""

__all__ = [
    "create_logging_hooks",
    "create_validation_hooks",
    "create_notification_hooks",
]
