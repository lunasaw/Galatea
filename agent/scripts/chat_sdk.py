#!/usr/bin/env python3
"""Interactive Galatea chat using the reusable Claude SDK foundation."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    HookEventMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent.agents import (  # noqa: E402
    DATA_PREPARER,
    DOCUMENTATION_GENERATOR,
    EXPERIMENT_ANALYZER,
    MODEL_EVALUATOR,
    PLATFORM_INSPECTOR,
    TRAINING_ORCHESTRATOR,
)
from agent.sdk_core import AgentSDKConfig, GalateaSDKRuntime, result_to_json  # noqa: E402


def print_header() -> None:
    print("\n" + "=" * 76)
    print("Galatea Agent - Interactive Chat (Claude SDK Core)")
    print("=" * 76)
    print("Safe defaults: strict MCP config, dontAsk permissions, mutation tools disabled")
    print()
    print("Commands:")
    print("  /exit, /quit        Exit chat")
    print("  /tools              Show Galatea MCP tools")
    print("  /status             Show MCP server status")
    print("  /context            Show SDK context usage")
    print("  /compact            Show compaction instructions")
    print("  /json               Toggle result JSON summary")
    print("=" * 76)
    print()


def print_tools() -> None:
    print()
    print("Available Galatea Tools:")
    print("  mcp__galatea-platform__list_training_projects")
    print("  mcp__galatea-platform__inspect_project_structure")
    print("  mcp__galatea-platform__check_service_health")
    print("  mcp__galatea-platform__inspect_mlflow_experiment")
    print("  mcp__galatea-platform__inspect_ray_status")
    print()


def build_runtime(args: argparse.Namespace) -> GalateaSDKRuntime:
    allowed_tools = [
        "mcp__galatea-platform__list_training_projects",
        "mcp__galatea-platform__inspect_project_structure",
        "mcp__galatea-platform__check_service_health",
        "mcp__galatea-platform__inspect_mlflow_experiment",
        "mcp__galatea-platform__inspect_ray_status",
    ]
    agents = {
        "inspector": PLATFORM_INSPECTOR,
        "data": DATA_PREPARER,
        "training": TRAINING_ORCHESTRATOR,
        "inference": MODEL_EVALUATOR,
        "experiment": EXPERIMENT_ANALYZER,
        "docs": DOCUMENTATION_GENERATOR,
    }
    config = AgentSDKConfig(
        project_root=args.cwd,
        model=args.model,
        agent_type="interactive-chat",
        allowed_tools=allowed_tools,
        agents=agents,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        task_budget_tokens=args.task_budget_tokens,
        auto_load_config=not args.no_auto_config,
    )
    return GalateaSDKRuntime(config)


async def interactive_chat(args: argparse.Namespace) -> None:
    print_header()
    show_json = False

    async with build_runtime(args) as runtime:
        print("SDK client initialized.\n")
        while True:
            try:
                user_input = input("You: ").strip()
                if not user_input:
                    continue

                command = user_input.lower()
                if command in {"/exit", "/quit", "exit", "quit", "q"}:
                    break
                if command == "/tools" or command == "tools":
                    print_tools()
                    continue
                if command == "/json":
                    show_json = not show_json
                    print(f"Result JSON summary: {'on' if show_json else 'off'}\n")
                    continue
                if command == "/compact":
                    print(runtime.context_compaction_instructions())
                    print()
                    continue
                if command == "/status":
                    await _print_status(runtime)
                    continue
                if command == "/context":
                    await _print_context(runtime)
                    continue

                print()
                response_started = False
                current_text = ""
                tools_used: list[str] = []
                hook_events = 0
                result_message: ResultMessage | None = None

                async for msg in runtime.stream_query(user_input):
                    if isinstance(msg, SystemMessage) and not isinstance(msg, HookEventMessage):
                        continue
                    if isinstance(msg, HookEventMessage):
                        hook_events += 1
                        continue
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                if not response_started:
                                    print("Claude: ", end="", flush=True)
                                    response_started = True
                                if block.text != current_text:
                                    print(block.text[len(current_text):], end="", flush=True)
                                    current_text = block.text
                            elif isinstance(block, ToolUseBlock):
                                tools_used.append(block.name)
                                if not response_started:
                                    print("Claude: ", end="", flush=True)
                                    response_started = True
                                print(f"\n   [tool: {block.name}]", end="", flush=True)
                    elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                print(" done", end="", flush=True)
                    elif isinstance(msg, ResultMessage):
                        result_message = msg
                        if response_started:
                            print()
                        break

                if result_message:
                    _print_result_summary(result_message, tools_used, hook_events)
                    if show_json:
                        from agent.sdk_core import SDKRunResult

                        print(
                            result_to_json(
                                SDKRunResult(
                                    messages=[],
                                    result_message=result_message,
                                    text=current_text,
                                    structured_output=result_message.structured_output,
                                )
                            )
                        )
                    print()
            except KeyboardInterrupt:
                print("\n")
                break
            except Exception as exc:  # noqa: BLE001 - CLI should keep running after one failed turn
                print(f"\nError: {exc}\n")

    print("Goodbye.\n")


async def _print_status(runtime: GalateaSDKRuntime) -> None:
    status = await runtime.get_mcp_status()
    print()
    for server in status.get("mcpServers", []):
        print(f"{server.get('name')}: {server.get('status')}")
        for tool in server.get("tools", []) or []:
            print(f"  - {tool.get('name')}")
    print()


async def _print_context(runtime: GalateaSDKRuntime) -> None:
    usage = await runtime.check_context_usage()
    print()
    print(
        f"Context: {usage.get('totalTokens', 0):,}/{usage.get('maxTokens', 0):,} "
        f"tokens ({usage.get('percentage', 0):.1f}%)"
    )
    for category in usage.get("categories", [])[:8]:
        print(f"  - {category.get('name')}: {category.get('tokens', 0):,}")
    advice = usage.get("galatea_compaction_advice")
    if advice:
        print(f"  Compaction advice: {advice}")
    print()


def _print_result_summary(
    result_message: ResultMessage,
    tools_used: list[str],
    hook_events: int,
) -> None:
    if tools_used:
        print(f"   Tools used: {', '.join(sorted(set(tools_used)))}")
    if hook_events:
        print(f"   Hook events: {hook_events}")
    if result_message.permission_denials:
        print(f"   Permission denials: {len(result_message.permission_denials)}")
    if result_message.total_cost_usd:
        print(f"   Cost: ${result_message.total_cost_usd:.4f}")
    if result_message.usage:
        total_tokens = (
            result_message.usage.get("input_tokens", 0)
            + result_message.usage.get("output_tokens", 0)
        )
        print(f"   Tokens: {total_tokens:,}")
    if result_message.terminal_reason:
        print(f"   Terminal reason: {result_message.terminal_reason}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Project root for Claude SDK")
    parser.add_argument("--model", default="claude-opus-5", help="Claude model or alias")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-budget-usd", type=float, default=0.20)
    parser.add_argument("--task-budget-tokens", type=int, default=None)
    parser.add_argument("--no-auto-config", action="store_true", help="Do not load ~/.claude/settings.json env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        asyncio.run(interactive_chat(args))
        return 0
    except KeyboardInterrupt:
        print("\nGoodbye.\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
