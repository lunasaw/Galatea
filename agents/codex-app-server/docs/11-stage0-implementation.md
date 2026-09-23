# 11 TDD 实施与验收记录

更新：2026-09-23。**runtime schema 兼容性已解决，离线实施及真实 binary 集成测试通过；
正式 release/现场部署验收尚未通过。** 这两种状态不能互相替代。

以下保留最初兼容性实施的测试记录；后续生产推进已新增来源验证、依赖锁、权限和进程崩溃测试。
最新 100 项应用测试及生产候选证据见 [12-production-readiness.md](12-production-readiness.md)。

## 1. 已确认的 schema 决策

用户已明确确认“用兼容性测试就行”。实现采用两份独立合同：

- `config/contracts/tools.json`：原始 17 工具 schema，不放宽范围、正则、长度、唯一性等约束，
  ToolRegistry 在 handler 和 journal 创建前执行严格校验。
- `config/contracts/runtime-compatibility.json`：经审查的 0.153.4 模型 wire schema，绑定原始
  catalog、binary、package tree、package manifest 和 app-server protocol schema 摘要。

`RuntimeContract` 按 principal 过滤冻结的工具集合，比较完整 namespace、description、function
和参数 schema。新增工具、缺少工具、枚举/描述/schema 漂移，以及 binary/catalog 漂移都失败。
不会在运行验收时用实际输出自动刷新期望值。

Codex 归约造成的差异保留为诊断：`limit.maximum/minimum`、身份 `pattern`、数组 `maxItems`、
`uniqueItems` 未出现在模型 schema；`const` 转成单值 `enum`。模型侧可接受的 `limit=101/0`、
非法 project id、重复/超量 run ids 均通过真实 ToolRegistry 负向测试，在 handler 前被拒绝。
这些差异不再作为已批准兼容性合同的失败项。

## 2. 本次 TDD 实施

新测试先揭示失败，再补实现，覆盖以下行为：

- **RPC**：单一持续读取任务、并发响应按 ID 分发、请求返回后继续处理工具/通知；接受实际 runtime
  省略 `jsonrpc` 的消息；重复 response/server request、EOF、坏 JSON 和超时失败关闭。
- **Host**：UUID 会话、写入 turn intent 后再发 RPC；重复消息按内容摘要幂等，不保存完整 prompt。
  运行中容量直到 `turn/completed` 才释放；绑定 thread/turn/session；interrupt 和 close 等待确认。
  重启先核对 runtime/catalog/principal/config 与 thread 历史，未知 turn 不自动重新提交。
- **持久化**：原子替换、文件与父目录 fsync；flock 排他写入且可在崩溃后重获；拒绝路径穿越和
  symlink。只有确定 receipt 能直接重放，历史 intent/executing/unknown 不能再次执行 handler。
- **Registry/Service**：action/project/campaign 作用域、deadline/cancel、异步 worker 与超时 unknown；
  保留数据形状的敏感字段删除及 256 KiB 上限。原 MCP 和 Host 复用 transport-neutral envelope。
  reconcile 与业务调用共用适配器锁，原 Service 继续持有自己的状态锁。
- **故障恢复**：fake 外部 API + 真实 Galatea Service 验证丢失提交响应、Host receipt 丢失后不重提
  Job；后台通过已有 plan/operation 的只读观察恢复 receipt。查不到或仍 unknown 时保持阻断。
- **Console**：uvicorn ASGI lifespan 保证同一事件循环；原生状态公开投影、工具步骤合并、静态页面；
  Bearer、Origin、session owner、所有写接口的持久化幂等；body/连接上限、持续 SSE、Last-Event-ID、
  heartbeat、背压/断线处理。残缺日志尾部转换为持久 observation_gap，原生 prompt/reasoning 不公开。
