# DeepSeek Harness 与 Galatea 训推平台架构及实施记录

> 状态：**已于 2026-09-01 按冻结架构完成直接替换**；仓库仅保留 DeepSeek Harness 与
> `dsh-galatea`，不包含双 Agent Runtime、兼容层或回退入口。
> 范围：多项目、多模型、多框架的训练、评估、Artifact 验证与模型治理

本文定义 DeepSeek Harness 与 Galatea 的最终职责边界，并记录从旧 Python Agent Runtime 直接
迁移到 `plugins/dsh-galatea/` 的当前实现。下述插件结构和能力描述当前契约，验收条件见第 12 节，
是否通过以当前工作树执行文档中的验证命令为准；部署步骤见
[`dsh-galatea-operations.md`](dsh-galatea-operations.md)。

本文不描述某个具体模型、框架、数据集、指标或训练项目的实现。训练项目仍遵守仓库级
[`AGENTS.md`](../AGENTS.md) 和各自的 README、配置与测试约定。

## 1. 冻结结论

最终架构只保留一套 Agent Runtime：

- **DeepSeek Harness** 负责思考和执行循环；
- **Galatea 插件**负责让 Harness 能够安全、确定地操作训推平台；
- **Ray** 负责实际训练；
- **MLflow** 负责实验追踪、Artifact 访问和模型治理；
- **MinIO** 负责数据、Checkpoint、模型和其他 Artifact 的持久化。

Galatea 不再实现第二套 Agent Runtime。旧 `agent/` 目录已整体删除，未保留：

- Claude Agent SDK；
- `GalateaRuntime`、`GalateaAgentClient` 或其他 Agent Client；
- Agent Loop、推理上下文、Session Store；
- 自建 Hooks、权限系统和 Token Budget；
- 自建 Agent Definition、Workflow Runtime 和 Skill Registry；
- Claude MCP Server；
- Chat、Demo、Agent CLI。

少量训练领域逻辑不能机械删除，也不能原样复制。项目检查、MLflow 查询、质量门禁、训练生命
周期规则等能力必须先重新确定边界、加固契约，再迁移为 DeepSeek Harness 插件中的 Tool、
Policy 或 Service。

## 2. 目标架构

```text
用户
  │
  ▼
DeepSeek Harness
  ├── Agent Loop、推理、上下文与 Session
  ├── Workflow、重试、审批与 Sub-agent
  └── 加载 dsh-galatea 插件
          ├── 检查训练项目与数据
          ├── 管理 Ray Job 生命周期
          ├── 查询日志、指标与状态
          ├── 分析可比较的 MLflow Runs
          ├── 修改并校验训练配置
          ├── 验证 Checkpoint、Model 与 Artifact
          └── 生成并执行质量门禁
                  │
                  ├── Ray：执行训练
                  ├── MLflow：实验追踪与模型治理
                  └── MinIO：数据、Checkpoint 与 Artifact
```

### 2.1 唯一 Runtime 原则

DeepSeek Harness 独占以下运行时职责：

- Agent Loop 和模型调用；
- 推理上下文、压缩和 Token Budget；
- Session、消息、持久化和恢复；
- Workflow、重试、暂停和继续编排；
- 审批、权限和工具执行策略；
- Sub-agent 调度；
- Skill 发现、加载和注入；
- 用户交互、CLI、Web 或其他客户端入口。

`dsh-galatea` 不实现或包装这些能力，也不修改 DeepSeek Harness 的 Agent Loop。所有扩展都通过
DeepSeek Harness 已公开的 Cordis 插件、Tool、Policy Hook、Approval 和 Skill 扩展点接入。

### 2.2 职责矩阵

| 能力 | 权威所有者 | Galatea 插件职责 |
| --- | --- | --- |
| 推理、上下文和 Session | DeepSeek Harness | 不保存副本 |
| Workflow、重试和 Sub-agent | DeepSeek Harness | 返回可供编排的结构化结果 |
| 授权和权限 | DeepSeek Harness | 完全权限直接授权；其他权限为当前高风险 Tool 请求一次性审批 |
| 训练领域规则 | `dsh-galatea` | 提供确定性 Policy 和验证结果 |
| 项目与配置 | 训练项目 | 发现、修改并调用项目校验入口 |
| Job 执行和资源状态 | Ray | 通过 Jobs API 提交、查询和停止；恢复仅在项目声明能力时可用，资源可满足性在提交后观察 |
| Run、Metric 和模型治理 | MLflow | 通过 Tracking、Artifact 和 Registry API 操作 |
| 数据和 Artifact 对象 | MinIO | 平台负责受控持久化；插件仅经 MLflow Artifact API 间接访问，不读取服务端目录 |
| 训练计算 | 训练项目与 Ray Worker | 不在插件进程中执行正式训练 |

