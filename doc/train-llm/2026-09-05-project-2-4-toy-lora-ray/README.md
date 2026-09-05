# 项目 2–4：Toy LoRA、可复现实验与 Ray Job 方案

> 状态：已实现代码与 TDD 验证；本目录保留方案、契约、运行手册和验收门。
>
> 当前边界：实现提供配置/schema/data/mask/LoRA/checkpoint/evaluation/MLflow/Ray 接口；本次交付只执行
> 不加载权重、不创建 Run 的契约检查，GPU 训练、Ray Job 和 test-once 仍需按运行手册明确启动。
>
> 上游状态：项目 0+1 的 Qwen3.5-0.8B 真实权重推理 Smoke 已完成。真实微信数据仍受
> consent ledger、人工审核和空 SFT 数据集阻断；项目 2–4 全部使用合成或公开数据。

## 1. 这组项目要解决什么问题

项目 2–4 是一条连续但可独立验收的 LLM 学习路线：

```text
项目 2：最小 Toy LoRA
    ├─ 合成数据与 chat template
    ├─ assistant-only loss mask
    ├─ adapter 保存/加载
    └─ checkpoint 生命周期
             │
项目 3：公平、可复现的比较
    ├─ 固定数据身份与 group split
    ├─ Base / Prompt-only / LoRA
    ├─ validation 选参，test 只评估一次
    └─ MLflow Artifact round-trip
             │
项目 4：单 GPU Ray Job
    ├─ 复用同一训练入口
    ├─ Driver/Worker MLflow 边界
    ├─ Job metadata 与 checkpoint 指针
    └─ 中断后的安全恢复
```

这三个项目只学习风格控制、实验治理和作业恢复，不是现有 `ray-cats-and-dogs` 等项目，
不模拟真实伴侣，不接入微信数据，
也不把任何模型注册为生产 Champion。

## 2. 文档导航

| 文档 | 用途 |
|---|---|
| [`design.md`](design.md) | 端到端架构、数据/训练/评估/追踪/恢复契约 |
| [`implementation-plan.md`](implementation-plan.md) | 按任务拆分的未来实现计划与测试接口 |
| [`runbook.md`](runbook.md) | 从只读检查到三阶段运行、比较和恢复的操作顺序 |
| [`acceptance-checklist.md`](acceptance-checklist.md) | 项目 2、3、4 的逐项验收门 |
| [`fine-tuning-evaluation-protocol.md`](../fine-tuning-evaluation-protocol.md) | 跨项目的开放式聊天四层评测、盲测与安全门禁 |
| [`schemas/sample.schema.json`](schemas/sample.schema.json) | 合成 SFT 样本契约 |
| [`schemas/run-manifest.schema.json`](schemas/run-manifest.schema.json) | Run 与工件身份契约 |
| [`schemas/job-metadata.schema.json`](schemas/job-metadata.schema.json) | Ray Job/attempt/checkpoint 关系契约 |

## 3. 未来代码落点

实现归属于一个模型项目：`train-model/llm-lora-playground/`。参数变体放在同一项目的
`configs/` 下，不为每个实验复制项目根。

```text
train-model/llm-lora-playground/
├── README.md
├── conda.yaml
├── pyproject.toml
├── galatea.project.yaml
├── configs/
│   ├── inference.yaml                 # 项目 0+1，已存在
│   ├── toy-lora-smoke.yaml             # 项目 2，10 steps
│   ├── toy-lora-baseline.yaml          # 项目 2，1 epoch
│   ├── reproducible-eval.yaml          # 项目 3，冻结比较协议
│   └── ray-job-smoke.yaml              # 项目 4，单 GPU 中断/恢复
├── scripts/
│   ├── generate_synthetic.py
│   ├── train_lora.py
│   ├── evaluate.py
│   ├── roundtrip_artifact.py
│   └── submit_train.py
├── job/
│   └── submit_train.py                 # 可选薄包装；不复制训练逻辑
├── src/llm_lora_playground/
│   ├── datasets.py
│   ├── sft.py
│   ├── lora.py
│   ├── checkpoints.py
│   ├── evaluation.py
│   ├── tracking.py
│   ├── ray_runtime.py
│   ├── recovery.py
│   └── job_metadata.py
└── tests/
    ├── test_synthetic_data.py
    ├── test_loss_mask.py
    ├── test_lora_roundtrip.py
    ├── test_checkpoint_metadata.py
    ├── test_split_integrity.py
    ├── test_evaluation_protocol.py
    ├── test_artifact_roundtrip.py
    ├── test_ray_metadata.py
    └── test_recovery.py
```

