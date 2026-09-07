# Python Codex Runtime 与独立 Skill 包

## 1. 固定运行路线

Python Runner → 官方 `openai_codex` SDK → 原版 `codex app-server` → 独立 Galatea MCP。
SDK 负责协议连接、Thread 和 Turn，Codex 负责工具和 Skill 执行；Runner 只负责外部任务调度。
不使用 TS SDK 主线，不修改 Codex Core，不实现第二套 Agent，也不改动 DeepSeek 插件。

本地接口依据：[Python README](../../../codex/sdk/python/README.md)、
[API reference](../../../codex/sdk/python/docs/api-reference.md)、
[公开封装](../../../codex/sdk/python/src/openai_codex/api.py)。
版本范围为本地源码 `459a79e`；发布包与 Runtime 的匹配验证是实施第一步。

## 2. 一次决策的 Python 调用

下面只展示 SDK 边界；`save_thread_id`、JSON 校验、超时和平台对账由 Runner 实现。
不含安装或执行真实训练的指令。

```python
import json
from pathlib import Path
from typing import Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput
from openai_codex.types import TurnStatus


def decide_once(
    workspace: Path,
    saved_thread_id: str | None,
    skill_path: Path,
    prompt: str,
    output_schema: dict,
    save_thread_id: Callable[[str], None],
) -> tuple[dict, object]:
    # 调用进程已由 supervisor 设置独立账号、受限环境和持久会话目录。
    with Codex(CodexConfig(cwd=str(workspace))) as codex:
        options = {
            "cwd": str(workspace),
            "sandbox": Sandbox.workspace_write,
            "approval_mode": ApprovalMode.deny_all,
        }
        thread = (
            codex.thread_resume(saved_thread_id, **options)
            if saved_thread_id
            else codex.thread_start(ephemeral=False, **options)
        )
        save_thread_id(thread.id)  # 成功持久化后才能启动可能调用 MCP 的 Turn。
        result = thread.run(
            [
                SkillInput(name="training-campaign", path=str(skill_path)),
                TextInput(text=prompt),
            ],
            output_schema=output_schema,
        )
        if result.status != TurnStatus.completed or result.error is not None:
            raise RuntimeError("Codex turn did not complete successfully")
        if result.final_response is None:
            raise RuntimeError("Codex turn returned no final response")
        return json.loads(result.final_response), result.usage
```

`SkillInput.path` 指向已经安装且校验过的独立包内 `SKILL.md`，不能由模型给任意路径。
Runner 还要校验 [输出 Schema](examples/agent-turn.schema.json)及引用归属，不能直接执行返回的建议。
生产需要保存 Turn ID、进度和取消时，使用 `thread.turn(...)` 得到 handle，保存 `handle.id`，
再消费 `handle.run()` 或 `handle.stream()`；同一个流只选一个消费者。
超时通过 `handle.interrupt()`，必要时由 supervisor 终止独立 worker 执行组。

## 3. 与 TS 流程不同的真实语义

| 项目 | Python SDK 的处理 |
| --- | --- |
| 方法名 | `thread_start/thread_resume`、`run/turn`、`output_schema` |
| 连接 | app-server over stdio，不是封装 `codex exec --json` |
| Thread ID | thread_start/resume 返回后即可保存，无需等待 TS thread.started |
| 事件 | app-server 通知，如 `item/completed`、`turn/completed`、`thread/tokenUsage/updated` |
| 失败 | `run()` 的收集器对 failed 和缺失完成事件抛错；interrupted 仍须显式判断 |
| 用量 | `TurnResult.usage` 可为空，结构为 ThreadTokenUsage 的 total/last |
| 环境 | `CodexConfig.env` 在继承环境上更新，不能用于清除父进程秘密 |
| 权限 | 显式指定 `ApprovalMode.deny_all`；默认 auto_review 不是业务预算授权 |

