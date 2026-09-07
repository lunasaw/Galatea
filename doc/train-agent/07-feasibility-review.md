# 架构纠偏与验收依据

更新：2026-09-06。结论：目标架构可以按 Python 独立实现；当前交付为设计，尚未上线。
本次核对本地 Galatea `7ef8377`、Codex 源码 `459a79e`，未重新连接远端，也未调用模型或启动训练。

## 1. 已纠正的设计偏差

| 上一版安排 | 本次修正 | 原因 |
| --- | --- | --- |
| 抽取 plugins/dsh-galatea 的 TS policy/controller | 独立 Python MCP，旧插件完整保留 | TS 文件属于 DeepSeek 工作流，不是新架构基础 |
| 旧插件改接领域服务或关闭写入口 | 删除迁移/切换要求，部署和任务来源独立 | 用户明确要求现有 plugin 能力不动 |
| Node/TS Codex Runtime adapter | 官方 openai_codex Python SDK → 原版 app-server | 按已有 Python 流程实施，不复制 TS 调用形状 |
| 平台 PostgreSQL + Runner SQLite | 本地 JSON 清单、原子写入、单实例进程锁 | 有界串行任务不需要新业务数据库 |
| training-control、提交 outbox、独立 dispatcher | MCP 包内普通 Python 模块与后台对账 | 避免新增控制平台成为接入前提 |
| 每轮 DecisionGrant/epoch、多实例租约 | 单 Runner 与受监督 worker，受限固定身份 | 首版不承诺跨主机自动接管 |
| 把现有插件测试通过当新路线支撑 | 新 MCP/Runner 分别做 Python 契约与故障验收 | 两条实现路径独立 |

MLflow 的现有数据库、MinIO 对象持久化和 Codex 自有会话存储仍然有用；
本次移除的是额外业务数据库方案，不是删除平台状态或改造原版 Runtime 存储。

## 2. Python SDK 源码证据

| 能力 | 已检查的源码 |
| --- | --- |
| 公开 Python SDK 与启动/恢复 | [README](../../../codex/sdk/python/README.md)、[api.py](../../../codex/sdk/python/src/openai_codex/api.py) |
| 启动 app-server、合并 env | [client.py](../../../codex/sdk/python/src/openai_codex/client.py) |
| output_schema、Turn handle、interrupt | [API reference](../../../codex/sdk/python/docs/api-reference.md)、api.py |
| 失败抛错、interrupted 与空 final_response、可选 usage | [_run.py](../../../codex/sdk/python/src/openai_codex/_run.py) |
| deny_all / auto_review | [_approval_mode.py](../../../codex/sdk/python/src/openai_codex/_approval_mode.py) |
| SkillInput | [_inputs.py](../../../codex/sdk/python/src/openai_codex/_inputs.py) |

这些证据足以定义 adapter 的调用边界；不证明公开发布包、本机安装包或远端部署都已验证。
正式实施必须锁定 SDK 与匹配 Runtime，并在干净环境做 MCP/Skill/恢复 smoke。
不能把 Python SDK 写成 TS `runStreamed`/AbortSignal，也不能把 Python 的 env 当清理继承环境的白名单。

## 3. 简化后仍保留的必要约束

- 提交前持久记录稳定 ID，超时先对账；不是只在 Runner 内存保留 job_id。
- 同一逻辑计算通过 step/attempt 去重，重建 Thread 不增加 Trial。
- 单写进程和本地卷是前提；未知提交、损坏文件或孤儿 worker 不明时停止自动重发。
- 首版保守预算只覆盖新 MCP 入口，不声称统一控制旧插件的资源和费用。
- 数据/split/配置/Release 身份必须成立；测试隔离不能只靠 Skill。
- 验证集选优、干净重训、一次最终测试和 Artifact 恢复仍需实施验收。
- Turn 数/时长可在 Runner 强制限制，Token 用量只作观测阈值；未知用量不当零。

## 4. 后续验证矩阵

| 验证 | 预期 |
| --- | --- |
| 普通 MCP client + fake 后端，无 Agent | 新 Python 服务能独立执行契约 |
| Python Runner + 原版 SDK + fake MCP | start、结构化输出、结束、resume 使用准确 Thread |
| 同 Plan/step 换幂等键 | 仍返回同一 operation/submission ID |
| 接受后丢响应、进程重启 | 查回原 Job；不明时 blocked 对账 |
| 第二 Runner 或第二 MCP 写进程 | 无法取得进程锁，不进入执行 |
| SDK failed/interrupted/空输出 | 不产生完成判定，仍查询平台已发生动作 |
| 多次等待 tick | 模型调用次数保持不变 |
| env 中混入管理员凭据 | supervisor 白名单隔离，worker 不继承 |
| Trial 请求测试内容、重复最终测试 | 拒绝，使用标记保留 |
| 质量失败或 Artifact 不完整 | 不 accepted，不降低阈值 |
| 插件与原版 Codex 工作树 | 本任务没有相关改动 |

具体文件、接口和验收顺序见 [实施索引](implementation/README.md)。
本次文档校验不代表新服务、真实训练或远端隔离已经通过；旧稿中的历史测试数字不作为本次验收结果。
