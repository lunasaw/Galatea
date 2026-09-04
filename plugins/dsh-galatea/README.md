# dsh-galatea

`dsh-galatea` 是 Galatea 面向 DeepSeek Harness 的唯一 Agent 扩展。它把训练项目契约、Ray Jobs、
MLflow Tracking/Artifact/Registry 和 Harness 权限/审批组合成类型化 Tool；不实现 Agent Loop、
Session、Workflow、Skill Registry、权限系统或客户端。

## 能力边界

插件注册 14 个 Tool：

| 领域 | Tool |
| --- | --- |
| 项目路由 | `galatea_list_projects`、`galatea_select_project` |
| 项目与配置 | `galatea_inspect_project`、`galatea_patch_config`、`galatea_plan_run` |
| Ray Job | `galatea_submit_job`、`galatea_observe_job`、`galatea_stop_job`、`galatea_pause_job`、`galatea_resume_job` |
| Run 与证据 | `galatea_compare_runs`、`galatea_build_stage_evidence`、`galatea_verify_candidate` |
| 授权与推广 | “完全权限”直接授权提交、恢复和推广；其他权限由 Tool 请求 `allowed-once`；`galatea_promote_model` 执行推广 |

项目入口来自 `galatea.project.yaml` 的固定 argv，模型不能提交任意 Shell。MLflow 只通过
Tracking、Artifact 和 Registry HTTP API 访问；插件不读取 `mlflow.db`、MinIO 服务端目录或
Ray 临时目录。首版 `skills/` 为空，确定性规则都在 Tool、Policy 和 Schema 中。

正式训练还有三道不可由提示词绕过的硬门禁：注册项目必须是 `train-model/<project-name>/` 下的
完整 workload（`README.md`、`configs/`、`src/<matching-python-package>/`、`scripts/`、
`tests/` 和 `conda.yaml`/`pyproject.toml`）；清单必须声明 `executionBackend: ray`；当前 Session
必须先成功调用 `galatea_plan_run`，随后才能以相同项目、配置、Release、角色和 attempt 调用
`galatea_submit_job`。提交前插件会重算 readiness digest，若工作区或计划已变化则拒绝提交。
短时、低风险检查和探索实验可以本地运行，但不能形成 Galatea 正式证据；正式 Trial/Champion、长时或
资源密集训练优先使用 Ray Job。直接运行本地命令不得绕过失败的结构、依赖、数据、切分或 release 预检。

## 安装到 Harness Profile

在插件目录构建，然后通过 Harness 的 Profile 插件命令安装当前 checkout：

```bash
cd /data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea
corepack pnpm install --frozen-lockfile --config.auto-install-peers=false
corepack pnpm build
dsh plugin --profile web add .
```

`package.json` 的 `dsh.bundle.patch` 会把 [`cordis.patch.yml`](cordis.patch.yml) 作为一个 bundle
层装入 Profile。可用 Harness 的无启动配置转储检查最终组合：

```bash
dsh --profile web --dump-config
```

默认 bundle 提供管理员配置的受信项目注册表，共三个项目：

- `ray-cats-and-dogs`（默认选择），Release 根为
  `/data/ai/chenzhangyue/code/galatea/platform-data/ray-cats-and-dogs-release`；
- `ray-handwritten-digits`，Release 根为
  `/data/ai/chenzhangyue/code/galatea/platform-data/ray-handwritten-digits-release`；
- `ray-kaggle-house-prices`，Release 根为
  `/data/ai/chenzhangyue/code/galatea/platform-data/ray-kaggle-house-prices-release`。

`galatea_list_projects` 只列出该注册表；`galatea_select_project` 只接受其中的 ID。成功的选择由
Harness 标准 `tool/call`、`tool/result` 或 `tool/code-dispatch` Session 事件记录，并经
`galateaProjectSelection` projection/replay 派生该 Session 的当前项目；未选择时使用
`defaultProject`。模型不能提交新的根目录、清单路径或任意项目 ID。注册表在插件启动时
规范化绝对 `projectRoot`/`releaseRoot`，拒绝重复 ID、重复项目清单身份和越界或符号链接逃逸的
`manifestPath`。这套受信路由限制模型可操作的项目，但共享的 Ray/MLflow 凭据和服务仍是同一信任域；
它不是租户隔离、身份认证或服务端授权边界。部署其他项目或主机必须由管理员修改 Profile/bundle 配置并
重新加载插件，不能由模型或 Session 中的项目选择改写。例如：

