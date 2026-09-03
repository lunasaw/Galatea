# dsh-galatea 部署与运维

本文说明如何把 [`dsh-galatea`](../plugins/dsh-galatea/README.md) 装入 DeepSeek Harness，并连接
现有 Ray、MLflow 和训练项目。架构和治理不变量见
[`agent-galatea.md`](agent-galatea.md)。

## 1. 部署前检查

插件不启动平台服务。装载前确认事实源可用：

```bash
systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
```

Ray Dashboard Jobs API 默认是 `http://127.0.0.1:8265`。远程地址必须通过受控私网、HTTPS 或
认证代理暴露，不能把未认证 Dashboard 直接公开。

## 2. 构建和安装

```bash
cd /data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea
corepack pnpm install --frozen-lockfile --config.auto-install-peers=false
corepack pnpm typecheck
corepack pnpm build
dsh plugin --profile web add .
dsh --profile web --dump-config
```

`dsh plugin` 把包安装到指定 Profile，并依据包内 `dsh.bundle.patch` 激活 bundle。安装后的
Profile 只增加 Tool，不增加审批 answerer、第二个 Agent Runtime 或独立服务进程。审批由 Harness
Profile 已配置的普通 UI、ACP 或其他 answerer 处理。

## 3. 配置来源与受信项目注册表

bundle 默认配置见 [`cordis.patch.yml`](../plugins/dsh-galatea/cordis.patch.yml)。项目不是由模型或
Tool 路径参数发现，而是由管理员在插件配置的 `projects` 注册表中逐项授权。
当前 bundle 配置三个项目：

| 项目 ID | `projectRoot` | `releaseRoot` |
| --- | --- | --- |
| `ray-cats-and-dogs`（默认） | `/data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs` | `/data/ai/chenzhangyue/code/galatea/platform-data/ray-cats-and-dogs-release` |
| `ray-handwritten-digits` | `/data/ai/chenzhangyue/code/galatea/train-model/ray-handwritten-digits` | `/data/ai/chenzhangyue/code/galatea/platform-data/ray-handwritten-digits-release` |
| `ray-kaggle-house-prices` | `/data/ai/chenzhangyue/code/galatea/train-model/ray-kaggle-house-prices` | `/data/ai/chenzhangyue/code/galatea/platform-data/ray-kaggle-house-prices-release` |

生产部署由管理员修改 Profile/bundle 并重新加载插件，例如：

```yaml
projects:
  - id: project-name
    projectRoot: /srv/galatea/train-model/project-name
    manifestPath: galatea.project.yaml
    releaseRoot: /srv/galatea-releases/project-name
defaultProject: project-name
rayBaseUrl: https://ray.internal.example
mlflowBaseUrl: https://mlflow.internal.example
```

`projectRoot` 和 `releaseRoot` 必须是已经存在的绝对目录；`manifestPath` 相对 `projectRoot`，
缺省为 `galatea.project.yaml`。启动时会规范化真实路径，并拒绝空/重复 ID、重复项目清单身份、
未知配置字段、越界路径和符号链接逃逸。`galatea_list_projects` 只展示管理员已经配置的条目；
`galatea_select_project` 只接受其中一个 ID。成功调用写入 Harness 标准 `tool/call`、`tool/result`
或 `tool/code-dispatch` Session 事件，并由 `galateaProjectSelection` projection/replay 派生当前选择；
失败或 malformed 调用不会改变选择。未选择时使用 `defaultProject`；一个 Session 的选择不改动
其他 Session，也不改写全局默认值。

这个注册表及项目归属检查是模型能力路由，不是多租户安全边界。各项目仍共享插件进程和配置的
Ray/MLflow 服务凭据；真正的租户隔离必须由独立凭据、服务端 ACL、网络/进程边界及必要时独立
Harness Profile 提供。

Ray/MLflow 地址可由 bundle 当前使用的 `GALATEA_RAY_BASE_URL` 和
`GALATEA_MLFLOW_BASE_URL` 注入。实际认证值由服务管理器或密钥系统注入；
`GALATEA_RAY_TOKEN_ENV` 和 `GALATEA_MLFLOW_TOKEN_ENV` 只填写持有实际 Token 的变量名。
变量名已配置但实际变量缺失时，插件拒绝启动，避免静默退化为未认证访问。

插件启动的本地项目入口不会继承完整 Harness 环境。默认 `projectProcessInheritedEnv` allowlist
只有 `PATH`、`HOME`、`LANG`、`LC_ALL`、`PYTHONPATH`、`CONDA_PREFIX`、
`CONDA_DEFAULT_ENV`、`MLFLOW_TRACKING_URI`、`RAY_ADDRESS`、`HTTP_PROXY`、
`HTTPS_PROXY`、`NO_PROXY`。管理员配置 `projectProcessInheritedEnv` 时是替换整份名单，不是自动
追加；仅填写确需传入项目校验/计划进程的变量，禁止把 Token/Secret 加入名单。插件按具体操作另行
注入 Checkpoint/恢复上下文，不通过通用继承传递服务凭据。

