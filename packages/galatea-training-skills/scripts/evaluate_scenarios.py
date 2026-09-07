#!/usr/bin/env python3
"""Validate recorded synthetic traces; this does not evaluate model behavior."""
from __future__ import annotations
import json
import sys
from pathlib import Path


class ScenarioError(ValueError):
    pass


def evaluate(fixture):
    required = {"scenario_id", "request", "platform_responses", "allowed_actions", "forbidden_actions",
                "expected_final_action", "required_evidence", "trace", "result"}
    if set(fixture) != required:
        raise ScenarioError("fixture fields do not match contract")
    tools = [event["tool"] for event in fixture["trace"]]
    forbidden = set(tools) & set(fixture["forbidden_actions"])
    if forbidden:
        raise ScenarioError(f"forbidden trace action: {sorted(forbidden)}")
    if any(tool not in fixture["allowed_actions"] for tool in tools):
        raise ScenarioError("trace contains action outside allowlist")
    if fixture["result"].get("action") != fixture["expected_final_action"]:
        raise ScenarioError("unexpected final action")
    evidence = set(fixture["result"].get("evidence_refs", []))
    if not set(fixture["required_evidence"]).issubset(evidence):
        raise ScenarioError("required evidence missing")
    if fixture["result"].get("proposed_outcome") == "accepted":
        verification = fixture["platform_responses"].get("galatea_verify_candidate", {})
        if verification.get("outcome") != "accepted" or verification.get("final_test_status") != "passed":
            raise ScenarioError("accepted lacks platform-verified passed final test")
    return True


if __name__ == "__main__":
    for path_text in sys.argv[1:]:
        evaluate(json.loads(Path(path_text).read_text()))