```yaml
projects:
  - id: my-project
    projectRoot: /srv/galatea/train-model/my-project
    manifestPath: galatea.project.yaml
    releaseRoot: /srv/galatea-releases/my-project
defaultProject: my-project
rayBaseUrl: https://ray.internal.example
mlflowBaseUrl: https://mlflow.internal.example
```

Ray/MLflow 地址仍可由 bundle 使用的 `GALATEA_RAY_BASE_URL` 和 `GALATEA_MLFLOW_BASE_URL`
注入；它们不选择项目。

认证配置只保存环境变量名，不保存 Token。需要 Bearer Token 时，先把实际 Token 注入受保护
环境，再告诉插件该变量的名字：

```bash
export RAY_JOB_API_TOKEN='<由密钥系统注入>'
export MLFLOW_API_TOKEN='<由密钥系统注入>'
export GALATEA_RAY_TOKEN_ENV=RAY_JOB_API_TOKEN
export GALATEA_MLFLOW_TOKEN_ENV=MLFLOW_API_TOKEN
```

不要把实际 Token 写入 `cordis.patch.yml`、项目声明、Harness Session 或 Tool 参数。

## 项目和 Release 契约

`projectRoot` 下必须有 `TrainingProject` 声明。声明固定主目标及 `max`/`min` 方向、配置目录、
正式入口、Run 可比性字段、MLflow Experiment、阶段 Artifact、模型 URI Tag、完整性规则和质量门禁。
声明解析会拒绝绝对/越界 Artifact 路径、任意 Shell 字符串和 secret-like 字段。

各类路径的基准不同：管理员配置中的 `projectRoot`、`releaseRoot` 必须为绝对目录；
`manifestPath` 相对 `projectRoot`；Tool 参数 `configPath` 相对 `projectRoot` 且必须位于清单声明的
`configRoot` 下；`releaseManifestPath` 相对当前项目的 `releaseRoot`；MLflow Artifact 路径相对
对应 Run 的 Artifact 根。绝对路径、`..` 越界和解析后的符号链接逃逸会被拒绝。

`releaseRoot` 是已发布的不可变 Ray Runtime Environment 清单根目录。`galatea_plan_run` 只消费
显式 `release.json`，检查清单结构与项目归属，并把 Release ID、清单声明的文件摘要和 Runtime
Environment 绑定到 readiness 身份；对象内容的摘要/不可变发布由项目 Release 构建与发布流程
保证，插件不会重新下载 S3 Runtime Package 计算摘要，也不会在计划或提交时构建、上传或覆盖
Release。源码、执行脚本、打包配置或会改变数据/切分身份的配置变化后，
必须重新运行项目 Release 构建/发布流程并改用新的 `<release-id>/release.json`；旧 Release 保持
不可变，也不会自动吸收工作区变化。

## 执行、完整性与治理语义

- 带 `operationStatus` 的生命周期/证据结果分别报告 `statuses.execution`、`quality`、
  `governance`，以及独立的 `integrity.preprocessingParity` 和 `migrationContamination`；Ray Job
  `SUCCEEDED` 只表示执行成功，不代表质量通过、完整性已证明或已获推广批准。
- `galatea_plan_run` 以项目 `--plan` 的结构化输出验证声明的项目结构、预处理上下文一致性、迁移来源和
  污染检查。项目目录、固定入口、依赖、release、必需字段或检查缺失，或者状态未知/失败时，readiness
  fail closed，不生成可提交计划；这类结构或完整性问题不是 advisory。非阻断 backlog 才作为 advisory。
- 短时、低风险的本地检查或探索实验可以直接运行项目的参数化入口，但不能把结果当作 governed Ray
  Run 或最终证据。正式 Trial/Champion、长时或资源密集训练优先使用 `galatea_plan_run` →
  `galatea_submit_job` 的 Ray Job 流程。
- Trial 只能使用训练集和验证集；Champion 才能执行最终测试。
- Run 比较要求任务、数据、切分、预处理、指标定义、评估协议和角色全部兼容。
- 相同计划身份生成确定性的 Ray Submission ID；提交校验受治理 metadata，停止要求
  `idempotencyKey` 与该 Submission 的身份匹配，冲突身份 fail closed。