依据：[client.py](../../../codex/sdk/python/src/openai_codex/client.py)、
[_run.py](../../../codex/sdk/python/src/openai_codex/_run.py)、
[_approval_mode.py](../../../codex/sdk/python/src/openai_codex/_approval_mode.py)。
`deny_all` 表示不授予额外的升级权限，允许范围内的操作照常执行；它不等于禁止所有 MCP 工具。
MCP 服务仍核验独立批准范围，不将 Codex 的“完全访问”映射为训练或推广权限。

Thread 用量 total 是累计值，不能每轮把整个 total 再加一次；last 也不能想当然当作完整 Turn。
保存同一 Thread 前后 total 快照并计算非负差值，重复通知不重复累计。
异常、中断、缺少用量或出现无法解释的回退时记 unknown，停止新增自主 Turn 并对账。
首版限制 Turn 数和进程时长，Token 仅用于观察阈值；不承诺精确实时费用硬上限。

## 4. MCP 配置与凭据

[配置样例](examples/codex-config.example.toml)部署到专用运行账号，包含 HTTPS MCP URL、
`bearer_token_env_var`、`required`、RPC 超时和首版 allowlist。
配置字段可在 [本地配置定义](../../../codex/codex-rs/config/src/mcp_types.rs)核对。
实际工具集合以 MCP 握手和 tools/list 为准，关键服务缺失就停止本轮。
`required` 的本地配置注释明确说明 exec 行为；Python app-server 路线仍要实测失败语义，
Runner 自己完成核心工具预检，不能只依赖这个配置字段推断启动成功。

Runner 在启动 worker 时通过受控 `subprocess` 环境或容器配置形成白名单，之后 SDK 才继承该环境。
模型认证和受限 MCP 身份属于 Runtime；Ray、S3、平台管理员凭据不进入这个进程。
`CodexConfig.env` 只作为覆盖层，不把它误当沙箱。不要修改开发者的 HOME/CODEX_HOME；
使用专用服务账号自己的正常会话目录、只读配置和独立工作区。

非交互首版遇到超出原授权的动作时输出 request_approval，由用户入口补充平台授权再恢复。
已有明确授权不重复询问。此处不自造 SDK 业务审批回调。

## 5. 独立 Skill 包

独立仓库/制品 `galatea-training-skills` 包含：

| Skill | 内容 |
| --- | --- |
| training-campaign | 总入口、阶段路由、工具顺序、等待和停止条件 |
| dataset-readiness | 数据身份、质量、split 和污染检查 |
| model-strategy | 基线、方法选择、硬件约束 |
| experiment-design | 有限搜索、兼容 cohort、seed 和停止判断 |
| mlflow-analysis | 通过 MCP 读取证据、验证集选优 |
| model-delivery | 交付内容、门禁、可复现性与限制 |

每个技能携带自包含 references，按固定版本和摘要安装；不指向 Galatea checkout 的相对路径。
不打包到 `Galatea/plugins/`，也不改变当前插件注册和发现能力。
发现路径按锁定 Codex 版本验收，关键入口用 `SkillInput` 显式指定；不要把开发机个人 Skill 全量装入服务。
生产 Skill 只读，更新只作用于新任务，旧任务恢复使用原 digest。

Skill 负责方法，MCP 负责执行约束，Runner 负责等待；三者不重复实现授权、调度或模型训练算法。
包结构与场景验收见 [方案 04](implementation/04-skill-bundle.md)。

## 6. 会话恢复

保存 thread_id 还不够；恢复必须具备原服务账号的 Codex 持久会话、固定工作区、Runtime 版本和 Skill 包。
使用 `thread_resume(exact_id)`，不采用“最后一个会话”，也不直接读写 Codex 内部数据库或日志格式。

Thread 丢失时，先确认旧 worker/app-server 已停止，再查询平台任务清单与 MLflow 证据，
新建 Thread 并记录 replaces_thread_id。无法证明旧进程停止时阻止自动接管。
Codex 恢复只恢复决策上下文，训练恢复由 workload 的 Checkpoint 合同决定。