## 4. 发布训练代码

插件只消费不可变 `release.json`，不隐式构建 Runtime Environment。以当前示例项目为例：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python job/ci.py --no-cd
```

两个项目的发布脚本默认写入 `/tmp/ray-*-job`，与当前 bundle 注册的持久 Release 根不同，
因此发布时必须显式对齐：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python job/ci.py --no-cd \
  --output-dir /data/ai/chenzhangyue/code/galatea/platform-data/ray-cats-and-dogs-release

cd /data/ai/chenzhangyue/code/galatea/train-model/ray-handwritten-digits
/data/conda/envs/attend-ray-py312/bin/python job/ci.py --no-cd \
  --output-dir /data/ai/chenzhangyue/code/galatea/platform-data/ray-handwritten-digits-release
```

命令生成所选 Release 根下的 `<release-id>/release.json`，相应 Tool 参数是相对路径
`<release-id>/release.json`。正式部署应在管理员注册表中把
`releaseRoot` 设为受控、只读挂载的 Release 清单根。同一对象键只允许内容摘要一致的发布，不能
覆盖已审批的代码身份。

插件检查 Release 清单结构和项目归属，并把清单声明的 Release ID、文件摘要和 Runtime
Environment 绑定到 readiness 身份；它不重新下载 S3 Runtime Package 计算内容摘要。Release
构建/发布脚本负责内容摘要和不可变对象写入。

Release 包含内容寻址的 `working-dir.zip`、Wheel 和 Runtime Environment，不是工作区的实时视图。
源码、正式入口、打包输入或会改变数据/切分身份的配置发生变化后，先重新运行项目的 CI/发布命令，
获得新的 `<release-id>/release.json`，再重新 `galatea_plan_run`。旧不可变 Release 不会自动吸收
这些变化；继续使用旧清单会运行旧包，不能以修改工作区代替重建。

## 5. 运行流程

插件共注册 14 个 Tool。典型流程是：

1. 调用 `galatea_list_projects`，再用 `galatea_select_project` 为当前 Session 选择注册项目。
2. 调用 `galatea_inspect_project` 检查声明、服务身份和审批策略。
3. 调用 `galatea_plan_run` 运行只读 preflight，验证完整性并生成 readiness Evidence Digest。
4. 调用 `galatea_submit_job`；Tool 重算计划，并对当前 Evidence Digest 请求一次性审批。
5. 用 `galatea_observe_job`、`galatea_compare_runs` 和 `galatea_build_stage_evidence` 观察 Trial，
   形成候选 Run 的 training-optimization Evidence Digest。
6. 提交 `role=champion` 时必须同时提供候选 Run；插件重新读取候选 Evidence，并在当前调用中
   分别请求 readiness 与候选 training-optimization 的一次性审批。
7. Champion 完成后调用 `galatea_verify_candidate`，验证最终报告、模型和质量门禁。
8. 只有用户明确要求时才显式调用 `galatea_promote_model`；Tool 重算最终验证证据并请求一次性
   推广审批。

`configPath` 必须相对当前项目 `projectRoot`，且解析后位于项目清单的 `configRoot` 下；
`releaseManifestPath` 必须相对当前项目 `releaseRoot`。不要传绝对路径。MLflow Artifact 路径则
相对对应 Run 的 Artifact 根，不相对本地文件系统。

安装插件、选择项目、计划、观察或分析都不会自动启动昂贵训练，也不会自动创建 Model Version 或
修改 Registry Alias。提交和推广必须由明确 Tool 调用触发，并满足各自审批门禁。

### 5.1 独立状态与完整性 fail closed

包含 `operationStatus` 的生命周期/证据结果中，各状态维度不能互相替代：

- `operationStatus.statuses.execution`：Ray/操作执行状态；
- `operationStatus.statuses.quality`：声明式质量门禁状态；
- `operationStatus.statuses.governance`：当前审批/推广治理状态；
- `operationStatus.integrity.preprocessingParity` 和 `migrationContamination`：预处理一致性与
  迁移污染完整性状态。

因此 Ray Job `SUCCEEDED` 仍可能对应质量未评估、治理未知或完整性未证明。`galatea_plan_run`
读取项目 `--plan` 的完整性输出；项目清单未声明完整性、必需上下文/字段/检查缺失、状态为
`unknown`/`failed`，或角色适用的必需检查报告 `not-applicable`，都会阻止 readiness 和后续提交。
只有清单中明确标为非阻断的 improvement backlog 作为 advisory 返回。

