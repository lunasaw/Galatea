# 方案 02：独立 Python MCP 与后端实施计划

> 后续实现使用 superpowers:executing-plans，先 fake 后端，再验证实际 API。

**目标：**任意 MCP 客户端可使用同一 Python 训练接口。
**架构：**官方 Python MCP SDK 处理 transport，内部 Python 模块执行方案 01 的约束。
**技术栈：**Python MCP SDK、Ray JobSubmissionClient、MlflowClient/Artifact API、S3 客户端。
**共同约束：**遵守 [实施索引](README.md)，不复用旧 TS service/controller 的运行依赖。

## 1. 文件与接口

相对于 Galatea/services/galatea-mcp：

| 文件 | 职责 |
| --- | --- |
| src/galatea_mcp/server.py、auth.py | MCP 启动、认证上下文、工具注册 |
| src/galatea_mcp/tools.py、results.py | input/output Schema、结构化/文本双返回、错误 |
| src/galatea_mcp/backends/ray.py | submit/status/logs/stop，集群身份和 metadata 核对 |
| src/galatea_mcp/backends/mlflow.py | 限定实验与角色的 Run/metric/Artifact 查询 |
| src/galatea_mcp/backends/objects.py | 登记对象版本/摘要读取，不暴露管理密钥 |
| src/galatea_mcp/evidence.py | 兼容性、Artifact 摘要与门禁证据 |
| src/galatea_mcp/watchdog.py | MCP 进程内后台对账和超时停止 |
| contracts/tool-envelope.schema.json、tools.json | 发布接口与工具集 |
| tests/test_mcp.py、test_backends.py、test_artifacts.py | 协议、身份、错误和下载测试 |

后端接口用受限结构参数，不接收任意 Shell 或平台根目录。
submit 返回已存在或新接纳 operation，observe 返回真实 API 快照，stop 仅登记/执行停止，
不能在 RPC 内等待整场训练。后台工作通过 state 模块串行更新，不另起数据库提交器。

## 2. 协议骨架

先实现 [首版工具清单](../02-platform-mcp-contract.md)。stdio 可用于本地 fake 测试；
远程 Streamable HTTP 使用实际验收的认证组件或受控反向代理，身份映射不能由客户端自报。
每次调用都校验作用域，MCP session ID 只管理协议连接。

结果构造的 Python 形状：

```python
import json

from mcp.types import CallToolResult, TextContent


def tool_result(payload: dict) -> CallToolResult:
    return CallToolResult(
        isError=not payload["ok"],
        structuredContent=payload,
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
    )
```

锁定 Python MCP SDK 后验证这个结果结构与公开 API；协议测试同时用文本/结构化客户端读相同 ID。
认证失败由 transport 明确拒绝；业务错误设置 isError，unknown side effect 返回 reconcile，
输入错误与预算不足不得混为可盲重试的网络失败。

## 3. 平台 API 取舍

Ray 使用官方 Python 客户端，entrypoint/runtime_env 仅由受信注册和固定 Release 构造。
提交 ID 来自已落盘 operation，metadata 含 project/campaign/step/attempt/source。
更换 Ray 集群或历史被清理时不能仅凭同名 ID/404 认定原任务身份或未执行。

MLflow 从显式配置读 Tracking URI 与 Experiment；先限制 Run 所属项目/角色，再处理允许筛选。
记录版本化数据/split/指标协议，比较时检查一致性。默认只给搜索阶段训练/验证证据。
Artifact 经官方 API，在受限临时目录下载/校验并生成引用；模型权重不塞进 MCP JSON。

数据经 S3 兼容 API 使用冻结对象版本和 manifest。官方 MLflow/MinIO MCP 仅作独立可选诊断，
不成为首版必装服务；不编造其 transport 参数或默认工具覆盖范围。

workload 自身执行 deadline；MCP 后台做独立于 Runner 的超时检查。MCP 崩溃后由服务管理器重启，
扫描原操作恢复检查。没有验证可强制限时的 workload 时，不能接收相应硬限时承诺。

## 4. 任务与验收

- [ ] M1：固定版本和契约，fake 后端完成 initialize/tools/list/tools/call；未知工具/非法字段失败。
- [ ] M2：测试身份跨项目、越权 Run/Artifact 和伪造批准字段均不能扩权；连接断开不取消 Ray。
- [ ] M3：替换 fake 为官方 Python 后端；分页、超时、丢响应、未知状态和停止均有测试。
- [ ] M4：验证大 Artifact 下载内存有界、摘要不符/穿越/中断失败，临时文件清理；不执行模型 pickle。
- [ ] M5：测试 MCP 重启后台恢复 pending/unknown；超时停止不需要启动 Codex。

从本服务根运行：`python -m unittest discover -s tests -p 'test_*.py' -v`。
真实端点先只读核验，再使用明确授权的专用小任务，不能对用户已有 Job 注入故障。
通过条件：无 Codex、无 Harness、无 plugins 依赖的环境仍完成完整协议测试。
