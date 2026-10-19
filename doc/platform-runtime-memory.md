# 新机器 Ray / MLflow / MinIO 运行记忆

更新：2026-10-14。本文件是仓库级持久记忆，记录已验证的机器事实、操作判断和故障案例。
具体部署见 [当前机器服务部署](local-services-18100.md)，Ray 进程与凭据维护见
[Ray 启动手册](ray-start.md)。迁移或执行前重新核对事实；历史验收记录不授予新的执行权限。

## 当前机器事实

| 项目 | 已验证配置 |
| --- | --- |
| 仓库 | `/data/ai/chenzhangyue/galatea-model/galatea` |
| 运行数据根目录 | `/data/ai/chenzhangyue/galatea-model/platform-data` |
| 兼容路径 | `galatea/platform-data` 是指向上述目录的软链接 |
| 原始训练数据 | `/data/ai/chenzhangyue/galatea-model/train-data` |
| MinIO S3 / Console | `http://10.26.0.140:18101` / `http://10.26.0.140:18102` |
| MLflow Tracking / UI | `http://10.26.0.140:18103`，服务 `mlflow.service` |
| 开发 Ray GCS | `10.26.0.140:18104`，服务 `ray-head.service` |
| 开发 Ray Jobs / Dashboard | `http://10.26.0.140:8265` |
| 开发 Ray Client | `ray://10.26.0.140:10001` |
| 开发 Ray 资源 | `dev:4`；物理 GPU `0,3,6`，Ray `--num-gpus=3` |
| 项目环境 | `/data/miniconda3/envs/ray-cats-and-dogs-py312` |
| 实际版本 | Ray `2.53.0`，MLflow `3.14.0`；提交前以项目锁和实际环境为准 |

MinIO 数据目录、MLflow 数据库及独立 venv 已迁移到新运行数据根目录。修改服务路径时，
同时核对 unit、EnvironmentFile、venv 脚本/shebang 和 WorkingDirectory；只保留软链接
不能证明迁移完成。运行数据、Release、证据回执和密钥不进入 Git。

## 多 Ray 集群：先选择目标，再操作

同机可能同时有开发 Head、生产/业务 Worker、推理服务或其他 Ray 集群。主机 IP、
Dashboard 端口 `8265`、Shell 当前环境或“发现一个 Ray 进程”都不足以确定目标。

为每个集群维护 profile，字段至少包含：环境、GCS 地址、Jobs API 地址、Client 地址、
systemd unit、节点集合、GPU 分配、资源标签、运行临时目录、端口范围、Redis 存储 namespace、
MLflow URI/Experiment、Runtime 只读凭据文件、发布凭据文件、Bucket/Prefix、允许角色。
参考 [无密钥 profile 示例](ray-clusters.example.yaml)。该 YAML 是配置设计示例，当前提交器
不自动读取它；生产 profile 必须从管理员配置获取，不能根据开发端口推算。

开发目标的显式选择示例：

```bash
export GALATEA_RAY_PROFILE=dev
export RAY_ADDRESS=10.26.0.140:18104
export RAY_API_SERVER_ADDRESS=http://10.26.0.140:8265
export MLFLOW_TRACKING_URI=http://10.26.0.140:18103
export MLFLOW_EXPERIMENT_NAME=ray-cats-and-dogs

systemctl list-units --all --type=service 'ray*'
ss -lntp
curl -fsS "$RAY_API_SERVER_ADDRESS/api/version"
/data/miniconda3/envs/attend-ray-py312/bin/ray status --address="$RAY_ADDRESS"
curl -fsS "$MLFLOW_TRACKING_URI/health"
```

核对 profile 与服务实际配置、节点及资源是否一致，再使用项目固定入口。记录实际 cluster ID
（接口提供时）或 session identity；取不到时明确缺项，不能用 profile 名伪造 cluster ID。
地址用途不同：`RAY_ADDRESS` 是 GCS/运行时连接地址，`RAY_API_SERVER_ADDRESS` 是 HTTP Jobs/State
API，`ray://` 是 Client。不能把三者互换。浏览器的 `/proxy/8265/` 地址也不自动等于 Jobs API 根地址。

共享机器上避免裸 `ray status`、`ray.init()`、`address="auto"` 和全局 `ray stop`。
独立启动集群时分开 GCS、Dashboard、Client、Agent、Worker 端口以及临时目录；Worker 端口范围
不能覆盖控制面端口。共享 Redis 时使用不同 `RAY_external_storage_namespace`。
停止或重启只针对已选定 unit/Job，在维护范围内处理其进程组，不清理其他集群的进程或会话目录。

