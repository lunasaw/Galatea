"""First-pass patrol push channels for CLI and Markdown artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import List

from agent.patrol.compaction import redact_sensitive_text
from agent.schemas.patrol import PatrolRunResult


def render_cli_summary(result: PatrolRunResult) -> str:
    """Render a short patrol summary suitable for CLI/notebook display."""
    lines = [
        f"Patrol run {result.patrol_run_id}: {result.status.value}",
        redact_sensitive_text(result.summary),
        f"Findings: {len(result.findings)} | Recommendations: {len(result.recommendations)} | Evidence: {len(result.evidence)}",
    ]
    for finding in result.findings[:5]:
        lines.append(f"- {finding.severity.value}: {finding.finding_id} {redact_sensitive_text(finding.summary)}")
    if len(result.findings) > 5:
        lines.append(f"- ... {len(result.findings) - 5} more finding(s)")
    return "\n".join(lines)


def render_markdown_report(result: PatrolRunResult) -> str:
    """Render a redacted Markdown patrol report without raw logs or samples."""
    lines: List[str] = [
        f"# Patrol Report: {result.patrol_run_id}",
        "",
        f"- Status: `{result.status.value}`",
        f"- Session: `{result.session_id}`",
        f"- Project scope: `{', '.join(result.project_scope) or 'platform'}`",
        f"- Next check: `{result.next_check_at or 'not scheduled'}`",
        "",
        "## Summary",
        "",
        redact_sensitive_text(result.summary),
        "",
        "## Findings",
        "",
    ]
    if result.findings:
        for finding in result.findings:
            lines.append(
                f"- `{finding.finding_id}` `{finding.severity.value}` `{finding.status.value}`: "
                f"{redact_sensitive_text(finding.summary)} "
                f"(evidence: {', '.join(finding.evidence_ids) or 'none'})"
            )
    else:
        lines.append("- No open findings.")
    lines.extend(["", "## Recommendations", ""])
    if result.recommendations:
        for recommendation in result.recommendations:
            lines.append(
                f"- `{recommendation.recommendation_id}` `{recommendation.type.value}` "
                f"risk=`{recommendation.risk.value}` confidence=`{recommendation.confidence:.2f}` "
                f"approval=`{str(recommendation.requires_approval).lower()}` "
                f"(evidence: {', '.join(recommendation.evidence_ids) or 'none'})"
            )
    else:
        lines.append("- No recommendations pushed.")
    lines.extend(["", "## Evidence Index", ""])
    if result.evidence:
        for evidence in result.evidence:
            lines.append(
                f"- `{evidence.evidence_id}` `{evidence.kind}` via `{evidence.source_tool}`: "
                f"{redact_sensitive_text(evidence.summary)}; raw=`{evidence.raw_ref.uri}`; digest=`{evidence.raw_ref.digest}`"
            )
    else:
        lines.append("- No evidence recorded.")
    return "\n".join(lines) + "\n"


def write_markdown_report(result: PatrolRunResult, output_path: Path | str) -> Path:
    """Write a redacted Markdown report artifact."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(result), encoding="utf-8")
    return path
