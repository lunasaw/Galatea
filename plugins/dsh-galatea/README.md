# dsh-galatea

`dsh-galatea` 是 Galatea 面向 DeepSeek Harness 的唯一 Agent 扩展。它把训练项目契约、Ray Jobs、
MLflow Tracking/Artifact/Registry 和一次性 Harness 审批组合成类型化 Tool；不实现 Agent Loop、
Session、Workflow、Skill Registry、权限系统或客户端。

## 能力边界

插件注册 12 个 Tool：

| 领域 | Tool |
| --- | --- |
| 项目与配置 | `galatea_inspect_project`、`galatea_patch_config`、`galatea_plan_run` |
| Ray Job | `galatea_submit_job`、`galatea_observe_job`、`galatea_stop_job`、`galatea_pause_job`、`galatea_resume_job` |
| Run 与证据 | `galatea_compare_runs`、`galatea_build_stage_evidence`、`galatea_verify_candidate` |
| 审批与推广 | 提交、恢复和推广 Tool 内部请求 `allowed-once`；`galatea_promote_model` 执行推广 |

项目入口来自 `galatea.project.yaml` 的固定 argv，模型不能提交任意 Shell。MLflow 只通过
Tracking、Artifact 和 Registry HTTP API 访问；插件不读取 `mlflow.db`、MinIO 服务端目录或
Ray 临时目录。首版 `skills/` 为空，确定性规则都在 Tool、Policy 和 Schema 中。

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

默认 bundle 指向当前仓库的 `ray-cats-and-dogs` 项目、回环 Ray Dashboard 和回环 MLflow。
部署到其他项目或主机时设置：

```bash
export GALATEA_PROJECT_ROOT=/srv/galatea/train-model/my-project
export GALATEA_MANIFEST_PATH=galatea.project.yaml
export GALATEA_RELEASE_ROOT=/srv/galatea-releases/my-project
export GALATEA_RAY_BASE_URL=https://ray.internal.example
export GALATEA_MLFLOW_BASE_URL=https://mlflow.internal.example
```

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
正式入口、Run 可比性字段、MLflow Experiment、阶段 Artifact、模型 URI Tag 和质量门禁。
声明解析会拒绝绝对/越界 Artifact 路径、任意 Shell 字符串和 secret-like 字段。

`releaseRoot` 是已发布的不可变 Ray Runtime Environment 清单根目录。`galatea_plan_run` 只消费
显式 `release.json`，验证项目、Release ID、代码包摘要和 Runtime Environment；插件不会在
计划或提交时构建、上传或覆盖 Release。

## 治理语义

- Trial 只能使用训练集和验证集；Champion 才能执行最终测试。
- Run 比较要求任务、数据、切分、预处理、指标定义、评估协议和角色全部兼容。
- 相同计划身份生成确定性的 Ray Submission ID；冲突身份 fail closed。
- 当前项目未声明安全的跨 Job Checkpoint 恢复，因此暂停和恢复返回 `unsupported`。
- 对声明 `pauseResume: true` 的项目，`checkpointEntrypoint` 不接受模板参数，通过
  `GALATEA_SUBMISSION_ID` 和 `GALATEA_PAUSE_REASON` 接收上下文，并向 stdout 输出唯一的
  `{runId,path,digest}` JSON。插件通过 MLflow Artifact API 校验摘要后才停止原 Job。
- `resumeEntrypoint` 必须包含且只包含一个完整的 `{config}` 参数。恢复 Tool 将原 Job、Run、
  Artifact 和 Attempt 关系注入 Ray Runtime Environment/metadata，重算 readiness 证据并在当前
  Tool 调用中请求一次性审批，然后提交新的确定性 Submission ID；不原地继续旧 Job。
- 提交和推广前由 Tool 重算 Evidence Digest，并在同一次 Tool 调用中请求 `allowed-once`。
- Champion 提交还必须绑定一个经过一次性审批的 training-optimization Trial Run；插件不会接受
  只有 readiness 审批、没有候选选择证据的 Champion。
- 拒绝、取消、无审批应答者或当前调用未获 `allowed-once` 全部 fail closed。
- 推广使用幂等键创建或复用 Model Version；相同键指向不同证据时返回 `conflict`。

高风险动作通过 Harness 现有 `ApprovalService.request()` 请求一次性 `allowed-once` 决定。实际
回答由部署已经配置的 Harness UI、ACP 或其他普通审批 answerer 负责；插件不注册审批 answerer，
不保存审批副本，也不新增 Session 事件。一次性授权只覆盖当前 Tool 调用；重试、证据变化或
下一阶段必须重新请求审批。

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