### 2.3 非目标

目标架构不要求：

- 为某个模型族、训练框架或指标建立平台特例；
- 在插件中实现通用 Agent SDK；
- 在插件中复制 DeepSeek Harness 的 Workflow 或审批状态；
- 把 Notebook Kernel 当作正式训练执行环境；
- 通过读取 `mlflow.db`、MinIO 服务端目录或 Ray 临时目录完成集成；
- 自动修改生产模型 Alias；
- 为已删除的旧 Agent Runtime 提供兼容期。

## 3. `dsh-galatea` 插件结构

当前目录为：

```text
plugins/
└── dsh-galatea/
    ├── package.json
    ├── cordis.patch.yml
    ├── src/
    │   ├── tools/
    │   ├── policies/
    │   ├── services/
    │   └── index.ts
    ├── harness-tests/
    └── tests/
```

`package.json` 声明 TypeScript ESM 包、DeepSeek Harness/Cordis 依赖和测试入口。
`cordis.patch.yml` 负责把插件装入指定的 DeepSeek Harness Profile。
`src/index.ts` 是唯一插件注册入口。它只注册能力，不启动 Agent、客户端或独立服务。

### 3.1 Tools

`src/tools/` 是模型可调用的类型化能力入口。当前精确注册 14 个 Tool，不把内部 Service 方法逐个
暴露给模型：

| Tool 组 | Tool | 状态影响 |
| --- | --- | --- |
| 项目列表 | `galatea_list_projects` | 只读 |
| 项目选择 | `galatea_select_project` | 修改当前 Session 的项目选择 |
| 项目与配置 | `galatea_inspect_project`、`galatea_patch_config`、`galatea_plan_run` | 只读计划；或修改并校验工作区配置 |
| Ray 生命周期 | `galatea_submit_job`、`galatea_observe_job`、`galatea_stop_job`、`galatea_pause_job`、`galatea_resume_job` | 部分会改变平台状态 |
| Run 与证据 | `galatea_compare_runs`、`galatea_build_stage_evidence`、`galatea_verify_candidate` | 只读并重新验证 MLflow/Artifact 事实 |
| 模型推广 | `galatea_promote_model` | 经当前证据审批后创建/复用版本并更新 Alias |

当前 bundle 由管理员直接配置 `ray-cats-and-dogs`、`ray-handwritten-digits` 和
`ray-kaggle-house-prices` 三个受信项目，默认前者。`galatea_select_project` 只能选择注册 ID；成功调用由 Harness 标准 `tool/call`、
`tool/result` 或 `tool/code-dispatch` Session 事件记录，并经 `galateaProjectSelection`
projection/replay 派生该 Session 的当前项目，未选择时回落到 `defaultProject`。选择不是进程级
单例，也不能让模型注入新的路径。注册表在启动时要求绝对的项目/Release 根目录，
解析真实路径并拒绝重复项目、越界清单和符号链接逃逸。

受信项目路由是模型能力约束，不是租户隔离。多个项目仍可共享插件进程、Ray/MLflow endpoint 和
服务凭据；跨租户边界必须另配服务端 ACL、独立凭据、网络/进程边界或独立 Profile/集群。

Tool 统一使用明确的输入 Schema、结构化输出和脱敏结果。适用的生命周期/证据 Tool 返回平台
ID、URI、Digest、状态和错误分类，访问日志、列表或 Artifact 时接受取消信号并限制读取规模。
需要稳定重放身份的提交、停止和推广等动作使用幂等身份；Session 选择、配置补丁或暂停等 Tool
只采用各自声明的参数，不能假定每个 Tool 都有幂等键。面向人的摘要与规范结果分开，任何 Tool
都不得把密码、Token、对象存储密钥或敏感样本写入结果。

### 3.2 Policies

