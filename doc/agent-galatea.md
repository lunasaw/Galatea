# Galatea：自主模型训练 Agent 的 Skill 架构

> 状态：设计草案；范围：多项目、多模型、多框架训练平台；当前参考实现：TensorFlow
> Cats vs Dogs Demo；平台组件：JupyterLab、Ray、MLflow、MinIO。

## 1. 背景与目标

Galatea 的目标不是为某一个 Demo 增加自动调参脚本，而是让 Agent 能够系统性地完成一个
模型训练项目：在数据集、任务定义、目标指标和资源约束已经明确后，生成合规训练代码，
通过 Ray 执行，使用 MLflow 记录完整证据，根据验证结果逐步改进，并最终交付满足明确
验收条件的候选模型。

Cats vs Dogs 只是当前可以执行和验证的参考工作负载，不是平台的架构边界。Galatea 必须
能够扩展到分类、回归、检测、分割、推荐、排序、预测、微调和其他任务，也不能把
TensorFlow、Accuracy 或单机 GPU 写死在共享能力中。

目标体验可以概括为：

```text
用户或上游系统确认数据集、任务、目标和预算
  ↓
Galatea 判断当前处于哪个训练阶段
  ↓
在写代码阶段加载 MLflow 和 Ray 规范 Skill
  ↓
生成可追踪、可调度、可恢复的训练代码
  ↓
执行、观察、诊断和迭代
  ↓
干净重训 Champion，并执行最终模型验收
```

Galatea 最终交付的不应只是一个模型文件，而应是一组可以复核的训练证据：

- MLflow Run ID 和模型 URI；
- 不可变的数据及切分身份；
- 完整训练配置、代码版本和环境信息；
- 训练与验证曲线；
- Checkpoint 和恢复信息；
- 最终评测报告和质量门禁结果；
- Ray Job、Trial 或 Train Run 的执行身份；
- Artifact 可回读和模型可加载的验证结果。

## 2. 关键认识：Skill 不是训练主循环

本设计最重要的边界是区分 Agent 主循环、Skill、确定性脚本和执行 Tool。

| 组件 | 职责 | 示例 |
| --- | --- | --- |
| Agent 主循环 | 识别当前阶段、选择能力、解释结果、决定下一步 | 判断现在应写代码、执行训练还是分析历史 |
| Skill | 给模型注入特定阶段必须遵守的工程规范和决策方法 | 如何编写 MLflow/Ray 合规训练代码 |
| 确定性脚本 | 稳定执行 API 检查、Schema 校验和合规审计 | 核对 MLflow API、审计 Run、审计 Ray 入口 |
| Tool | 对外部系统执行有副作用的动作 | 提交 Ray Job、停止任务、注册候选模型 |
| MLflow/Ray/MinIO | 保存外部事实和持久状态 | Run、Job、Checkpoint、模型和 Artifact |

Skill 不应把当前 `cats_dogs_tuner.py` 的循环包装成一个巨大的 Tool，也不应尝试替代未来
的 Agent 主循环。Skill 的核心价值是：当主循环进入一个相对固定的阶段时，向模型提供
足够精确的规范，使其生成的平台代码在第一次就具备正确的结构和边界。

例如，主循环已经确认：

```yaml
project: example-image-classifier
task: binary-classification
framework: tensorflow
dataset_version: sha256-...
objective:
  metric: val_f1
  mode: max
resources:
  cpu: 4
  gpu: 1
```

此时写代码的 Agent 不应重新讨论“应该做分类还是回归”，而应加载规范 Skill 并回答：

- 如何建立和结束 MLflow Run；
- 哪些参数、指标、数据身份和 Artifact 必须上报；
- 如何保证 Trial 不访问最终 Holdout；
- 应该使用 Ray Job、Ray Train 还是 Ray Tune；
- Driver、Worker 和 Rank 0 分别拥有什么职责；
- 如何声明资源、保存 Checkpoint 并支持幂等重试。

## 3. Galatea 的能力分层

