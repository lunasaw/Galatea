#!/usr/bin/env python3
"""
快速测试脚本 - 验证 SDK 最佳实践实现

测试所有关键功能：
1. 导入测试
2. MCP 工具创建
3. AgentDefinition 使用
4. 基础查询
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))


def test_imports():
    """测试所有导入"""
    print("=" * 60)
    print("1. 测试导入")
    print("=" * 60)

    try:
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AgentDefinition
        print("✅ Claude SDK 导入成功")
    except Exception as e:
        print(f"❌ Claude SDK 导入失败: {e}")
        return False

    try:
        from agent.tools import create_galatea_mcp_server
        print("✅ Galatea 工具导入成功")
    except Exception as e:
        print(f"❌ Galatea 工具导入失败: {e}")
        return False

    try:
        from agent.agents import (
            PLATFORM_INSPECTOR,
            DATA_PREPARER,
            TRAINING_ORCHESTRATOR,
            MODEL_EVALUATOR,
            EXPERIMENT_ANALYZER,
            DOCUMENTATION_GENERATOR,
        )
        print("✅ 所有 Agent 定义导入成功")
        print(f"   - PLATFORM_INSPECTOR: {PLATFORM_INSPECTOR.description}")
        print(f"   - DATA_PREPARER: {DATA_PREPARER.description}")
    except Exception as e:
        print(f"❌ Agent 定义导入失败: {e}")
        return False

    print()
    return True


async def test_basic_query():
    """测试基础查询"""
    print("=" * 60)
    print("2. 测试基础 SDK 查询")
    print("=" * 60)

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock
    from agent.tools import create_galatea_mcp_server

    try:
        options = ClaudeAgentOptions(
            mcp_servers={"galatea": create_galatea_mcp_server()},
            permission_mode="dontAsk",
        )
        print("✅ SDK 选项创建成功")

        async with ClaudeSDKClient(options) as client:
            print("✅ SDK 客户端创建成功")

            # 发送简单查询
            await client.query("What is 1+1?")

            # 接收响应
            response_received = False
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(f"✅ 收到响应: {block.text[:50]}...")
                            response_received = True
                            break
                if response_received:
                    break

            if response_received:
                print("✅ 基础查询测试通过")
            else:
                print("❌ 未收到响应")
                return False

    except Exception as e:
        print(f"❌ 基础查询测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    return True


async def test_agent_definition():
    """测试 AgentDefinition"""
    print("=" * 60)
    print("3. 测试 AgentDefinition")
    print("=" * 60)

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock
    from agent.tools import create_galatea_mcp_server
    from agent.agents import PLATFORM_INSPECTOR

    try:
        options = ClaudeAgentOptions(
            mcp_servers={"galatea": create_galatea_mcp_server()},
            agents={"inspector": PLATFORM_INSPECTOR},
            permission_mode="dontAsk",
        )
        print("✅ 带 AgentDefinition 的选项创建成功")

        async with ClaudeSDKClient(options) as client:
            print("✅ 带 Agent 的客户端创建成功")

            # 使用 agent
            await client.query("Use inspector agent to say hello")

            response_received = False
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(f"✅ Agent 响应: {block.text[:50]}...")
                            response_received = True
                            break
                if response_received:
                    break

            if response_received:
                print("✅ AgentDefinition 测试通过")
            else:
                print("❌ 未收到 Agent 响应")
                return False

    except Exception as e:
        print(f"❌ AgentDefinition 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    return True


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("🧪 Galatea Agent SDK 最佳实践 - 快速测试")
    print("=" * 60)
    print()

    # 测试导入
    if not test_imports():
        print("\n❌ 导入测试失败，停止后续测试")
        return 1

    # 测试基础查询
    if not await test_basic_query():
        print("\n❌ 基础查询测试失败，停止后续测试")
        return 1

    # 测试 AgentDefinition
    if not await test_agent_definition():
        print("\n❌ AgentDefinition 测试失败")
        return 1

    # 所有测试通过
    print("=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)
    print()
    print("SDK 最佳实践实现验证成功！")
    print()
    print("可以运行：")
    print("  python agent/scripts/chat_sdk.py        # 交互式对话")
    print("  python agent/demo/demo_agents.py        # Agent 示例")
    print("  python agent/demo/demo_sdk_direct.py    # SDK 用法")
    print()

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
