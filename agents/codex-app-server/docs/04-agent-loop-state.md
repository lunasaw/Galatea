# 04 Agent Loop 与状态

## 1. 不改造 Agent Loop

Codex 仍负责：读取 instructions/skills、构造模型请求、决定是否调用工具、处理工具结果、
继续模型轮次、生成最终消息和发出 turn notifications。Host 只做三件事：

1. 按 app-server public JSON-RPC API 启动/恢复/中断 Thread/Turn；
2. 执行 dynamic tool server request 并回传结果；
3. 在外层提供会话幂等、事件持久化、Console 和恢复边界。

Host 不把模型输出解析成自己的计划，不把工具调用重新排队到另一个 Agent loop，也不在
turn 结束后自动再问模型。长时间 Ray Job 仍由 Codex 根据工具结果决定下一步，Host 只提供
真实观察事实。

## 2. 状态目录

首版所有状态放在应用配置目录，目录由 `--config` 文件的父目录确定：

```text
agents/codex-app-server/config/
├── agent.json                 # 非秘密配置模板
├── contracts/
│   ├── tools.json              # 17 个支持工具合同的只读发布副本
│   ├── runtime-compatibility.json # 已接纳的模型 wire schema
│   └── catalog-metadata.json   # descriptions、annotations、公开性策略
├── runtime/
│   ├── bin/codex               # 复制的 Codex executable
│   ├── codex-package.json
│   └── resources/              # runtime 需要的资源和许可
├── codex-home/                 # CODEX_HOME，Codex 状态唯一根；不得依赖宿主默认 HOME
│   ├── sessions/               # 由 Codex runtime 管理
│   └── ...
├── state/
│   ├── sessions/<session>.json
│   ├── requests/<request>.json
│   ├── http-requests/<request>.json
│   ├── session-intents/<session>.json
│   ├── tool-calls/<call>.json
│   ├── .host.lock
│   └── runtime-process.json
├── events/<session>.jsonl
├── workspaces/                 # 受控 cwd，不承载 durable 业务状态
└── release/                    # manifest 与管理员接纳证据
```

`codex-home/` 是配置目录的一部分，不使用 `~/.codex`、CubeFS、NFS 或隐式当前用户目录。Codex
源码的配置加载仍可能读取系统层、`CODEX_HOME/config.toml`、cwd、父目录 `.codex/config.toml` 与
repo 配置；Host 必须在受控 workspace 放置明确配置、禁用不需要的层或把最终 effective config
摘要纳入 manifest，不能仅凭设置 `CODEX_HOME` 声称隔离完成。子进程的 `HOME` 也应指向受控目录，
但不能把 `HOME` 的设置误写成 Codex 官方配置覆盖机制。

`contracts/tools.json` 只保存输入 schema；与其配套的 catalog metadata（description、read-only、
idempotent、公开性策略）必须作为同一 release 的不可变文件保存并纳入 catalog digest。session
journal 记录实际发布的 principal-filtered catalog，而不是只记录“17 个支持工具”。
文件权限由部署用户控制；token 和密钥另放 mode `0600` 的 secret 文件，不写入 YAML 快照、
事件或 Console。

## 3. 持久化规则

- JSON 状态采用同目录临时文件、flush/fsync、replace、目录 fsync；写失败保留旧版本。
- 每个 session/turn/tool-call 使用不可变输入摘要；同一 ID 的不同 payload 是协议冲突。
- 每个可能产生副作用的 call 至少有 `intent_recorded`、`executing`、`receipt_saved` 或 `unknown`
  状态。intent 记录包含 canonical arguments digest、业务 idempotency key（如有）、catalog/config
  revision 和 deadline；没有 intent 不能开始 handler。
- 事件日志 append-only，带 `seq`、时间、session/thread/turn/call 关联；SSE 以 seq 续读。事件落盘
  是权威观察日志，发送给 SSE 客户端只是 best-effort 投影；发送失败不能回写或删除日志。
- 状态文件损坏或 manifest digest 不一致时 fail closed，不能清空目录重试。
- Host 的状态只保存关联和观察事实；Codex thread history 仍由 `CODEX_HOME` 管理，Host 不
  读取或修改 Codex 内部数据库。

## 4. 一轮状态机

```text
new
 → starting_thread
 → ready
 → turn_starting
 → running
 → waiting_tool_call
 → running
 → completed | failed | interrupted | unknown
```

工具调用期间 Host 另有：

```text
received → validating → executing → receipt_saved → responded
                                      └→ unknown
```

`unknown` 代表可能产生了副作用，必须观察 Galatea operation；它不是可以自动重试的普通
异常。只有后端明确提供幂等回执，Host 才能安全返回已有结果。进程在 `executing` 与
`receipt_saved` 之间退出时，恢复流程必须优先对账，不得因为 app-server 没有收到 response 就再次
执行 handler。

## 5. 恢复

1. 取得 `.host.lock`；第二实例直接失败。
2. 校验 runtime package、effective config、catalog 和 `CODEX_HOME` manifest digest。
3. 检查上次 app-server PID/process group 是否仍存活；无法确认时不启动替代进程。
4. 读取 session/turn/tool journal，找出 `starting`、`running`、`unknown` 状态。
5. 对每个未知 tool call 查询 Galatea `get_operation`/`observe_job` 等只读事实；不调用模型
   重新猜测操作 ID。
6. 若 thread 可恢复、rollout 中存在可验证的动态工具元数据、catalog digest 一致，并且阶段 0 已
   验证该 runtime 的恢复后有效工具面，才使用 `thread/resume`；否则 session 标记
   `recovery_required`，等待人工处理。恢复前核对已接纳的 runtime、principal、catalog 与有效配置绑定；真实模型请求的工具面断言由
   同一 runtime 的兼容性验收建立证据，Host 不截获生产模型网络请求。
7. 只有没有未核对副作用且 session 输入明确时，才允许下一次 `turn/start`。

## 6. 取消与停机

- Console 取消先写 Host cancellation marker，再发送 `turn/interrupt`。
- 等待 `turn/completed`、Tool handler 退出和所有 operation 核对；不能以 HTTP 200 作为完成。
- 进程组清理必须覆盖 app-server 及其子进程；失败则 readiness 失败，禁止新 session。
- 停机先停止 reconciler 接受新写入，再等待正在执行的 reconcile/handler 到达终态或 `unknown`；
  只杀 app-server 不能声称 backend operation 已停止。
- 正在运行的 Ray Job 由 `galatea_stop_job` 和后端事实处理，不能只杀 app-server 就声称训练
  已停止。