Galatea 的第一版能力可以分成六个阶段。阶段之间存在顺序，但 Skill 本身不实现隐藏主循环。

```text
1. 项目接入
   mlflow-onboarding
        ↓
2. 官方 API 查询
   searching-mlflow-docs
        ↓
3. 训练代码生成
   write-mlflow-training-code
   write-ray-training-code
        ↓
4. 执行与恢复
   未来的 Ray Job / Train / Tune Tools
        ↓
5. 证据分析与优化
   mlflow-optimize-models
        ↓
6. Champion 重训与 SLA 验收
   后续的模型验收能力
```

主循环根据项目实际状态跳过不需要的阶段。成熟项目不应重复 onboarding；全新项目则必须
先完成基础接入，不能假定其已经具有和 Cats vs Dogs 相同的追踪能力。

## 4. 官方 MLflow Skills 的定位

MLflow 官方 Skills 位于同级仓库
[`mlflow-skills`](../../mlflow-skills/README.md)。当前本地版本的 Commit 为
`d95eabfe9758d8f94fa8b82cb14851ffa1451fcf`。

官方仓库主要面向 MLflow 官方 API、GenAI Tracing、Agent Evaluation 和调试。Galatea
应尽量复用这些上游能力，而不是自行维护一份容易过时的 MLflow 全量 API 手册。

### 4.1 `mlflow-onboarding`

官方 [`mlflow-onboarding`](../../mlflow-skills/mlflow-onboarding/SKILL.md) 对 Galatea 是
必要的入口能力，而不是因为 Cats vs Dogs 已经成熟就可以忽略的能力。

它负责：

- 判断项目是 GenAI/Agent 应用还是传统 ML/深度学习；
- 从代码识别 TensorFlow、PyTorch、scikit-learn 等框架；
- 判断项目是否已经接入 MLflow；
- 为新项目建立 Autolog 或基础手动 Tracking；
- 配置 Experiment 并验证是否产生 Run；
- 对已经接入的项目返回“无需重复 onboarding”。

它解决的是“任意新项目如何进入 MLflow 世界”，而不是完整的正式训练规范。对不同项目，
它应该产生类似以下能力状态：

```json
{
  "project_type": "traditional-ml",
  "framework": "tensorflow",
  "mlflow_status": "basic-integration",
  "tracking_uri_configured": true,
  "experiment_configured": true,
  "run_verified": true,
  "missing_capabilities": [
    "dataset-lineage",
    "checkpoint-recovery",
    "ray-run-ownership"
  ]
}
```

`mlflow-onboarding` 必要但不充分。它不会独立保证数据血缘、指标语义、分布式 Run 所有权、
Checkpoint 恢复、测试集隔离或发布门禁，这些由 Galatea 的平台规范 Skill 补充。

### 4.2 `searching-mlflow-docs`

官方 [`searching-mlflow-docs`](../.codex/skills/searching-mlflow-docs/SKILL.md) 已复制为当前
仓库的项目级 Skill。它负责从 MLflow 官方文档获取当前 API 和完整示例。

它回答：

```text
当前版本的 mlflow.start_run 有哪些参数？
mlflow.tensorflow.log_model 应如何记录 Signature 和 Input Example？
Dataset Inputs、Logged Models 和 Model Registry 当前怎样使用？
某个 API 在 MLflow 3.x 中是否已经变化？
```

Galatea 本地规范不重复保存所有 API 签名。本地 Skill 应保存“必须做什么”和“禁止做什么”，
需要精确调用方式时，先使用官方文档 Skill，再通过本机安装版本进行签名核对。

需要注意，官方 Skill 自身明确说明 `llms.txt` 目前主要覆盖 GenAI 文档，传统 ML Tracking
页面可能不完整。因此它是官方文档入口，但还不能被当作传统训练 API 的完整离线手册。
当索引找不到 TensorFlow、PyTorch、Dataset、Artifact 或 Registry 文档时，应继续限定在
MLflow 官方站点和已安装包的 Docstring/Signature 中核对，不能退回第三方博客或凭记忆
生成调用。后续可在不修改官方副本的前提下，为 Galatea 增加一个传统训练文档查询适配层。

