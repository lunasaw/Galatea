# Galatea Agent 使用指南

## 三种使用方式

根据你的需求选择合适的方式：

---

## 🚀 方式 1：直接使用 Claude SDK（推荐）

**最灵活、最符合最佳实践**

### 基本用法

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from agent.tools.server import create_galatea_mcp_server

options = ClaudeAgentOptions(
    model="claude-opus-5",
    mcp_servers={"galatea-platform": create_galatea_mcp_server()},
    permission_mode="dontAsk",
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("List all training projects")
    
    async for msg in client.receive_response():
        # 处理消息
```

### 交互式对话

```bash
# 最佳实践版本
python agent/scripts/chat_sdk.py
```

### 完整示例

```bash
# 包含多种用法示例
python agent/demo/demo_sdk_direct.py
```

### 优点

- ✅ 完全控制所有 SDK 功能
- ✅ 支持 `AgentDefinition`
- ✅ 支持多 Agent 切换
- ✅ 支持所有 SDK 选项
- ✅ 符合 SDK 最佳实践

---

## 📦 方式 2：使用 GalateaRuntime（便利封装）

**适合快速开始**

### 基本用法

```python
from agent.runtime import GalateaRuntime
from pathlib import Path

async with GalateaRuntime(project_root=Path.cwd()) as runtime:
    # 简化的 API
    result = await runtime.inspect_platform()
    
    # 或者自由对话
    async for msg in runtime.query("你的问题"):
        # 处���消息
```

### 交互式对话

```bash
# 简单版本
python agent/demo/demo_chat_simple.py

# 或完整版本
python agent/scripts/chat.py
```

### 优点

- ✅ API 更简单
- ✅ 自动配置加载
- ✅ 内置平台检查方法
- ✅ 适合初学者

### 缺点

- ⚠️ 封装限制了某些 SDK 功能
- ⚠️ 不支持自定义 Agent
- ⚠️ 不支持多 Agent

---

## 🎯 方式 3：使用 query() 函数（最简单）

**最简洁的单次查询**

```python
from claude_agent_sdk import query
from agent.tools.server import create_galatea_mcp_server

options = ClaudeAgentOptions(
    mcp_servers={"galatea-platform": create_galatea_mcp_server()},
)

async for msg in query("你的问题", options=options):
    # 处理消息
```

### 优点

- ✅ 最简单
- ✅ 适合单次查询

### 缺点

- ⚠️ 不适合多轮对话
- ⚠️ 不能复用连接

---

## 🎨 使用 AgentDefinition 定义专门的 Agent

```python
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient
from agent.tools.server import create_galatea_mcp_server

options = ClaudeAgentOptions(
    mcp_servers={"galatea-platform": create_galatea_mcp_server()},
    agents={
        "data-agent": AgentDefinition(
            description="Data preparation and validation",
            prompt="You prepare and validate datasets for ML training. "
                   "Use Ray Data for processing.",
            tools=["list_training_projects", "inspect_project_structure", "Bash"],
            model="sonnet",
        ),
        "training-agent": AgentDefinition(
            description="Training orchestration",
            prompt="You orchestrate model training with Ray and MLflow. "
                   "Monitor training progress and report results.",
            tools=["inspect_mlflow_experiment", "inspect_ray_status", "Bash"],
            model="sonnet",
        ),
    },
)

async with ClaudeSDKClient(options=options) as client:
    # 使用特定 agent
    await client.query("Use data-agent to prepare the cats-and-dogs dataset")
    
    async for msg in client.receive_response():
        # 处理
```

---

## 📚 完整示例代码

### 1. 基础对话

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from agent.tools.server import create_galatea_mcp_server

async def basic_example():
    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
    )
    
    async with ClaudeSDKClient(options) as client:
        await client.query("List training projects")
        
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")
```

### 2. 流式输出

```python
async def streaming_example():
    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
    )
    
    async with ClaudeSDKClient(options) as client:
        await client.query("Explain MLflow tracking")
        
        current_text = ""
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        # 增量打印
                        new_text = block.text[len(current_text):]
                        print(new_text, end="", flush=True)
                        current_text = block.text
```

### 3. 多轮对话

```python
async def multi_turn_example():
    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
    )
    
    async with ClaudeSDKClient(options) as client:
        # Turn 1
        await client.query("List training projects")
        async for msg in client.receive_response():
            # 处理响应
            
        # Turn 2
        await client.query("Now check MLflow service health")
        async for msg in client.receive_response():
            # 处理响应
```

---

## 🛠️ 可用的 Galatea MCP 工具

```python
# Stage 1 已实现的工具
tools = [
    "list_training_projects",      # 列出所有训练项目
    "inspect_project_structure",   # 检查项目结构
    "check_service_health",        # 检查服务健康状况
    "inspect_mlflow_experiment",   # 检查 MLflow 实验
    "inspect_ray_status",          # 检查 Ray 集群状态
]
```

---

## 🎯 推荐使用方式

### 对于生产代码
✅ **使用方式 1**：直接使用 Claude SDK
- 完全控制
- 支持所有功能
- 易于测试和维护

### 对于快速原型
✅ **使用方式 2**：GalateaRuntime
- 快速上手
- 简化 API
- 内置便利方法

### 对于单次查询
✅ **使用方式 3**：query() 函数
- 最简单
- 快速验证

---

## 📖 更多示例

查看这些文件获取更多示例：

- `agent/demo/demo_sdk_direct.py` - 直接使用 SDK 的完整示例
- `agent/scripts/chat_sdk.py` - SDK 版本的交互式对话
- `agent/scripts/chat.py` - GalateaRuntime 版本的对话
- `agent/demo/demo_basic.py` - 原始的基础示例

---

## ⚡ 快速开始

```bash
# 1. 确保 API key 已配置
# ~/.claude/settings.json 或环境变量 ANTHROPIC_API_KEY

# 2. 运行交互式对话（推荐）
cd /data/ai/chenzhangyue/code/galatea
python agent/scripts/chat_sdk.py

# 3. 或运行示例
python agent/demo/demo_sdk_direct.py
```

---

## 💡 最佳实践

1. **使用 Claude SDK 原生 API** - 不要过度封装
2. **使用 AgentDefinition** - 为不同任务定义专门的 agent
3. **处理所有消息类型** - SystemMessage, AssistantMessage, ResultMessage
4. **跳过 ThinkingBlock** - 用户不需要看到思考过程
5. **增量更新文本** - 实现真正的流式输出
6. **显示工具调用** - 让用户知道正在使用什么工具
7. **追踪成本** - 显示 total_cost_usd 和 token 使用量