Head 和每台 Worker 的服务环境也要固定对应的 `RAY_ADDRESS` 与 `RAY_API_SERVER_ADDRESS`。
本次案例中，仅设置提交终端地址不能覆盖 Ray Train 的后台 Controller/PlacementGroupCleaner，
内部自动发现可能选错集群或失败。开发 GPU 集合须与生产/推理服务错开；资源标签约束按项目的
ScalingConfig/placement 声明执行。Ray 逻辑 GPU 数不代表显存比例。

Profile 是操作配置名，不是权限校验器。当前 `governed.mjs` 强制显式 profile、GCS 和服务地址，并在
本地回执记录目标；它尚未加载 profile 注册表、验证生产 ACL 或把目标 cluster ID 绑定到
Galatea Readiness 摘要。因此仍须核对目标与任务授权，不能把填入 `prod` 当成生产授权。

## 授权与文件上传

先使用当前会话已给出的任务授权。用户明确要求“打通该项目完整 Ray + MLflow + MinIO 训练”，
其固定链路所需的该项目不可变 Runtime Release 上传和约定训练属于任务范围；无需每上传一个
ZIP/Wheel 都重新确认。用户明确指定数据文件、目标 Bucket/Prefix 并要求上传时，可直接执行该上传。
同一授权范围内的必要修复、只读计划、验证和合理重试可继续；分析、改代码或更新本文件本身不授权新训练。

分别判断数据使用/上传、Runtime Release 发布、计算预算/目标集群、最终测试、生产注册/别名变更。
已授权的开发 smoke 不自动授权生产训练或 promotion。`danger-full-access` 是当前执行权限设置，
`approval_policy=never` 是运行机制；二者都不能替代用户任务范围。历史“全放开”只在该会话授权范围
内生效，不能写成今后所有任务的永久授权。

| 身份 / 文件 | 当前用途 | 不能用于 |
| --- | --- | --- |
| `/etc/minio/training-data-s3.env` | 当前项目数据及 Runtime Release 发布的读写身份 | 冒充 MLflow 服务端账号或其他环境的授权 |
| `/etc/minio/ray-runtime-s3.env` | Head/Worker 下载 Runtime Package 的只读身份 | `PutObject`、发布或覆盖对象 |
| `/etc/minio/mlflow-s3.env` | MLflow 服务端代理 Artifact 读写 | 训练客户端、数据发布器或 Ray Job 凭据 |

发布进程显式使用获准的 publisher 文件，示例：

```bash
python job/publish.py \
  --env-file /etc/minio/training-data-s3.env \
  --output-dir /data/ai/chenzhangyue/galatea-model/platform-data/ray-cats-and-dogs-release
```

先构建可检查的包/清单或 dry run，核对数据来源、文件内容和目标 Prefix。授权已覆盖时直接发布；
确实缺授权时，只请求缺失的具体目标/动作，附上已准备的清单。记录文件 SHA-256、Release ID、
对象 URI/版本及无密钥的授权依据。发布器遇到同 Key 内容冲突应拒绝覆盖；数据使用新快照/版本。
新失败重试用新 Attempt，查询网络超时的同一次提交先核对原 Submission ID，避免重复训练。

缺少 publisher 文件、当前账号不能读取它或 `PutObject` 被拒绝时，报告缺少的文件/权限/Prefix；
不自动改用只读账号、MLflow 账号或 MinIO 管理员。若任务明确授权管理员配置修复，可在该范围内
修复策略后验证；不得公开 Bucket 来绕过权限。

Ray Runtime Environment Agent 在 Driver 启动前下载 ZIP/Wheel，读取凭据必须在 Head/Worker
执行 `ray start` 前注入。写进 Job 的 `runtime_env.env_vars` 太晚且会暴露长期凭据。
客户端上传/回读训练 Artifact 走 MLflow API，不读取数据库、MinIO 服务端文件或服务端密钥。
密钥只留在受保护文件/密钥系统，不打印，不复制到仓库、Notebook、日志、Release、聊天或回执。

## 已跑通的项目流程

项目：[`ray-cats-and-dogs`](../train-model/ray-cats-and-dogs/README.md)，PyTorch + Ray Train，
固定 Driver `scripts/train.py`。数据源为 `shaunthesheep/microsoft-catsvsdogs-dataset/versions/1`，
不是先前指定错误的 `tongpython/cat-and-dog`。固定本地数据入口为
`train-data/microsoft-cats-vs-dogs/PetImages`；25,000 张图中排除并记录 3 张损坏图，
train/validation/test 为 22,497 / 1,250 / 1,250。

1. 推断项目包管理器、环境锁和固定入口，做 `--check-config` / `--plan`；不在本地启动训练。
2. 核对数据版本、Manifest/Split 摘要与 profile；各节点预装同版本 Conda 环境并执行 `job/warmup.py`。
3. 用 publisher 发布不可变 Release。源码、配置或依赖变化后重新发布。
4. 按上文显式 export 目标；执行 `node job/governed.mjs plan ... - <attempt>`。
5. 在任务计算授权内执行 `node job/governed.mjs submit ... danger-full-access <attempt>`。
   由 Galatea 重算 Readiness、绑定授权并提交固定 Driver，不使用通用 `ray job submit` 替代训练入口。