当前项目固定 `mlflow==3.14.0`，因此正式生成代码时至少要同时确认：

1. 项目依赖中声明的 MLflow 版本；
2. 本机或 Ray Runtime Environment 中的实际版本；
3. 官方文档中的当前 API；
4. 生成代码使用的参数是否和目标运行环境一致。

### 4.3 其他官方 Skills

官方仓库中的 Tracing、Trace Analysis、Agent Evaluation 和 Agent Issue 修复能力，主要用于
Galatea 主循环本身，而不是 TensorFlow/PyTorch Epoch 指标。

| 官方 Skill | 对 Galatea 的后续用途 |
| --- | --- |
| `instrumenting-with-mlflow-tracing` | 记录主 Agent 的决策、Tool 调用和阶段耗时 |
| `retrieving-mlflow-traces` | 查询失败或缓慢的自主训练 Session |
| `analyze-mlflow-trace` | 分析某一步 Agent 决策或 Tool 调用错误 |
| `analyze-mlflow-chat-session` | 重建多轮自主训练过程 |
| `querying-mlflow-metrics` | 查询 Agent Trace 的 Token、延迟和错误率，不是训练 Run 指标 |
| `agent-evaluation` | 评估 Agent 的 Skill 选择、答案质量、成本和工具使用 |
| `fix-agent-issue` | 根据 Trace 建立回归测试并修复 Agent 行为 |
| `mlflow-agent` | 在相关子 Skill 全部可用后提供总路由 |
| `sagemaker-mlflow` | 仅适用于 SageMaker Managed MLflow，当前部署不使用 |

这些能力应在 Galatea 主循环开始实现后按需引入，不应为了传统模型训练而无差别安装全部
GenAI Skills。

## 5. 核心规范 Skill：`write-mlflow-training-code`

### 5.1 触发条件

当 Agent 正在创建、补全或评审正式训练代码，并且代码需要通过 MLflow 记录参数、指标、
数据、Checkpoint、模型或评测结果时，必须加载该 Skill。

它不是 MLflow 入门教程，也不是历史 Run 查询器。它是写代码阶段的强制工程规范。

### 5.2 输入前提

调用前，上游应尽量提供：

- 项目 ID 和任务类型；
- 使用的框架和模型族；
- Tracking URI 与 Experiment 配置来源；
- 数据集、切分和预处理身份；
- 主验证指标与 `max/min` 方向；
- 最终 Holdout 指标和质量门禁；
- 当前执行模式：Smoke、Trial、Champion 或 Evaluation；
- 是否运行在 Ray Job、Ray Train 或 Ray Tune 中。

缺少这些内容时，Skill 可以生成明确的未决项，但不能擅自将测试指标作为优化目标，或为
模糊的自定义指标猜测方向。

### 5.3 Tracking 与 Experiment 规范

生成代码必须：

- 从 `MLFLOW_TRACKING_URI` 或等价显式配置读取 Tracking URI；
- 从 `MLFLOW_EXPERIMENT_NAME`、Experiment ID 或项目配置读取 Experiment；
- 支持远程 Tracking Server，不能假定训练节点可以访问后端数据库；
- 不直接打开、复制或查询 `mlflow.db`；
- 不在训练客户端读取 MLflow Server 的 MinIO 长期凭据；
- 通过 Tracking Server 和 Artifact API 访问 Artifact；
- 在训练开始前验证 Tracking Server 和 Artifact Store 可用性；
- 对无法建立可追踪 Run 的正式训练快速失败。

框架 Autolog 可以作为基础采集能力，但不能被视为完整训练契约。Skill 应先确认目标框架和
MLflow 版本是否受支持，再决定启用 Autolog，并显式补充数据身份、自定义指标、执行角色、
恢复信息和质量门禁。还应避免 Autolog 与手动模型记录重复生成模型和 Artifact。

