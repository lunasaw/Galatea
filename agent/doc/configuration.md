# Galatea Agent 配置指南

## API 配置

### 方法 1: 使用 ~/.claude/settings.json（推荐）

`GalateaRuntime` 会自动从 `~/.claude/settings.json` 加载配置：

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key",
    "ANTHROPIC_BASE_URL": "https://your-custom-endpoint.com/api/"
  }
}
```

这是**最简单的方式**，不需要在代码或命令行中设置环境变量。

### 方法 2: 环境变量

如果你想临时覆盖 settings.json 中的配置：

```bash
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_BASE_URL="https://your-custom-endpoint.com/api/"
```

**优先级**: 环境变量 > settings.json

### 方法 3: 禁用自动加载

如果你不想使用 settings.json，可以禁用自动加载：

```python
from agent.runtime import GalateaRuntime

async with GalateaRuntime(
    project_root=Path("/data/ai/chenzhangyue/code/galatea"),
    auto_load_config=False  # 禁用自动加载
) as runtime:
    # 必须手动设置环境变量
    pass
```

## 支持的 Base URL

### 1. 官方 Anthropic API（默认）
```
不设置 ANTHROPIC_BASE_URL，或设置为：
https://api.anthropic.com
```

### 2. 自定义代理（如 vdian）
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://ai.vdian.net/api/"
  }
}
```

### 3. OpenRouter
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-or-v1-...",
    "ANTHROPIC_BASE_URL": "https://openrouter.ai/api/v1"
  }
}
```

### 4. Azure OpenAI（需要适配）
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-azure-key",
    "ANTHROPIC_BASE_URL": "https://your-resource.openai.azure.com/openai/deployments/your-deployment"
  }
}
```


## 配置边界

API 配置只用于让 Claude Agent SDK runtime 能访问模型服务，不代表授权平台动作。

- `ANTHROPIC_API_KEY` 和 `ANTHROPIC_BASE_URL` 不应写入项目源码、notebook 输出或 Agent transcript。
- 巡推 Agent 默认只使用 L0/L1 权限；配置 API key 不等于允许 L2 approval request 或 L3 apply。
- Ray、MLflow、MinIO 的 tracking URI、endpoint 和 credential 应通过明确配置或环境注入，不从模型 prompt 推断。
- 不要把 MinIO 长期密钥传给 LLM 或写入 Ray runtime_env YAML。
- 生产和开发建议使用不同 API key、不同 budget 和不同 permission policy。

## 验证配置

运行配置测试脚本：

```bash
python agent/test/test_config.py
```

**预期输出**:
```
✅ Loaded settings from ~/.claude/settings.json
✅ API Key found: cr_ae121ade4804c7346... (length: 67)
✅ Base URL found: https://ai.vdian.net/api/
```

## 常见问题

### Q1: 如何检查当前使用的配置？

```python
from agent.config import get_anthropic_config

config = get_anthropic_config()
print(f"API Key: {config['api_key'][:20]}...")
print(f"Base URL: {config['base_url']}")
```

### Q2: settings.json 在哪里？

```bash
ls -la ~/.claude/settings.json
# 或
cat ~/.claude/settings.json
```

### Q3: 为什么我的 Base URL 不生效？

检查：
1. settings.json 格式是否正确（JSON 语法）
2. Base URL 是否包含正确的路径（通常以 `/api/` 或 `/v1` 结尾）
3. 环境变量是否覆盖了 settings.json（环境变量优先）

### Q4: 如何使用多个不同的配置？

**选项 A**: 使用环境变量临时覆盖
```bash
ANTHROPIC_BASE_URL="https://other-endpoint.com" python agent/demo/demo_basic.py
```

**选项 B**: 在代码中动态设置
```python
import os
os.environ["ANTHROPIC_BASE_URL"] = "https://other-endpoint.com"

from agent.runtime import GalateaRuntime
# ... 使用 runtime
```

## 完整示例

```python
import asyncio
from pathlib import Path
from agent.runtime import GalateaRuntime
from agent.config import get_anthropic_config

async def main():
    # 查看当前配置
    config = get_anthropic_config()
    print(f"使用 Base URL: {config['base_url']}")
    
    # 创建 runtime（会自动加载 settings.json）
    project_root = Path("/data/ai/chenzhangyue/code/galatea")
    
    async with GalateaRuntime(project_root=project_root) as runtime:
        # 执行查询
        async for message in runtime.query("列出所有训练项目"):
            print(message)

asyncio.run(main())
```

## 安全注意事项

⚠️ **不要提交 settings.json 到 git**

```bash
# 检查是否已忽略
cat .gitignore | grep ".claude"

# 如果没有，添加：
echo "**/.claude/settings.json" >> .gitignore
```

⚠️ **API Key 权限**

- 只读操作（Stage 1）不需要特殊权限
- 未来阶段可能需要更多 API 配额（Stage 2+）
- 建议为开发和生产使用不同的 API key