`src/policies/` 保存训练领域的不变量和纯判定逻辑，包括：

- 项目契约和项目能力声明；
- 数据、Manifest、切分、预处理和代码身份；
- Run 可比性；
- Trial、候选配置、最终验证和推广的阶段前置条件；
- 重试和重复提交的幂等规则；
- Checkpoint 与 Artifact 完整性要求；
- 声明式质量门禁和主目标的优化方向；
- 最终测试集隔离；
- 推广只能引用已批准最终验证产物的约束。

Policy 不保存 Session 或 Workflow 状态，不向用户发起审批，也不实现 DeepSeek Harness 的权限
系统。它接收显式输入并返回稳定、可测试的判定和原因。

### 3.3 Services

项目路径语义是契约的一部分：`manifestPath` 相对管理员配置的 `projectRoot`；Tool 的
`configPath` 相对当前 `projectRoot` 且必须位于声明的 `configRoot` 下；
`releaseManifestPath` 相对当前 `releaseRoot`；Artifact 路径相对对应 MLflow Run。模型不能用
绝对路径或 `..` 改变信任根。

`src/services/` 封装平台正式接口：

- Ray Jobs HTTP API；
- MLflow Tracking、Artifact 和 Model Registry HTTP API；
- 参数化项目入口的结构化调用。

Service 负责认证、超时、取消、错误规范化和响应上限；MLflow Service 还负责 Run、Artifact
列表和 Model Version 的分页，Ray Job 列表则读取 Jobs API 返回的完整数组。Tool 负责编排一次
用户可理解的领域动作，Policy 负责判定领域不变量，Service 不反向承担 Tool 或 Workflow 职责。

项目校验、计划和 Checkpoint 入口以固定 argv、`shell: false` 运行，并只继承管理员配置的环境
allowlist。默认名单是 `PATH`、`HOME`、`LANG`、`LC_ALL`、`PYTHONPATH`、
`CONDA_PREFIX`、`CONDA_DEFAULT_ENV`、`MLFLOW_TRACKING_URI`、`RAY_ADDRESS`、
`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`；`projectProcessInheritedEnv` 会替换整份名单。
操作所需的 Galatea 上下文另行显式注入，Token/Secret 不因存在于 Harness 环境就自动传入子进程。

项目注册时还会执行结构硬校验：项目必须位于 `train-model/<project-name>/`，清单中的
`metadata.name`、`spec.packageName` 和 `src/<package>/` 必须一致，并具备 `README.md`、
`configs/*.yaml`、`src/`、`scripts/`、`tests/` 及环境文件。清单必须声明
`spec.executionBackend: ray`，计划必须返回非空 Ray 配置和资源声明。当前 Session 必须先成功
`galatea_plan_run`，再以相同项目/配置/Release/角色/attempt 提交；readiness digest 变化会阻断提交。
插件还在 Harness `tools/pre-execute` 边界拒绝直接 `scripts/train.py` 正式训练和 `ray job submit`，
但通用 Shell 工具的最终可用性仍由宿主权限预设控制。

正式模型和 Checkpoint 验证使用 MLflow Artifact API；`dsh-galatea` 当前不实现直接 MinIO
客户端。若平台中的其他组件需要直接访问 MinIO，必须在插件边界外使用独立配置的最小权限客户端，
不能读取 MinIO 服务端文件系统，也不能复用服务端长期密钥。

### 3.4 Skills

`skills/` 是可选目录，不是首版迁移的完成条件。确定性能力优先由 Tool、Policy、Schema 和
项目入口实现。只有确实需要模型进行跨步骤判断、证据综合或诊断策略选择的能力，才可以成为
Skill。

插件不得实现自己的 Skill Registry。候选 Skill 由 DeepSeek Harness 的 Skill 能力发现和加载。

## 4. 调用链与状态事实源

```text
DeepSeek Harness Workflow
  → dsh-galatea Tool
  → Galatea Policy 校验
  → Ray / MLflow / Artifact Service
  → 结构化证据
  → Harness Session 与阶段汇总
  → 受治理操作取得完全权限授权或发起一次性审批
```

平台状态的事实源固定如下：