### 5.4 Run 生命周期规范

每个正式 Run 必须有明确角色：

```text
smoke       验证代码、数据、追踪和恢复路径
baseline    建立可比较基线
trial       使用训练与验证证据，不访问最终 Holdout
champion    以选定配置从干净状态重训
evaluation  对固定 Champion 执行最终评测
```

Run 至少要记录：

- `project`、`task`、`model_family` 和 `run_role`；
- 唯一的 Trial 或执行身份；
- 数据、切分、预处理和代码摘要；
- 执行主机、Ray 身份和资源声明；
- `run.outcome`、失败类型和失败阶段；
- Parent/Child 或 Study/Trial 关系；
- 测试集是否被访问。

只有权威进程可以创建和结束共享 Run。分布式 Worker 不得各自无序创建 Run，并同时写入
同一个共享 Artifact 路径。

### 5.5 参数规范

必须上报训练真正使用的“解析后配置”，而不是只记录用户传入的部分值。至少包括：

- 模型结构、预训练权重和冻结策略；
- Optimizer、Learning Rate、Scheduler、Loss；
- Batch Size、Epoch/Step 上限和 Early Stopping；
- 数据增强、采样、类别权重和阈值；
- 随机种子和确定性配置；
- 分布式并行参数；
- CPU、GPU 和内存资源；
- 所有被搜索或条件启用的字段。

参数名应分层，例如：

```text
model.architecture
training.optimizer
training.learning_rate
training.seed
data.content_sha256
data.split_sha256
preprocessing.version
execution.num_gpus
```

Skill 不应硬编码 Accuracy、图像分类或 TensorFlow 字段。每个项目通过契约声明指标和
额外参数。

### 5.6 数据血缘规范

模型结果必须能追溯到不可变数据身份。代码至少记录：

- 数据源 URI；
- 数据集版本；
- 内容或 Manifest 摘要；
- 训练、验证和最终测试切分摘要；
- 样本数量与类别/目标分布摘要；
- 预处理、Tokenizer、Feature Schema 或增强版本；
- MLflow Dataset Inputs 及其 `training`、`validation`、`test` 上下文。

不得只记录一个可变化的本地目录路径，也不得在数据内容不一致时伪造 MinIO 来源 URI。

### 5.7 指标语义规范

指标必须区分数据角色和用途：

```text
train/*       训练状态和优化诊断
validation/*  选参、Early Stopping 和 Champion 选择
test/*        最终描述性评测
system/*      资源利用率和运行成本
```

每个项目必须声明一个主验证目标及其方向。Trial 只能使用训练与验证证据。最终 Holdout
不能用于 Trial 选择、Early Stopping 或重复搜索。

训练代码应按 Epoch 或 Step 记录 Metric History，而不是只记录最终标量。必须记录足够的
曲线，使后续能力能够诊断欠拟合、过拟合、截断、震荡和资源浪费。

### 5.8 Artifact 与模型规范

根据项目类型记录：

- 解析后的训练配置；
- 数据 Manifest 和 Profile；
- 训练曲线和评测报告；
- 最佳与最近 Checkpoint；
- 恢复元数据；
- 预测结果或安全的汇总；
- 模型 Signature、Input Example 和依赖；
- 源码或不可变代码包；
- 环境与硬件摘要。

Artifact 上传成功并不等于可恢复。正式 Run 应通过 MLflow Artifact API 回读验证关键对象，
Champion 还应重新加载模型并完成最小预测测试。

敏感样本、测试标签、密钥和内部 Endpoint 不能写入日志、公开 Artifact 或模型元数据。

### 5.9 失败与恢复规范

异常路径也必须留下可诊断证据：

- `failure.type`；
- `failure.phase`；
- 已完成的 Epoch/Step；
- 最近 Checkpoint URI；
- Ray Job/Trial ID 和 Attempt；
- 是否允许重试及其幂等键。