数据、adapter、checkpoint、模型缓存、执行 notebook 和密钥不进入该源代码目录，也不进入 Git。

## 4. 总体前置条件

进入项目 2 实现前，必须确认：

- 项目 0+1 的模型、tokenizer/chat template 和环境兼容性结果已留有可回读证据；
- `Qwen/Qwen3.5-0.8B` 的 immutable revision 已确定，训练环境能加载 BF16 `cuda:0`；
- `train-model/llm-lora-playground/` 的项目环境定义、测试入口和平台健康检查已明确；
- MLflow Tracking URI、Experiment 名称和 Artifact API 可由显式配置提供；
- 合成数据生成器不会读取真实微信目录，也不会把真实人物、对白、姓名或隐私字段作为模板；
- 资源预算固定为单 GPU、4 CPU、8 GiB memory，除非书面变更并产生新的 config digest。

任一前置条件不满足时，只做契约修复和只读诊断，不绕过检查启动训练。

## 5. 推荐顺序与停止点

1. 实现项目 2 骨架、配置、数据生成器、loss mask 和单元测试。
2. 仅执行 `--check-config`、schema 校验和少量数据生成检查；不启动模型训练。
3. 获得确认后执行 10-step Toy LoRA smoke。
4. Smoke 的数据、反向传播、adapter round-trip 和 checkpoint 门全部通过后，执行 1 epoch baseline。
5. 冻结项目 3 的数据、split、prompt、指标定义和测试集；再实现三组对照与 Artifact round-trip。
6. 项目 3 候选配置冻结并完成一次最终 test 后，才将同一训练函数包装为项目 4 Ray Job。
7. Ray 中断/恢复演练只使用 smoke 配置；不能把恢复成功误报为最终测试集证据。

任何阶段出现数据泄漏、assistant mask 错误、工件哈希不一致、恢复指针歧义或隐私/安全门失败，
停止进入下一阶段。

## 6. 时间与算力预算

| 阶段 | 主要工作 | 参考投入 | 资源 |
|---|---|---:|---|
| 项目 2 | 数据生成、loss mask、LoRA smoke、adapter round-trip | 0.5–1 天 | 单 GPU；smoke ≤10 分钟，baseline ≤30 分钟 |
| 项目 3 | 固定 split、三组对照、MLflow artifact 复现 | 1–2 天 | 单 GPU；数小时级比较 |
| 项目 4 | Ray Job 包装、metadata、checkpoint 中断恢复 | 0.5–1 天 | 单 GPU；smoke 级恢复演练 |

时间是学习和工程投入估计，不是训练时长保证。首次模型下载、环境修复、数据去重和服务故障
不应通过降低验收门来压缩；遇到阻断应记录原因并停在当前项目。

## 7. 预期运行状态

每个训练或评估动作都应能由以下身份重新定位：

```text
dataset_id + source/manifest sha256 + split digest
    + preprocessing version + model revision
    + canonical config digest + code revision
    + seed + execution mode + MLflow Run ID
```

成功的 adapter、checkpoint 和评估报告通过 MLflow Artifact API 保存并可下载校验；失败 Run 只保留
诊断和失败 checkpoint，不覆盖其他成功 Run，也不更新任何生产 alias。