| 状态 | 事实源 |
| --- | --- |
| Agent 会话、上下文、审批和 Workflow | DeepSeek Harness Session Log |
| Job 生命周期、资源和运行日志 | Ray |
| Experiment、Run、参数、指标、Tag 和 Registry | MLflow |
| 数据、Checkpoint、模型和报告内容 | MinIO/Artifact Store |
| 训练配置和项目能力声明 | 训练项目源码与配置 |

插件可以生成缓存和幂等索引，但缓存不是事实源。插件不得建立与 Harness Session、Ray Job 或
MLflow Run 并行的自有状态机。缓存丢失后，必须能够通过平台 ID 和正式 API 恢复观察。

生命周期/证据 Tool 在提供 `operationStatus` 时，同时保留互不推导的维度：`statuses.execution` 表示执行状态，
`statuses.quality` 表示质量门禁，`statuses.governance` 表示审批/推广治理状态；`integrity` 下的
`preprocessingParity` 与 `migrationContamination` 单独表示完整性。Ray `SUCCEEDED` 不能推出
质量通过、完整性通过或已获推广批准，质量门禁通过也不会自动改变 Alias。

完整性在 readiness 阶段 fail closed。项目必须在清单中声明预处理上下文比较、迁移来源和污染
检查，并由只读计划入口返回结构化结果；适用角色的必需证据缺失、未知、失败、字段不一致，或把
适用检查报告成 `not-applicable`，都会阻止计划进入提交。只有清单明确标为非阻断的改进 backlog
作为 advisory，不得反向中断已满足必需检查的训练目标。

## 5. 抽象训练生命周期

目标生命周期与具体框架、模型、数据类型、指标名称和优化方向无关。

```text
项目与数据就绪
  → 提交操作审批
训练优化
  → Champion 提交操作审批
最终验证
  → 推广操作审批
模型推广
  → 操作回执与验收
```

### 5.1 项目与数据就绪阶段

本阶段建立后续操作所需的可复核输入：

- 项目结构和参数化正式入口；
- 项目声明的训练、暂停/恢复、评估和推广能力；
- 数据来源、内容或 Manifest Digest；
- 确定性切分和预处理身份；
- 完整配置、代码修订、环境和资源计划；
- MLflow Tracking URI、Experiment 身份和 Artifact 可达性；
- Ray Jobs 服务可达性与版本，以及项目计划声明的请求资源；实际调度是否可满足需在提交后观察。

阶段产物是“就绪证据包”。提交训练 Job 的当前 Tool 调用获得完全权限直接授权或一次性审批后，
才执行该次提交。

### 5.2 训练优化阶段

DeepSeek Harness 在本阶段内驱动自主调试闭环：

```text
观察状态
  → 分析问题
  → 生成训练配置差异
  → 调用项目校验入口
  → 提交 Ray Job
  → 监控日志、资源和指标
  → 比较可兼容的 MLflow Runs
  → 继续调整或结束本阶段
```

同一阶段可以包含多个 Trial、自动重试和多轮调参，不要求逐 Run 人工审批。所有 Trial 只能
使用训练集和验证集进行优化、早停和候选选择。

阶段产物至少包含候选配置、候选 Run、可比性证明、选择依据、失败尝试摘要和未解决风险。
提交 Champion Job 的当前 Tool 调用必须同时获得 readiness 与所选候选证据授权；完全权限直接
授权，其他权限分别请求一次性审批。

### 5.3 最终验证阶段

最终验证使用已批准候选，从干净状态重训，或按项目明确声明的恢复契约从持久 Checkpoint
恢复；当前 bundle 中的两个项目都声明 `pauseResume: false`，因此只能采用前者。最终测试集只在
这一阶段读取，不得反向影响 Trial 搜索。

本阶段至少验证：

- 最终指标和项目声明的统计口径；
- Checkpoint Digest 与回读；
- Logged Model 或等价交付物可加载；
- 必需 Artifact 存在且可通过正式 API 读取；
- 预测、报告和环境证据完整；
- 所有必需质量门禁通过。

阶段产物是“可推广模型证据包”。推广 Tool 必须针对当前证据取得授权：完全权限直接授权，其他
权限请求一次性审批；获准后才能执行该次模型推广动作。

### 5.4 模型推广阶段

模型推广重新读取内容未变化的最终验证证据包，创建受控模型版本或修改 Registry Alias。任何
Run、Artifact、配置、数据或代码身份变化都会形成不同证据；即使证据未变，新 Tool 调用也不能
复用先前授权。