失败 Run 不得伪装成成功 Run。重试应创建新的 Attempt 或安全恢复原任务，不能覆盖其他
Run、Checkpoint 或模型。

### 5.10 Registry 与发布规范

探索性 Trial 不得自动改变生产模型。Skill 可以生成：

- Logged Model；
- 候选模型版本；
- 模型卡和验收报告；
- Candidate 注册建议。

修改生产 Alias 必须是单独、显式并经过审批的动作，不能作为训练脚本正常结束时的副作用。

### 5.11 Skill 目录建议

```text
.codex/skills/write-mlflow-training-code/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── training-run-contract.md
│   ├── dataset-lineage.md
│   ├── metric-semantics.md
│   ├── artifact-and-model.md
│   ├── ray-integration.md
│   └── tensorflow.md
└── scripts/
    ├── inspect_mlflow_api.py
    └── audit_training_run.py
```

`inspect_mlflow_api.py` 负责核对当前安装版本和关键 API 签名；`audit_training_run.py` 负责
通过 Tracking/Artifact API 审计实际 Smoke Run。静态检查只能辅助，不能代替对真实 Run
的验证。

## 6. 核心规范 Skill：`write-ray-training-code`

### 6.1 触发条件

当 Agent 将已经确定的数据、模型和训练函数编写为 Ray Job、Ray Task、Ray Train 或
Ray Tune 工作负载时，必须加载该 Skill。

它负责执行结构、资源、并发、失败、恢复和 MLflow 所有权，不负责决定任务本身应该采用
哪个模型。

### 6.2 选择正确的 Ray 抽象

| 工作负载 | 推荐抽象 |
| --- | --- |
| 单机单卡或单进程长训练 | Ray Job 提交参数化训练脚本 |
| 多 Worker 分布式训练 | Ray Train |
| 多组超参数受控试验 | Ray Tune 或显式 Trial Jobs |
| 简短独立计算 | Ray Task |
| 数据规模较大的分布式读取和转换 | Ray Data |
| 探索和可视化 | Notebook，不作为正式训练入口 |

不要为了“使用 Ray”而把所有函数装饰为 `@ray.remote`。应根据失败边界、资源需求、恢复
方式和并行粒度选择抽象。

### 6.3 正式入口规范

正式训练必须提供非 Notebook、参数化入口，例如：

```bash
python scripts/train.py --config configs/train.yaml
```

入口必须：

- 解析完整配置并在启动时验证；
- 显式接收数据、目标、资源和追踪配置；
- 输出或记录 Ray Job ID 和 MLflow Run ID；
- 支持 Smoke、Trial 和 Champion 角色；
- 正确处理终止信号；
- 将恢复所需信息写入持久化系统；
- 不依赖 Notebook Kernel 内存状态。

### 6.4 资源规范

Ray 代码必须显式声明：

- CPU 数量；
- GPU 数量；
- 内存预算；
- Worker 数量；
- Placement 或并发约束；
- 数据加载 Worker 和训练 Worker 的资源关系。

逻辑资源必须和物理硬件核对。不能因为 Ray 集群声明了多个 `GPU` 资源，就假定主机真的
存在同样数量的物理 GPU。并行搜索必须限制最大并发，防止多个 Trial 抢占同一设备并 OOM。

### 6.5 配置传播与确定性

所有 Worker 必须接收同一份已解析配置和不可变身份，至少包括：

- 数据及切分摘要；
- 模型和训练参数；
- 随机种子；
- Trial ID；
- 代码和环境摘要；
- MLflow Experiment 和 Run 关系；
- Checkpoint 与恢复策略。

分布式采样、数据分片和随机数必须按框架要求初始化。无法完全确定的操作应被显式记录，
而不是声称训练完全可复现。

### 6.6 幂等、重试和恢复

每个工作负载需要稳定的幂等键，例如：

```text
project + dataset_digest + split_digest + code_digest + trial_signature + seed + role
```

重试必须满足：

