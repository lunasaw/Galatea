#!/usr/bin/env python3
"""
Simple interactive chat demo.

Quick start for Galatea Agent chat.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage


async def simple_chat():
    """简单的交互式对话"""
    import sys
    sys.path.insert(0, str(Path.cwd()))

    from agent.runtime import GalateaRuntime

    print("\n🤖 Galatea Agent - Simple Chat")
    print("=" * 50)
    print("Type your message or 'exit' to quit\n")

    async with GalateaRuntime(project_root=Path.cwd()) as runtime:
        while True:
            try:
                user_input = input("You: ").strip()

                if user_input.lower() in ['exit', 'quit', 'q']:
                    break

                if not user_input:
                    continue

                print()
                async for msg in runtime.query(user_input):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                print(f"Claude: {block.text}")
                    elif isinstance(msg, ResultMessage):
                        if msg.total_cost_usd:
                            print(f"\n[Cost: ${msg.total_cost_usd:.4f}]")
                print()

            except KeyboardInterrupt:
                break

    print("\nGoodbye! 👋")


if __name__ == "__main__":
    asyncio.run(simple_chat())