- **发布边界**：`check` 与 `serve` 使用同一 evidence gate，核对 release/config/runtime/compatibility；
  原生 `config/read` 包括配置来源，启动及 admission 比较摘要。官方后端只读 preflight 和 systemd
  模板已提供。父进程先退出的 process-group helper 清理有实际子进程测试。

恢复边界保持保守：只有 submit 的既有 operation 有足够持久证据时自动重建回执；其他没有确定
receipt 的 mutation 保留 unknown，需管理员核对。它们不会因超时、重启或新的 callId 自动重试。
没有可靠 turn id 的丢失 `turn/start` 响应同样保留 unknown，避免重复输入。

## 3. 本次测试结果

| 测试 | 结果 | 边界 |
| --- | --- | --- |
| App-server 项目全集 | 85 通过，0 跳过 | 显式设置真实 runtime 包路径 |
| 其中真实 binary 探针 | 5 通过 | full/subset、新建/续轮/跨进程恢复、成功/错误回执、opt-in |
| 其中真实 Host 集成 | 1 通过 | 生产 RPC/Host/Registry + 本地 mock model，跨进程恢复 |
| Galatea 原服务全集 | 48 通过 | 含真实 MCP 协议兼容性与 fake backend 治理测试 |
| 仓库级测试 | 7 通过 | 现有仓库合同 |
| JavaScript / Python / diff 检查 | 通过 | Node 语法、compileall、diff whitespace |
| Python wheel | 构建通过 | 三份 Console 静态资源均在包内 |

未显式设置 `GALATEA_TEST_RUNTIME_PACKAGE` 时，6 个真实 binary 测试会跳过，不能把跳过数算成通过。
真实 HTTP 测试启动临时 loopback uvicorn，检查网页、鉴权、重复请求和持续 SSE；不只是直接调用 ASGI。

## 4. 真实 runtime 证据

本次证据位于仓库根目录的 `outputs/codex-app-server-compatibility-20260923/`，Git 忽略。
`report.json` 的 `protocol_status=passed`，full/subset 和 opt-in 负向均通过。
`stage0_status=blocked`、退出码 2 保留正式 release 证据不完整的事实。

实际 runtime 为统一 npm package `codex-cli 0.153.4`，target `x86_64-unknown-linux-musl`：

- binary SHA-256：`56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da`
- package tree：`2574264f46dab839626ec0d7034d54c5454c2d619987107b93e208e1fbd5867a`
- protocol schema：`74d5cdc8998ea238aac6af751caa40d95d6cceb01512d5cae542aa3786d2e87d`

参考源码 revision `94174e44cbc54cece45f6052328ca0c2cd7a8a2a` 不等于 binary 构建来源。
后续 npm/Sigstore 验证已确认实际来源 `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`，
详见生产接纳记录；本节原始协议报告没有被改写。
官方文档先前访问返回 403；结论来自本地源码、实际 binary 生成 schema 和实际模型请求捕获。

历史 `tests/fixtures/codex-0.153.4/provenance.json` 和 2026-09-22 失败记录保持原貌，作为兼容性决策
前的证据；没有回写成通过。原始 schema 差异仍能从报告追溯。

## 5. 未完成的现场验收

- 目标生产配置/工具面和完整 release evidence 的正式接纳（binary-to-source 来源验证已补齐）；
- 既有 Campaign 的排他状态接管；本机 Ray、MLflow Tracking/Artifact 索引和 Object 元数据
  已用真实凭据完成只读预检，未打开 Campaign writer；
- 实际安装 systemd unit、目录身份权限和平台停机/恢复验收；
- 如果要求被攻破 runtime 也无法读取 Host secrets，必须另行部署并验证独立 UID/容器/broker；
- 真实受治理训练 smoke 和最终数据/质量门禁，仅在另行明确授权后执行。

后续 staging 可执行路径的 `systemd-analyze verify` 已通过，最终 `/opt` 路径仍未安装；
不能声称本机服务已部署。当前实施没有安装系统服务、调用真实模型、提交真实训练或更改 Alias。