- 不重复执行已成功的相同 Trial；
- 不覆盖其他 Attempt 的 Run 和 Artifact；
- 能从持久 Checkpoint 恢复；
- 能区分应用错误、节点错误、资源不足和人为停止；
- 达到最大 Attempt 或预算后可靠停止；
- 将失败原因同时关联到 Ray 和 MLflow 身份。

Checkpoint 不能只存在于 Worker 的临时目录。Ray Checkpoint 和 MLflow Artifact 之间应有
可解析的关联，客户端不应绕过 API 读取服务端 MinIO 文件系统。

### 6.7 分布式 MLflow 所有权

Ray 与 MLflow 组合时最容易产生的错误，是多个 Worker 无序创建、结束或共同写入一个
Run。必须为每种执行模式定义权威写入者。

#### 单个 Ray Job

```text
Ray Job entrypoint
  └── 一个 MLflow Run
      ├── 训练指标
      ├── Checkpoint
      └── 模型和报告
```

#### Ray Train 多 Worker

```text
Ray Train Driver / Rank 0
  └── 创建和结束共享 MLflow Run
      ├── Rank 0 上报全局指标与 Artifact
      └── 其他 Worker 只执行计算或上报给 Rank 0
```

如果使用每 Worker 独立 Run，必须显式记录 Parent/Child 关系，并证明不会发布不完整的局部
模型。默认应优先单一权威 Run。

#### Ray Tune

```text
可选 Study Parent Run
  ├── Trial A → 独立 MLflow Run
  ├── Trial B → 独立 MLflow Run
  └── Trial C → 独立 MLflow Run
```

每个 Trial Run 只使用训练与验证集。选定配置后应另建 Champion Run，从干净状态重训并
执行最终评测。

Ray 与 MLflow 至少应互相记录：

```text
ray.job_id
ray.task_or_trial_id
ray.attempt
ray.worker_rank
execution.resources
mlflow.run_id
checkpoint.uri
```

### 6.8 Skill 目录建议

```text
.codex/skills/write-ray-training-code/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── workload-selection.md
│   ├── resource-contract.md
│   ├── retry-and-idempotency.md
│   ├── checkpoint-and-recovery.md
│   ├── distributed-training.md
│   ├── mlflow-run-ownership.md
│   └── tensorflow.md
└── scripts/
    ├── inspect_ray_api.py
    └── audit_ray_entrypoint.py
```

`inspect_ray_api.py` 用于核对当前 Ray 版本和关键 API；`audit_ray_entrypoint.py` 对生成代码
执行静态与配置审计。真正的恢复能力还必须通过受控失败测试验证。

## 7. 规范 Skill 与确定性脚本的边界

规范写在 `SKILL.md` 和 References 中，重复且脆弱的检查写进脚本。

### 7.1 适合写进 Skill 的内容

- 哪个阶段必须加载该能力；
- 必须满足的工程不变量；
- 如何根据执行模式选择代码模式；
- 哪些操作禁止；
- 什么时候需要读取框架专用 Reference；
- 验证顺序和完成条件。

### 7.2 适合写进脚本的内容

- 获取已安装 MLflow/Ray 版本；
- 输出关键 API 的真实签名；
- 校验项目契约和配置 Schema；
- 通过 MLflow API 检查 Run 字段；
- 检查 Artifact 是否存在并可下载；
- 校验 Ray 资源声明和入口参数；
- 输出机器可读的合规报告。

### 7.3 不适合写进脚本的内容

- 用大量固定 `if/else` 代替对模型代码的理解；
- 硬编码 Cats vs Dogs、Accuracy 或 TensorFlow；
- 在审计脚本中隐藏训练主循环；
- 未经授权自动提交 GPU 任务；
- 未经审批自动注册或提升生产模型。

脚本统一使用结构化 JSON 输出，预期的领域结论应作为正常结果，而不是异常退出：

```json
{
  "schema_version": "galatea-compliance/v1",
  "capability": "audit-training-run",
  "status": "non-compliant",
  "findings": [
    {
      "code": "MISSING_SPLIT_DIGEST",
      "severity": "error",
      "evidence": "Run parameter data.split_sha256 is absent"
    }
  ],
  "next_actions": [
    "apply-write-mlflow-training-code"
  ]
}
```

