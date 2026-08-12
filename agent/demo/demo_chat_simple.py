#!/usr/bin/env python3
"""
Galatea Agent - Simple Interactive Chat

最简单的交互式对话界面，支持流式输出。
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
)


async def simple_chat():
    """简单的交互式对话"""
    sys.path.insert(0, str(Path.cwd()))
    from agent.runtime import GalateaRuntime

    print("\n" + "=" * 60)
    print("🤖 Galatea Agent - Interactive Chat")
    print("=" * 60)
    print()
    print("Commands: 'exit' or 'quit' to exit, 'clear' for new session")
    print()

    async with GalateaRuntime(project_root=Path.cwd()) as runtime:
        print("✅ Agent ready!\n")

        while True:
            try:
                # Get user input
                user_input = input("You: ").strip()

                if user_input.lower() in ['exit', 'quit', 'q']:
                    break

                if user_input.lower() == 'clear':
                    print("\n" + "=" * 60)
                    print("🗑️  New conversation started")
                    print("=" * 60 + "\n")
                    continue

                if not user_input:
                    continue

                # Send query
                print()

                # Track if we've printed "Claude:" prefix
                printed_prefix = False
                current_text = ""

                async for msg in runtime.query(user_input):
                    # Skip system messages (internal prompts)
                    if isinstance(msg, SystemMessage):
                        continue

                    # Handle assistant messages
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            # Skip thinking blocks (extended thinking)
                            if type(block).__name__ == 'ThinkingBlock':
                                continue

                            # Handle text blocks
                            if isinstance(block, TextBlock):
                                if not printed_prefix:
                                    print("Claude: ", end="", flush=True)
                                    printed_prefix = True

                                # Print incrementally if text is new
                                if block.text != current_text:
                                    # Print only the new part
                                    new_text = block.text[len(current_text):]
                                    print(new_text, end="", flush=True)
                                    current_text = block.text

                            # Show tool usage
                            elif isinstance(block, ToolUseBlock):
                                if not printed_prefix:
                                    print("Claude: ", end="", flush=True)
                                    printed_prefix = True
                                print(f"[Using tool: {block.name}]", end=" ", flush=True)

                    # Handle result message (end of response)
                    elif isinstance(msg, ResultMessage):
                        if printed_prefix:
                            print()  # New line after response

                        # Show cost if available
                        if msg.total_cost_usd:
                            print(f"💰 ${msg.total_cost_usd:.4f}")

                        print()
                        break

            except KeyboardInterrupt:
                print("\n")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}\n")

    print("👋 Goodbye!\n")


if __name__ == "__main__":
    try:
        asyncio.run(simple_chat())
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!\n")