推广 Tool 在实际修改 Registry 前展示当前最终验证证据并按当前权限直接授权或请求一次性审批；
获准后输出 Registry
操作回执、最终 Alias/Version 状态和审计信息，由用户验收。驳回时 Tool fail closed，用户再决定
是否启动新的纠正或回退流程。自动重试、Session 恢复、Sub-agent 或 Workflow 循环都必须重新
请求审批，不能复用上一次 `allowed-once`。

## 6. 受治理操作授权

Galatea 使用 DeepSeek Harness 现有权限 preset 和普通 Tool 审批，不扩展 Harness 的审批词汇或
Session 事件。当前有效 preset 为 `danger-full-access`（“完全权限”）时，插件不发起审批，而是对
当前阶段、产物 ID 和 Evidence Digest 生成 full-access 授权。其他权限的审批绑定当前 Tool 调用；
`allowed-once` 只允许该次操作，不产生可供后续 Tool 重放或复用的长期授权。

DeepSeek Harness 负责：

- 展示阶段结果和证据；
- 通过现有 `approval/request` 收集允许一次、拒绝、取消或不可用决定；
- 记录现有 `approval/asked` 与 `approval/decided` 审计事件；
- 对 `rejected`、`cancelled` 和 `unavailable` 统一 fail closed。

Galatea 插件负责：

- 生成完整、结构化的阶段证据；
- 为产物计算或收集不可变身份；
- 在提交、恢复和推广等受治理 Tool 内重新计算当前证据，并把精确摘要交给 Harness；
- 通过 Harness `permissionPresets.current(session)` 读取有效 preset；
- 仅在完全权限生成证据绑定授权，或当前请求返回 `allowed-once` 后执行该次状态变更。

审批原因至少展示阶段名称、产物 ID 和 Evidence Digest，并在适用时概括：

- 数据、切分、预处理、配置和代码身份；
- Ray Job ID 与 MLflow Run ID；
- Checkpoint、Model、报告和其他 Artifact URI；
- Artifact Digest；
- 主目标、优化方向和质量门禁结果。

一次性审批不能提前申请并供后续操作消费。非完全权限下，Tool 重试、Session 恢复、Sub-agent
交接、Evidence Digest 变化或进入下一阶段，都必须在实际状态变更调用中重新请求审批；完全权限
下则为每份当前证据重新生成 full-access 授权。

当 Harness Session 的当前有效 preset 为 `danger-full-access` 时，即使审批策略为 `never`、
`promptsEnabled: false`，提交、恢复和推广也通过 full-access 授权继续。若审批策略为 `never` 但
preset 不是 `danger-full-access`，则不能取得 `allowed-once`，必须以 `approval-required` fail closed；
重复调用不能绕过禁用策略，需切换到完全权限或在 Harness 层启用审批。
这不表示所有其他 Tool 都只读：`galatea_patch_config` 会修改并校验项目 YAML，
`galatea_stop_job` 会停止指定 Job，而它们当前不走上述三类阶段审批请求。

## 7. Ray 训练生命周期

插件通过 Ray 的正式接口管理 Job，不把 Notebook Kernel 或插件进程当作训练宿主。

### 7.1 提交和幂等

提交前必须完成配置、数据、资源和 MLflow Preflight。提交请求携带幂等键，幂等身份至少覆盖
项目、数据、切分、预处理、解析后配置、代码修订和执行角色。

重复请求不得意外创建新的 Job 或 MLflow Run。调用方确实需要新 Attempt 时，必须显式请求并
产生新的 Attempt 身份。

### 7.2 暂停和恢复

Ray Job 没有跨工作负载的通用原地暂停语义。插件只在训练项目明确声明支持时提供暂停：

```text
请求暂停
  → 项目生成持久 Checkpoint 和恢复元数据
  → 验证 Checkpoint 可读
  → 优雅停止当前 Ray Job

请求恢复
  → 校验 Checkpoint、配置和代码兼容性
  → 对恢复 readiness 证据取得完全权限授权或请求一次性 Tool 审批
  → 基于 Checkpoint 提交新的 Ray Job
  → 记录新旧 Job 与 Run 的恢复关系
```