## 8. 主 Agent 中的 Skill 选择

未来主循环应依据阶段和证据选择 Skill，而不是每次加载所有内容。

| 当前状态 | 应加载的 Skill |
| --- | --- |
| 新项目，MLflow 状态未知 | `mlflow-onboarding` |
| Agent 不确定某个 MLflow API | `searching-mlflow-docs` |
| 正在创建或修改训练追踪代码 | `write-mlflow-training-code` |
| 正在创建 Ray 正式训练入口 | `write-ray-training-code` |
| 已有 Runs，需要分析和制定优化动作 | `mlflow-optimize-models` |
| 正在实现 Galatea 自身的可观测性 | `instrumenting-with-mlflow-tracing` |
| 正在评估 Galatea 的 Tool/Skill 选择能力 | `agent-evaluation` |

典型的新项目路径：

```text
主循环收到已经确认的数据集、任务和目标
  ↓
mlflow-onboarding 检查基础接入
  ↓
searching-mlflow-docs 获取目标版本 API
  ↓
write-mlflow-training-code 生成追踪与模型上报代码
  ↓
write-ray-training-code 生成正式执行入口
  ↓
确定性脚本审计代码和 Smoke Run
  ↓
执行 Tool 提交正式训练
```

典型的成熟项目路径：

```text
主循环发现项目已有合规训练入口和可比较 Runs
  ↓
跳过 onboarding 和代码生成
  ↓
mlflow-optimize-models 分析证据
  ↓
根据诊断修改配置或最小范围代码
```

## 9. Cats vs Dogs Demo 的正确定位

[`train-model/cats-and-dogs/`](../train-model/cats-and-dogs/README.md) 是第一套 TensorFlow
验收样例。它可以验证规范 Skill 是否能识别和生成：

- 数据内容摘要和确定性切分摘要；
- MLflow Dataset Inputs；
- 参数、Epoch 指标和系统指标；
- Trial 与 Champion 的测试集隔离；
- Checkpoint、模型、Signature 和 Input Example；
- Artifact API 回读；
- 失败阶段和质量门禁。

它也暴露了规范 Skill 应识别的改进空间，例如正式 Ray Job/Train 入口、Ray 与 MLflow 身份
关联、跨进程恢复和更加独立的最终 Holdout。

测试 Skill 时不能只验证它能复现 Cats vs Dogs 现有实现。还应至少使用一个不同任务的
Fixture，例如：

- scikit-learn 回归；
- PyTorch 多类分类；
- 自定义最小化 Loss 的任务；
- Ray Train 多 Worker 示例。

如果 Skill 只能在指标名为 `best_val_accuracy`、目录名为 `cats-and-dogs` 或框架为
TensorFlow 时工作，就没有达到平台级抽象目标。

## 10. 自主优化与模型 SLA

Galatea 可以在预算内逐步改进模型，但不能承诺数学意义上的全局最优。正确表述是：

> 在固定数据、评估协议、搜索空间和资源预算内，获得当前证据支持的最佳候选，并判断其
> 是否通过已冻结的模型验收条件。

主循环的合理终态是：

```text
candidate-checks-passed
sla-not-met-within-budget
insufficient-evidence
blocked-by-platform-or-data
```

Agent 不得为了获得 `passed` 而修改质量阈值。Trial 使用训练和验证证据；选定配置后从
干净状态重训 Champion，再执行正式 Holdout 评测。生产 Alias 变化仍需显式人工审批。

SLA 可以包含：

- 主质量指标及方向；
- 次要质量和切片门禁；
- 推理延迟与硬件协议；
- 最大 Trial、GPU 小时和完成时限；
- Artifact 可恢复性；
- 模型重新加载和预测一致性；
- 数据、代码和环境完整性。

## 11. 安全与治理边界