6. 查询同一目标 Ray Jobs API 和 MLflow API；核对 terminal status、Run、metrics、Trace、Artifact 回读。
   Ray 成功仅证明执行完成，最终测试与模型提升仍各有边界。

MLflow 接入时先运行 `mlflow skills list`，读取已安装版本对应的 tracing/framework Skill，
再选择 API。入口设置 Tracking URI 和 Experiment，并调用 `mlflow.autolog(log_models=False, silent=True)`；
训练编排函数显式使用 `@mlflow.trace`。框架 autolog 不保证任意 PyTorch 训练自动产生 Trace，
也不替代项目自己的 epoch 指标回调。Driver/Train Controller 是父 Run 和共享 Artifact 的权威写入者。

本次完整 smoke 验收（开发集群，不包含 Champion/promotion）：

| 证据 | 结果 |
| --- | --- |
| Release | `35aa6749c10e7e31ea2c` |
| Ray Submission / Job | `ray-cats-and-dogs-smoke-5c09eaca38a7` / `10000000`，`SUCCEEDED`，exit `0` |
| Experiment | `ray-cats-and-dogs`，ID `2` |
| MLflow Run | `cc1e33b493674c30bb1f0a3a61ce409b`，`FINISHED`，94 项指标 |
| MLflow Trace | `tr-acdda569f9c055e996272e2fcc36464f`，`OK` |

这些 ID 是历史验证锚点，不能复制为下一次提交身份。可变机器回执位于仓库外的
`platform-data/ray-cats-and-dogs-evidence/`，只读计划与提交回执包含目标 profile/端点。

## 遇到这些症状时

| 症状 | 优先检查 / 处理 |
| --- | --- |
| Ray 日志有多集群发现或 cleanup actor 错误 | 对照 Jobs API、GCS、unit、节点和 GPU；同时核对服务级地址，避免只改提交 Shell |
| RuntimeEnvSetupError / GetObject AccessDenied | 查目标 Head/Worker 的 reader 身份、Prefix 权限和可达 S3 Endpoint；修复已授权服务配置后用新 Attempt 提交原 Release |
| 发布 PutObject AccessDenied | 核对 publisher 文件与目标 Prefix；不要用 `ray-runtime-reader` 发布 |
| Head 重启后 Agent/raylet 或 Job 日志 API 异常 | 检查目标 unit 的固定 Agent 端口、Worker 端口范围和进程组；不要清理整机 Ray |
| Conda 在非交互 Job 中卡住或等待条款确认 | 查看项目源/镜像配置和冷启动日志；在部署窗口预装并 warmup，各节点环境一致后再提交 |
| MLflow Models 页面没有训练指标 | 先查同 Experiment 的 Runs 及 Metric History；Models、Runs、Traces 是不同视图，没 Logged Model 不代表没训练指标 |
| MLflow Run 没指标 | 查 Driver 是否启动/失败、URI/Experiment 是否一致、epoch callback 是否触发；用 Run API 核对，不只看 UI |
| autolog 后没有 Trace | 查实际框架是否支持 tracing；按安装的 MLflow Skill 显式埋点并等待 Trace 导出，核对同 Experiment 的 Trace API |
| UI `Request error / INTERNAL_ERROR` | 保存失败 API 的 status/body，核对对应请求与 MLflow 服务日志；区分真正 500、Host/CORS/代理错误及前端筛选。未取得错误证据前不指定根因；仅放开 CORS 不保证修复 500 |
| 健康检查正常但 Artifact 失败 | 单独验证 MLflow Artifact 上传、下载和 SHA-256；`/health` 不能证明 S3 权限或代理链路 |
| 换机器后仍使用旧路径或 loopback | 核对真实路径、unit/venv shebang、所有节点可达端点和相同数据挂载；远端节点的 `127.0.0.1` 不是 Head |
| 数据集名称相近但下载错了 | 核对 canonical slug、固定 version、目录结构、样本数和 Manifest 摘要；更新来源后重新计算 split/plan/release |

## 后续更新方式

机器事实与操作判断分开更新：新机器验收后更新端点/目录/profile，并保存真实 API 回执；
新 case 只记录已观察症状、证据、原因和修复后的验证，尚未确认的原因保持为检查方向。
把跨项目判断接入 `.codex/skills/`，项目实现细节保留在项目 README。授权只在当前任务记录中
保留范围与依据，不把历史许可固化成默认授权，也不把凭据写进记忆。