未声明或不能验证 Checkpoint 恢复能力的项目必须返回结构化 `unsupported`，不能把进程停止
伪装成安全暂停，也不能声称原 Job 被原地继续。

项目通过固定 argv 的 `checkpointEntrypoint` 和 `resumeEntrypoint` 声明能力。Checkpoint 入口输出
`{runId,path,digest}`，插件只在 MLflow Artifact API 按 Digest 回读成功后停止原 Job。恢复入口
必须包含一个完整 `{config}` 参数；原 Job、Checkpoint 和 Attempt 关系通过 Ray Runtime
Environment 与 metadata 传递，不拼接模型提供的 Shell。恢复提交和普通提交一样受 readiness
Evidence Digest 授权约束。

### 7.3 状态观察与日志游标

`galatea_observe_job` 默认只查询执行状态；首次需要日志时从 `logCursor=0` 开始，后续把前次
`nextLogCursor` 原样传回。该值是当前 Ray 累计日志字符串的字符偏移，不是行号、页码或字节偏移。
服务返回游标后的增量；增量超过上限时只返回尾部但游标仍推进到完整日志末尾。若累计日志缩短使
旧游标越界，则从头返回并标记 `logCursorReset: true`。持续监控应在首读后优先只读状态，仅在失败
诊断或终态取证时按游标继续读取日志。

### 7.4 停止和失败

停止动作必须说明目标 Job、原因和预期终态，并提供与该 Submission metadata 中提交身份匹配的
`idempotencyKey`；不匹配时返回 `conflict`，不得停止不属于该身份的 Job。失败结果至少区分：

- 可原样重试；
- 需要修改配置或资源；
- 需要人工介入；
- 不可恢复；
- 能从已验证 Checkpoint 恢复。

Harness 根据这些结果决定下一步；插件不自行启动无限重试循环。

## 8. MLflow、Artifact 与模型治理

### 8.1 Run 可比性

只有任务、数据版本、切分、预处理、指标定义、评估协议和执行角色兼容的 Runs 才能进入比较。
证据不足时，插件必须返回“不可比较”或“证据不足”，不能仅按某个同名指标排序后声称最优。

主目标和 `max`/`min` 优化方向来自项目配置，不能由共享插件按指标名称猜测。

### 8.2 Run 所有权

正式训练必须明确唯一的权威 Run 写入者。分布式 Worker 不得并发创建、结束或争用同一个父
Run，也不得各自发布局部模型冒充最终模型。安全的 Nested Run 设计必须由项目显式声明。

### 8.3 Artifact 访问

Checkpoint、模型、预测、报告和恢复元数据通过 MLflow Artifact API 记录和验证。插件不得：

- 打开或查询 MLflow Backend Store 数据库；
- 读取 MLflow 或 MinIO 服务端本地目录；
- 要求训练客户端持有服务端长期对象存储密钥；
- 把本地临时路径或 Ray Worker 临时目录当作持久 URI。

### 8.4 质量门禁

质量门禁是项目声明、插件执行的确定性规则。每个门禁必须声明指标或证据名称、比较方式、阈值、
是否必需和缺失值处理方式。

门禁输出包含每项实际值、期望值、状态和原因。缺失的必需证据必须失败；可选证据可以明确跳过。
门禁通过只表示满足已声明标准，不自动授权模型推广。

### 8.5 不可变 Release

插件只消费当前项目 `releaseRoot` 下显式选择的 `release.json`：检查清单结构和项目归属，并把
Release ID、清单声明的文件摘要和 Runtime Environment 绑定到 readiness 证据。Runtime Package
对象内容的摘要和不可变写入由项目 Release 构建/发布流程保证；插件本身不重新下载 S3 对象计算
摘要，也不会在计划或提交时构建或覆盖 Release。

源码、正式入口、依赖/打包输入或会改变数据/切分身份的配置发生变化后，必须重新构建并发布新的
内容寻址 Release，再以新的相对 `<release-id>/release.json` 重做计划和审批。旧 Release 不会自动
吸收工作区变化，也不得原地修改来复用旧审批身份。

## 9. Skill 最小化与验证

现有 `.codex/skills/` 中的内容不是 Runtime 迁移资产。首版审计结论是零内置 Skill：
`dsh-galatea` 依靠类型化 Tool、Policy 和 Schema 完成确定性平台操作，仓库级 Skills 继续只为
开发代理提供工作流说明。

