#!/usr/bin/env python3
"""
Galatea Agent 演示 - 阶段 1：只读运行时 POC

演示：
- Claude SDK 运行时初始化
- 带有只读工具的进程内 MCP 服务器
- 使用 agent 进行平台检查
- 结构化输出（基础版本）

这是 agent 架构的最小 POC。
"""

import asyncio
import json
import sys
import logging
from pathlib import Path

# 添加父目录到路径，以便导入 agent 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.runtime import GalateaRuntime

# 配置日志以显示模型序列化
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


async def demo_platform_inspection():
    """演示：使用只读工具进行平台检查。"""

    print("=" * 70)
    print("Galatea Agent 演示 - 阶段 1：平台检查")
    print("=" * 70)
    print()

    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    print(f"项目根目录: {project_root}")
    print(f"正在初始化带有只读检查工具的 agent 运行时...")
    print()

    try:
        async with GalateaRuntime(project_root=project_root) as runtime:
            print("✓ 运行时初始化成功")
            print("✓ 已创建带有检查工具的 MCP 服务器")
            print()

            print("-" * 70)
            print("执行平台检查...")
            print("-" * 70)
            print()

            result = await runtime.inspect_platform()

            print("=" * 70)
            print("检查结果")
            print("=" * 70)
            print()
            print(f"状态: {result.get('status', 'unknown')}")
            print(f"时间戳: {result.get('timestamp', 'N/A')}")
            print()

            if result.get('status') == 'success':
                print("响应:")
                print("-" * 70)
                response = result.get('response', '')
                # 处理字符串和 Message 对象
                if hasattr(response, 'content'):
                    print(response.content)
                else:
                    print(response)
                print()
            else:
                print(f"错误: {result.get('error', '未知错误')}")
                print()

            print("=" * 70)
            print("演示成功完成！")
            print("=" * 70)

    except Exception as e:
        print()
        print("=" * 70)
        print("错误")
        print("=" * 70)
        print(f"演示失败: {e}")
        print()
        import traceback
        traceback.print_exc()
        return 1

    return 0


async def demo_custom_query():
    """演示：使用只读工具的自定义查询。"""

    print()
    print("=" * 70)
    print("自定义查询演示")
    print("=" * 70)
    print()

    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    try:
        async with GalateaRuntime(project_root=project_root) as runtime:
            prompt = """列出平台中的所有训练项目并显示 'ray-cats-and-dogs' 项目的结构。
它有哪些配置文件？"""

            print(f"查询: {prompt}")
            print()
            print("-" * 70)
            print("Agent 响应:")
            print("-" * 70)
            print()

            async for message in runtime.query(prompt):
                # 流式传输到达的消息
                if hasattr(message, 'content'):
                    print(message.content, end='', flush=True)
                else:
                    print(message, end='', flush=True)

            print()
            print()

    except Exception as e:
        print(f"查询失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def main():
    """运行所有演示。"""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "GALATEA AGENT 架构演示" + " " * 30 + "║")
    print("║" + " " * 20 + "阶段 1：只读运行时 POC" + " " * 27 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    # 运行平台检查演示
    result = asyncio.run(demo_platform_inspection())

    if result == 0:
        # 如果成功，运行自定义查询演示
        result = asyncio.run(demo_custom_query())

    return result


if __name__ == "__main__":
    exit(main())