### 5.2 审批提示禁用

先查看 `galatea_inspect_project` 的 `approval.policy` 和 `promptsEnabled`。若 Session 策略为
`never`，审批提示已禁用，`galatea_submit_job`、`galatea_resume_job`、`galatea_promote_model`
无法取得 `allowed-once`，会以 `approval-required` fail closed；无需审批请求的 Tool 仍按各自语义
可用。注意 `galatea_patch_config` 会修改并校验工作区 YAML，`galatea_stop_job` 会停止指定 Job；
它们当前不请求这三个受治理 Tool 使用的一次性阶段审批，不能统称为只读。重复调用受治理 Tool 不会
绕过策略；需要提交、恢复或推广时，应由管理员/用户在 Harness 层启用审批后再调用。

### 5.3 Ray 日志游标

`galatea_observe_job` 默认只查状态。第一次需要日志时传 `includeLogs: true`，`logCursor` 可省略
（等同 `0`）；后续必须把前次 `nextLogCursor` 原样传回。游标表示 Ray 累计日志字符串的字符偏移，
不是字节偏移。服务每次重新读取累计日志，只返回游标后的增量；增量超过 `maxLogChars` 时仅保留
尾部，置 `logsTruncated: true`，但 `nextLogCursor` 仍是完整累计日志末尾。若 Ray 日志被截短或
重置导致旧游标越界，则从 `0` 返回并置 `logCursorReset: true`。首读后优先做无日志状态轮询，仅在
失败诊断或终态取证时继续按游标读取。

## 6. 故障处理

| 现象 | 处理 |
| --- | --- |
| 插件启动时报 Token 环境变量未设置 | 注入实际 Token，或移除对应 `GALATEA_*_TOKEN_ENV` |
| `approval-required` | 若提示已启用，重试实际状态变更 Tool 以对当前 Evidence Digest 发起新的单次请求；若 `approval.policy=never`，先在 Harness 层启用审批，重复调用本身不能绕过策略 |
| `conflict` | 检查 Ray Submission ID、停止操作的 `idempotencyKey` 或推广幂等键是否与已有身份一致 |
| `unsupported` 暂停/恢复 | 项目没有声明并验证跨 Job Checkpoint 恢复，保留原 Job 证据后新建 Attempt |
| Artifact `not-found`/`integrity-error` | 通过 MLflow Artifact API 检查 Run 和声明路径，不读取 MinIO 服务端目录 |
| Ray 请求超时 | 先按 Submission ID 查询 Ray 事实源，再决定是否重试 |

卸载或 HMR 替换插件 fiber 会移除全部 14 个 Tool；Harness Session、Ray Job、
MLflow Run 与 Artifact 不由插件 fiber 删除。

声明 `pauseResume: true` 的项目必须提供固定 argv 的 `checkpointEntrypoint` 和
`resumeEntrypoint`。Checkpoint 入口从 `GALATEA_SUBMISSION_ID`、`GALATEA_PAUSE_REASON` 读取请求，
将持久化 Artifact 后的 `{runId,path,digest}` JSON 写到 stdout；其他日志写 stderr。Resume 入口
只允许一个完整 `{config}` 占位符，并从以下 Ray Runtime Environment 变量读取恢复关系：

- `GALATEA_RESUMED_FROM_SUBMISSION_ID`
- `GALATEA_RESUME_RUN_ID`
- `GALATEA_RESUME_ARTIFACT_PATH`
- `GALATEA_RESUME_ARTIFACT_DIGEST`
- `GALATEA_RESUME_ATTEMPT`

插件先用 MLflow Artifact API 校验 Checkpoint，再为“原 Job + Checkpoint + 配置 + Release”生成
新的 readiness Evidence Digest。只有恢复 Tool 的当前审批请求返回 `allowed-once`，才提交新的
Ray Job。当前配置的 `ray-cats-and-dogs` 和 `ray-handwritten-digits` 都未声明这项能力，因此仍安全返回
`unsupported`。

## 7. 验收

```bash
cd /data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea
node --test tests/*.test.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/tsc -p tsconfig.build.json

/data/ai/chenzhangyue/code/deepseek-harness/node_modules/.bin/vitest run \
  --config "$PWD/vitest.harness.config.ts"

/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s ../../train-model/ray-cats-and-dogs/tests -p 'test_*.py'
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s ../../train-model/ray-handwritten-digits/tests -p 'test_*.py'
```

验收使用 mock/loopback 服务，不提交真实训练、不写生产 Alias。发布前另行执行平台健康检查和
项目只读 `--plan`。