- 当前两个项目都未声明安全的跨 Job Checkpoint 恢复，因此暂停和恢复返回 `unsupported`。
- 对声明 `pauseResume: true` 的项目，`checkpointEntrypoint` 不接受模板参数，通过
  `GALATEA_SUBMISSION_ID` 和 `GALATEA_PAUSE_REASON` 接收上下文，并向 stdout 输出唯一的
  `{runId,path,digest}` JSON。插件通过 MLflow Artifact API 校验摘要后才停止原 Job。
- `resumeEntrypoint` 必须包含且只包含一个完整的 `{config}` 参数。恢复 Tool 将原 Job、Run、
  Artifact 和 Attempt 关系注入 Ray Runtime Environment/metadata，重算 readiness 证据并获得当前
  权限授权，然后提交新的确定性 Submission ID；不原地继续旧 Job。
- 提交和推广前由 Tool 重算 Evidence Digest。“完全权限”（Harness preset
  `danger-full-access`）对当前证据直接生成绑定授权，不弹审批；其他权限在同一次 Tool 调用中请求
  `allowed-once`。
- Champion 提交还必须绑定经过授权的 training-optimization Trial Run；插件不会接受只有 readiness
  授权、没有候选选择证据的 Champion。
- 非完全权限下，拒绝、取消、无审批应答者或当前调用未获 `allowed-once` 全部 fail closed。
- 推广从不自动发生；只有显式调用 `galatea_promote_model` 且当前证据已获授权，才用幂等键创建或
  复用 Model Version 并设置 Alias；相同键指向不同证据时返回 `conflict`。

插件通过 Harness `permissionPresets.current(session)` 读取当前有效 preset，不根据
`approval.policy=never` 猜测完全权限。只有 `danger-full-access` 跳过审批；该授权仍绑定阶段、产物 ID
和 Evidence Digest，readiness 重算、Champion 候选证据、质量门禁和显式推广调用均不跳过。

非完全权限的高风险动作通过 Harness 现有 `ApprovalService.request()` 请求一次性 `allowed-once`。实际
回答由部署已经配置的 Harness UI、ACP 或其他普通审批 answerer 负责；插件不注册审批 answerer，
不保存审批副本，也不定义自有审批事件。Harness `ApprovalService` 仍会为每次请求记录标准
`approval/asked` 和 `approval/decided` Session 事件。一次性授权只覆盖当前 Tool 调用；重试、证据变化或
下一阶段必须重新请求审批。`galatea_inspect_project` 同时报告 `permissionPreset`、
`promptsEnabled`、`requiredForGovernedActions`、`governedActionsAvailable` 和
`authorizationMode`。若策略为 `never` 且
preset 不是 `danger-full-access`，提交、恢复和推广仍按 `approval-required` fail closed；重复调用不能
绕过。只读检查、计划、观察、比较和证据验证仍可使用。

`galatea_observe_job` 默认只读状态。需要日志时首次传 `includeLogs: true`（游标缺省为 `0`），
后续把上次返回的 `nextLogCursor` 原样传为 `logCursor`；游标是 Ray 返回的完整累计日志中的字符
偏移，不是字节偏移。服务每次重新读取累计日志并返回该偏移后的增量；若增量超过
`maxLogChars`，只返回尾部并置 `logsTruncated: true`，但 `nextLogCursor` 仍指向完整日志末尾。
若日志缩短使旧游标越界，则从 `0` 重置并置 `logCursorReset: true`。首读后优先做不含日志的状态
观察，仅在失败或终态证据收集时继续按游标取日志。

项目本地校验/计划/Checkpoint 入口不继承整个 Harness 环境。默认 allowlist 仅为 `PATH`、
`HOME`、`LANG`、`LC_ALL`、`PYTHONPATH`、`CONDA_PREFIX`、`CONDA_DEFAULT_ENV`、
`MLFLOW_TRACKING_URI`、`RAY_ADDRESS`、`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`；管理员可用
`projectProcessInheritedEnv` 替换整份名单。插件另按操作注入必要的 Galatea 上下文变量，未列入的
环境变量（尤其 Token/Secret）不会传给项目子进程。

## 开发和验证

```bash
cd /data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea
node --test tests/*.test.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/tsc -p tsconfig.build.json
```

真实 Harness 源码级 Tool、Session 审批和 Cordis 装配测试从相邻 checkout 运行：

```bash
/data/ai/chenzhangyue/code/deepseek-harness/node_modules/.bin/vitest run \
  --config /data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea/vitest.harness.config.ts
```

Service 测试会绑定 loopback 临时端口；受限执行环境需要允许本地监听。测试不会启动正式训练，
不会修改真实 Registry Alias。