### 9.1 Skill 准入条件

候选 Skill 必须同时满足：

1. 对应当前训推平台的真实任务；
2. 任务需要跨步骤判断、证据综合或诊断策略，不能完全由 Tool、Policy 或 Schema 表达；
3. 依赖的 Tool 和平台能力已经存在；
4. 明确输入证据、输出、退出条件和禁止动作；
5. 不复制 DeepSeek Harness 或插件 Tool 已有说明；
6. 通过固定场景验证，并相对“不加载该 Skill”的基线产生可测收益。

### 9.2 验证维度

每个候选 Skill 至少验证：

- 路由准确性：该用时能被选择，不该用时不会干扰；
- 任务完成率：能完成目标场景，而不是只生成建议；
- 证据完整性：结论可追溯到 Job、Run、配置和 Artifact；
- 治理合规：不比较不兼容 Runs、不泄漏测试集、不绕过审批；
- 失败恢复：平台不可用、证据缺失和调用中断时能安全停止；
- 增量价值：相对基础 Tool 描述显著改善结果或减少错误。

无法证明价值的 Skill 删除，不以“可能有用”为理由进入插件。

### 9.3 候选审计结果

| 仓库 Skill | 审计结论 |
| --- | --- |
| `model-project-structure` | 项目边界已落入声明、Policy 和契约测试；不进入插件 Runtime |
| `mlflow-optimize-models` | 保留为开发代理的诊断工作流；不进入插件 Runtime |
| `ray` | 保留为通用参考资料；不进入插件 Runtime |
| `searching-mlflow-docs` | 保留为文档检索能力；不进入插件 Runtime |

## 10. 错误、重试与恢复

Tool 错误使用稳定的结构化分类，至少包含错误类别、是否可重试、已完成的状态变更、相关平台
ID 和推荐的下一步动作。自然语言错误不能成为 Harness 判断恢复策略的唯一输入。

重试遵循以下规则：

- 读操作可以按 Harness 策略重试；
- 状态变更操作只有在幂等身份明确时才可自动重试；
- 已发布 Job ID 后的请求取消只停止当前等待，不得默默终止已发布 Job；
- 超时后先查询平台事实源，再决定是否重试；
- 恢复只接受已验证的持久 Checkpoint；
- 失败 Run、部分 Artifact 和恢复关系必须保留审计证据；
- 任何失败都不得隐式触发模型推广。

## 11. 直接替换实施记录

这是一次直接替换，不建立旧 Agent Runtime 与新插件的并行运行期。

```text
实现并验证 plugins/dsh-galatea
        +
删除旧 Agent Runtime 和全部旧入口
        +
更新依赖、文档与测试
        ↓
仓库只支持 DeepSeek Harness + dsh-galatea
```

### 11.1 删除与重写映射

| 旧内容 | 已完成处理 |
| --- | --- |
| `agent/core/`、`agent/runtime.py`、`agent/client.py` | 删除 |
| `agent/agents/` | 删除 |
| `agent/state/` | 删除，由 Harness Session 和平台事实源替代 |
| `agent/hooks/` | 删除，使用 Harness 扩展点 |
| `agent/policies/budget.py`、`permission.py` | 删除，使用 Harness 权限与预算能力 |
| `agent/workflows/` | 删除，不迁移 Workflow Runtime |
| `agent/skills/` | 删除，不迁移 Skill Registry |
| `agent/tools/server.py` 和 Claude MCP | 删除，重写为 Cordis Tools |
| `agent/demo/`、Agent CLI 和 Chat 脚本 | 删除 |
| `agent/doc/`、`agent/summary/` | 删除或把仍有效的领域事实并入正式文档；不保留旧架构说明 |
| `agent/test/` | 删除；领域行为改由插件测试覆盖 |
| 项目与数据检查 | 加固后重写到 Tool/Policy |
| Ray 状态和 Job 生命周期 | 通过正式接口重写到 Service/Tool |
| MLflow 查询和 Run 比较 | 通过 Tracking/Artifact/Registry API 重写 |
| 质量门禁 | 重写为无状态 Policy |
| 训练生命周期规则 | 重写为阶段前置条件，不迁移状态机 |

