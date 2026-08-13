"""Train-inference integrated patrol runtime components.

Exports are resolved lazily to avoid import cycles with tool modules.
"""

__all__ = [
    "FileAuditEventWriter",
    "PatrolRunner",
    "compact_patrol_memory",
    "make_patrol_sdk_config",
    "patrol_run_result_json_schema",
    "render_cli_summary",
    "render_markdown_report",
    "validate_compaction_fidelity",
    "validate_llm_patrol_result",
    "write_markdown_report",
]


def __getattr__(name: str):
    if name == "FileAuditEventWriter":
        from agent.patrol.audit import FileAuditEventWriter

        return FileAuditEventWriter
    if name in {"render_cli_summary", "render_markdown_report", "write_markdown_report"}:
        from agent.patrol import channels

        return getattr(channels, name)
    if name in {"compact_patrol_memory", "validate_compaction_fidelity"}:
        from agent.patrol import compaction

        return getattr(compaction, name)
    if name == "PatrolRunner":
        from agent.patrol.runner import PatrolRunner

        return PatrolRunner
    if name in {"make_patrol_sdk_config", "patrol_run_result_json_schema", "validate_llm_patrol_result"}:
        from agent.patrol import sdk

        return getattr(sdk, name)
    raise AttributeError(name)
