#!/usr/bin/env python3
"""
Galatea Agent Interactive Chat

交互式对话界面，支持多轮对话和工具调用。

Usage:
    python agent/scripts/interactive_chat.py
    python agent/scripts/interactive_chat.py --model claude-sonnet-4-5-20250929
    python agent/scripts/interactive_chat.py --project-root /path/to/project
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    UserMessage,
)


def parse_args():
    """解析命令行参数"""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea Agent Interactive Chat")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Galatea project root directory (default: current directory)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-opus-5",
        help="Claude model to use (default: claude-opus-5)"
    )
    parser.add_argument(
        "--mlflow-uri",
        type=str,
        default="http://127.0.0.1:5000",
        help="MLflow tracking URI (default: http://127.0.0.1:5000)"
    )

    return parser.parse_args()


def print_welcome():
    """打印欢迎信息"""
    print()
    print("=" * 70)
    print("🤖 Galatea Agent Interactive Chat")
    print("=" * 70)
    print()
    print("Commands:")
    print("  Type your message and press Enter to chat")
    print("  /help    - Show this help")
    print("  /clear   - Clear conversation history")
    print("  /skills  - Show enabled repository Skills")
    print("  /commit-push [notes] - Commit relevant changes and push branch")
    print("  /exit    - Exit the chat")
    print("  /quit    - Exit the chat")
    print()
    print("Available tools:")
    print("  - Git automation is command-scoped through /commit-push")
    print("  - Read-only code tools: Read, Glob, Grep, LS")
    print("  - list_training_projects")
    print("  - inspect_project_structure")
    print("  - check_service_health")
    print("  - inspect_mlflow_experiment")
    print("  - inspect_ray_status")
    print("  - Skill: invoke repository Skills")
    print()
    print("=" * 70)
    print()


def display_message(msg, show_tool_calls: bool = True):
    """
    显示消息内容

    Args:
        msg: Message to display
        show_tool_calls: Whether to show tool call details
    """
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"🤖 Claude: {block.text}")
            elif isinstance(block, ToolUseBlock) and show_tool_calls:
                print(f"🔧 [Tool: {block.name}]")
                if block.input:
                    import json
                    print(f"   Input: {json.dumps(block.input, indent=2)}")
    elif isinstance(msg, ResultMessage):
        # Show cost and usage
        if msg.total_cost_usd:
            print(f"\n💰 Cost: ${msg.total_cost_usd:.4f}")
        if msg.usage:
            tokens = msg.usage.get('output_tokens', 0) + msg.usage.get('input_tokens', 0)
            print(f"📊 Tokens: {tokens}")
        print()


async def interactive_chat(
    project_root: Path,
    model: str,
    mlflow_uri: str,
):
    """
    运行交互式对话

    Args:
        project_root: Galatea project root
        model: Claude model name
        mlflow_uri: MLflow tracking URI
    """
    # Add project root to path
    sys.path.insert(0, str(project_root))

    # Import after adding to path
    from agent.commands import claude_code_read_only_allowed_tools, default_command_registry
    from agent.runtime import (
        GalateaRuntime,
        claude_code_tools_preset,
    )
    from agent.skills import SkillRegistry

    print(f"Initializing Galatea Agent...")
    print(f"  Project: {project_root}")
    print(f"  Model: {model}")
    print(f"  MLflow: {mlflow_uri}")
    print("  Tools: controlled git automation + Galatea MCP inspection tools")
    print("  Skills: repository Skills enabled through Claude SDK")
    print()
    command_registry = default_command_registry()

    async with GalateaRuntime(
        project_root=project_root,
        model=model,
        mlflow_tracking_uri=mlflow_uri,
        tools=claude_code_tools_preset(),
        allowed_tools=claude_code_read_only_allowed_tools(),
        disallowed_tools=command_registry.disallowed_tools(),
        permission_mode="dontAsk",
        skills="all",
        max_turns=24,
        max_budget_usd=1.00,
        command_registry=command_registry,
    ) as runtime:
        print("✅ Agent initialized successfully!")
        print_welcome()

        turn = 0

        while True:
            try:
                # Get user input
                user_input = input("👤 You: ").strip()

                if not user_input:
                    continue

                # Handle only known slash commands; absolute paths also start with "/".
                command = user_input.lower()
                if command in ['/exit', '/quit']:
                    print("\n👋 Goodbye!")
                    break
                elif command == '/help':
                    print_welcome()
                    continue
                elif command == '/clear':
                    print("\n🗑️  Conversation cleared (note: actual clearing not implemented in Stage 1)")
                    print("   This would require session management from state/")
                    print()
                    continue
                elif command == '/skills':
                    print("\nAvailable Skills:")
                    for skill in SkillRegistry(project_root).discover(include_plugin=False):
                        print(f"  - {skill.name}: {skill.description}")
                    print()
                    continue

                # Send query and receive response
                turn += 1
                print()

                async for message in runtime.query(user_input):
                    if isinstance(message, SystemMessage):
                        continue
                    display_message(message, show_tool_calls=True)
                    if isinstance(message, UserMessage) and isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                print("   ✓ tool result received")

            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")
                print()


def main():
    """主入口"""
    args = parse_args()

    try:
        asyncio.run(interactive_chat(
            project_root=args.project_root,
            model=args.model,
            mlflow_uri=args.mlflow_uri,
        ))
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