旧 `agent/services/` 只有声明而没有可复用实现，因此未原样迁移。

### 11.2 已执行的实施顺序

替换按以下顺序完成，期间未对外提供双栈模式：

1. 建立 `plugins/dsh-galatea/` 包、Cordis 注册入口和测试骨架；
2. 定义结构化结果、平台 ID、错误分类、幂等和审批证据类型；
3. 实现并验证 Ray、MLflow、Artifact 和项目入口 Services；
4. 实现项目检查、生命周期、Run 可比性和质量门禁 Policies；
5. 注册最小 Tool 集并接入 DeepSeek Harness Profile；
6. 评估候选 Skills，允许评估结果为零；
7. 完成端到端验收后删除整个旧 Agent Runtime 及其依赖、入口和引用；
8. 更新仓库 README、运维文档和测试命令，使其只描述新架构。

未建立 Python 到 TypeScript 的旧 Runtime Adapter。项目专用 Python 行为通过参数化入口执行，
使用结构化输入输出，不导入旧 Runtime。

## 12. 测试与验收

### 12.1 测试层次

- Policy 单元测试：身份、可比性、生命周期、质量门禁和一次性审批证据绑定；
- Service 契约测试：Ray 请求、错误、日志游标和幂等身份，以及 MLflow Run、Artifact、Model
  Version 分页、超时、取消和错误映射；
- Tool 测试：Schema、结构化输出、幂等和敏感信息处理；
- Cordis 装配测试：插件可从 `cordis.patch.yml` 加载，Tool 注册与卸载正确；
- 场景测试：项目检查、训练提交、暂停/恢复、Run 比较、最终验证和推广阻断；
- Skill Eval：仅对申请进入插件的候选 Skill 运行，并包含无 Skill 基线。

### 12.2 必须通过的架构验收

- DeepSeek Harness 可以加载 `dsh-galatea` 的 14 个 Tool，且无需启动任何 Galatea Agent Runtime；
- 仓库不存在 `agent/`、Claude Agent SDK、旧 MCP Server、Agent CLI 或第二套 Session/Workflow；
- 插件不修改 DeepSeek Harness Agent Loop；
- 只能选择管理员注册的项目，且选择按 Harness Session 隔离，不是进程级全局单例；
- 训练项目、模型、框架、指标和优化方向均通过配置或项目能力声明提供；
- 相同幂等身份的重复提交不会意外创建重复 Job/Run；
- 不可比较的 Runs 被拒绝进入排序和候选选择；
- Trial 阶段不能读取最终测试集；
- 必需的预处理一致性和迁移污染证据缺失、未知或失败时 readiness fail closed；
- 执行、质量、治理和完整性状态独立报告，不从 Ray 成功推断质量或批准；
- Artifact 只能通过正式 API 验证，不直接访问服务端数据库或目录；
- 不支持 Checkpoint 恢复的项目对暂停返回 `unsupported`；
- 恢复会创建新 Job，并保留与原 Job、Run 和 Checkpoint 的关系；
- 每个受治理的提交、恢复和推广动作都生成可展示的当前证据包；
- 非完全权限的当前调用未获得 `allowed-once`、被驳回、取消或无人应答时，受治理操作失败；
- 模型推广未获得当前最终验证证据的完全权限授权或一次性审批时必定失败；
- 候选 Skill 未证明增量价值时，插件仍能以零 Skill 完成确定性平台操作。

## 13. 迁移完成后的仓库边界

迁移完成后的相关结构为：

```text
Galatea/
├── plugins/
│   └── dsh-galatea/               # 训推平台插件
├── train-model/
│   └── <project-name>/             # 项目拥有训练、评估和配置
├── tests/                          # 仓库级和跨项目测试
├── doc/                            # 架构、部署与运维文档
├── systemd/                        # 平台服务部署单元
├── platform-data/                  # 运行状态，Git 忽略
├── AGENTS.md                       # 仓库级训练与治理规则
└── README.md                       # 平台入口
```

不再存在：

```text
Galatea/agent/
```

最终职责可以概括为：

> DeepSeek Harness 决定下一步做什么并维护执行循环；`dsh-galatea` 证明某个训推动作是否安全、
> 执行该动作并返回证据；Ray、MLflow 和 MinIO 保存实际运行与产物事实。