Galatea 及其 Skill 必须保持以下不变量：

- 不在日志、Artifact 或代码中写入 Token、密码和对象存储密钥；
- 训练客户端通过 MLflow API 访问 MinIO Artifact，不直接读取服务端存储目录；
- 不使用最终测试集反复选参；
- 不比较数据、切分、预处理或评估协议不兼容的 Run；
- 不让非权威 Worker 发布共享模型；
- 不让重试覆盖其他 Run 或 Checkpoint；
- 不自动修改生产模型 Alias；
- 不把 Notebook Kernel 或 `/tmp` 当作持久恢复状态；
- 不因为用户请求代码分析就自动启动昂贵训练；
- 不把有限搜索结果描述为全局最优。

## 12. 第一版实施计划

### 阶段一：上游官方能力

1. 保留已复制的 `searching-mlflow-docs` 项目级 Skill。
2. 引入官方 `mlflow-onboarding` 作为新项目入口能力。
3. 记录上游 Commit，后续更新时按差异审查，不直接修改官方副本。

### 阶段二：MLflow 训练代码规范

1. 创建 `write-mlflow-training-code`。
2. 定义框架无关的 Run、数据、指标和 Artifact 契约。
3. 增加 TensorFlow Reference。
4. 实现 MLflow API 签名检查脚本。
5. 实现真实 Run 合规审计脚本。
6. 使用 Cats vs Dogs 和不同指标名 Fixture 验证。

### 阶段三：Ray 训练代码规范

1. 创建 `write-ray-training-code`。
2. 定义 Job、Train、Tune 和 Task 选择规则。
3. 定义资源、幂等、重试、Checkpoint 和恢复契约。
4. 定义 Ray/MLflow 权威写入模式。
5. 实现入口和资源审计脚本。
6. 使用单 Job 与多 Worker Fixture 验证。

### 阶段四：主循环和执行 Tools

在规范 Skill 稳定后，再实现：

- Ray Job 提交、查询、日志和停止 Tool；
- 训练预算和超时控制；
- Agent Session 状态与恢复；
- MLflow Tracing 和 Agent Evaluation；
- Champion 重训、SLA 验收和候选注册。

这个顺序能避免先写一个会持续生成不合规训练代码的自主循环。

## 13. 第一版验收标准

规范 Skill 第一版至少应通过以下验收：

1. 面对全新 TensorFlow 项目，Agent 能先完成 MLflow 基础接入，再补齐正式追踪契约。
2. 面对已有 `autolog()` 的项目，Agent 不重复重写，而是识别缺失的数据血缘和恢复信息。
3. Agent 不靠记忆猜测 MLflow API，能查询官方文档并核对目标版本。
4. 生成的 Trial 代码不读取最终 Holdout。
5. 生成的 Ray 代码显式声明资源并限制并发。
6. Ray Train 多 Worker 代码只有权威 Worker 完成共享 MLflow 上报。
7. 每个 Trial、Attempt、Ray Job 和 MLflow Run 都能互相追踪。
8. 训练失败后能定位失败阶段和持久 Checkpoint。
9. 合规审计脚本能发现缺失的数据摘要、指标历史、Artifact 和资源声明。
10. Skill 在回归、最小化目标和非 TensorFlow Fixture 上不依赖 Cats vs Dogs 语义。

## 14. 当前结论

Galatea 的第一版亮点不应定义为“Agent 能调用当前 Demo 的自动调优器”，而应定义为：

> Agent 在面对任意已确认的数据集和建模任务时，能够加载官方知识和本地工程规范，生成
> 符合 MLflow、Ray、MinIO 和模型治理要求的训练代码，并让后续执行、分析、恢复和验收
> 都建立在完整、可比较、可审计的证据之上。

官方 MLflow Skills 解决 API 知识、基础接入以及未来 Agent 的 Tracing/Evaluation；Galatea
本地 Skills 解决本平台正式模型训练的强制代码规范。两者组合，而不是重复建设，才构成
可持续的自主训练能力基础。
