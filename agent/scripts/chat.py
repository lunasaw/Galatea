#!/usr/bin/env python3
"""
Galatea Agent - Interactive Chat with Tool Visibility

完整的交互式对话界面，支持：
- 流式输出
- 工具调用显示
- 成本追踪
- 多轮对话
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


def print_header():
    """打印欢迎界面"""
    print("\n" + "=" * 70)
    print("🤖 Galatea Agent - Interactive Chat")
    print("=" * 70)
    print()
    print("Commands:")
    print("  exit, quit  - Exit chat")
    print("  clear       - Start new conversation")
    print("  tools       - Show available tools")
    print()
    print("Features:")
    print("  ✓ Real-time streaming responses")
    print("  ✓ Tool call visibility")
    print("  ✓ Multi-turn conversation")
    print("  ✓ Cost tracking")
    print()
    print("=" * 70)
    print()


def print_tools():
    """打印可用工具"""
    print()
    print("Available Tools:")
    print("  📋 list_training_projects      - List all training projects")
    print("  🔍 inspect_project_structure   - Inspect project files")
    print("  ❤️  check_service_health        - Check service status")
    print("  📊 inspect_mlflow_experiment   - Check MLflow experiments")
    print("  🚀 inspect_ray_status          - Check Ray cluster")
    print()


async def interactive_chat():
    """完整的交互式对话"""
    sys.path.insert(0, str(Path.cwd()))
    from agent.runtime import GalateaRuntime

    print_header()

    async with GalateaRuntime(project_root=Path.cwd()) as runtime:
        print("✅ Agent initialized!\n")

        turn_number = 0

        while True:
            try:
                # Get user input
                user_input = input("You: ").strip()

                # Handle empty input
                if not user_input:
                    continue

                # Handle commands
                if user_input.lower() in ['exit', 'quit', 'q']:
                    break

                if user_input.lower() == 'clear':
                    print("\n" + "=" * 70)
                    print("🗑️  Conversation cleared")
                    print("=" * 70 + "\n")
                    turn_number = 0
                    continue

                if user_input.lower() == 'tools':
                    print_tools()
                    continue

                # Process query
                turn_number += 1
                print()

                # Track response state
                response_started = False
                current_text = ""
                tools_used = []

                async for msg in runtime.query(user_input):
                    # Skip system messages
                    if isinstance(msg, SystemMessage):
                        continue

                    # Handle assistant messages
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            # Skip thinking blocks
                            if type(block).__name__ == 'ThinkingBlock':
                                continue

                            # Handle text blocks
                            if isinstance(block, TextBlock):
                                if not response_started:
                                    print("🤖 Claude: ", end="", flush=True)
                                    response_started = True

                                # Stream text incrementally
                                if block.text != current_text:
                                    new_text = block.text[len(current_text):]
                                    print(new_text, end="", flush=True)
                                    current_text = block.text

                            # Handle tool use blocks
                            elif isinstance(block, ToolUseBlock):
                                if not response_started:
                                    print("🤖 Claude: ", end="", flush=True)
                                    response_started = True

                                tools_used.append(block.name)
                                print(f"\n   🔧 [Calling: {block.name}]", end="", flush=True)

                    # Handle user messages (tool results)
                    elif isinstance(msg, UserMessage):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                print(" ✓", end="", flush=True)

                    # Handle result message
                    elif isinstance(msg, ResultMessage):
                        if response_started:
                            print()  # End line

                        # Show summary
                        print()
                        if tools_used:
                            print(f"   🔧 Tools used: {', '.join(set(tools_used))}")

                        if msg.total_cost_usd:
                            print(f"   💰 Cost: ${msg.total_cost_usd:.4f}")

                        if msg.usage:
                            total_tokens = (
                                msg.usage.get('input_tokens', 0) +
                                msg.usage.get('output_tokens', 0)
                            )
                            print(f"   📊 Tokens: {total_tokens:,}")

                        print()
                        break

            except KeyboardInterrupt:
                print("\n")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")
                import traceback
                traceback.print_exc()
                print()

    print("👋 Goodbye!\n")


if __name__ == "__main__":
    try:
        asyncio.run(interactive_chat())
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!\n")
