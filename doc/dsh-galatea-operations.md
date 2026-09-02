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

## 3. 配置来源

bundle 默认配置见 [`cordis.patch.yml`](../plugins/dsh-galatea/cordis.patch.yml)。机器相关值通过
继承环境、工作区 `.env` 或 Harness Home `.env` 注入。生产部署至少显式设置项目、Release、
Ray 和 MLflow 地址：

```bash
export GALATEA_PROJECT_ROOT=/srv/galatea/train-model/project-name
export GALATEA_MANIFEST_PATH=galatea.project.yaml
export GALATEA_RELEASE_ROOT=/srv/galatea-releases/project-name
export GALATEA_RAY_BASE_URL=https://ray.internal.example
export GALATEA_MLFLOW_BASE_URL=https://mlflow.internal.example
```

实际认证值由服务管理器或密钥系统注入。`GALATEA_RAY_TOKEN_ENV` 和
`GALATEA_MLFLOW_TOKEN_ENV` 只填写持有实际 Token 的变量名。变量名已配置但实际变量缺失时，
插件拒绝启动，避免静默退化为未认证访问。

## 4. 发布训练代码

插件只消费不可变 `release.json`，不隐式构建 Runtime Environment。以当前示例项目为例：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python job/ci.py --no-cd
```

命令生成 `/tmp/ray-cats-and-dogs-job/<release-id>/release.json`，默认 bundle 的 `releaseRoot`
正是它的父目录。正式部署可把 `GALATEA_RELEASE_ROOT` 指向受控、只读挂载的 Release 清单根。
同一对象键只允许内容摘要一致的发布，不能覆盖已审批的代码身份。

## 5. 运行流程

1. 调用 `galatea_inspect_project` 检查声明。
2. 调用 `galatea_plan_run` 生成 readiness Evidence Digest。
3. 调用 `galatea_submit_job`；Tool 重算计划，并对当前 Evidence Digest 请求一次性审批。
4. 用 `galatea_observe_job`、`galatea_compare_runs` 和 `galatea_build_stage_evidence` 观察 Trial，
   形成候选 Run 的 training-optimization Evidence Digest。
5. 提交 `role=champion` 时必须同时提供候选 Run；插件重新读取候选 Evidence，并在当前调用中
   分别请求 readiness 与候选 training-optimization 的一次性审批。
6. Champion 完成后调用 `galatea_verify_candidate`，验证最终报告、模型和质量门禁。
7. 显式调用 `galatea_promote_model`；Tool 重算最终验证证据并请求一次性推广审批。

安装插件或请求分析不会自动启动昂贵训练，也不会自动修改 Registry Alias。提交和推广必须由
明确 Tool 调用触发，并满足各自审批门禁。

## 6. 故障处理

| 现象 | 处理 |
| --- | --- |
| 插件启动时报 Token 环境变量未设置 | 注入实际 Token，或移除对应 `GALATEA_*_TOKEN_ENV` |
| `approval-required` | 重试实际状态变更 Tool，让它为当前 Evidence Digest 重新请求一次性审批 |
| `conflict` | 检查 Ray Submission ID 或推广幂等键是否已绑定另一身份 |
| `unsupported` 暂停/恢复 | 项目没有声明并验证跨 Job Checkpoint 恢复，保留原 Job 证据后新建 Attempt |
| Artifact `not-found`/`integrity-error` | 通过 MLflow Artifact API 检查 Run 和声明路径，不读取 MinIO 服务端目录 |
| Ray 请求超时 | 先按 Submission ID 查询 Ray 事实源，再决定是否重试 |

卸载或 HMR 替换插件 fiber 会移除全部 12 个 Tool；Harness Session、Ray Job、
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
Ray Job。当前 `ray-cats-and-dogs` 未声明这项能力，因此仍安全返回 `unsupported`。

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
```

验收使用 mock/loopback 服务，不提交真实训练、不写生产 Alias。发布前另行执行平台健康检查和
项目只读 `--plan`。
