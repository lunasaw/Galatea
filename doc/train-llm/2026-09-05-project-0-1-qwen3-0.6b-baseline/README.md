# 项目 0+1：环境/GPU 基线与 Qwen3.5-0.8B 基础推理

> 状态：数据已交付；Qwen3.5-0.8B 模型下载/兼容性验证中。当前仍只维护设计与运行契约，不启动训练、不修改外部数据。
>
> 对应路线图：[`2026-09-05-ai-girlfriend-learning-roadmap-design.md`](../2026-09-05-ai-girlfriend-learning-roadmap-design.md)

## 目标

把路线图中的“项目 0：环境与 GPU 基线”和“项目 1：0.8B 基础推理”合并为一个可独立验收的第一阶段。完成后应能从干净环境重复加载 `Qwen/Qwen3.5-0.8B`，对固定的 20 条脱敏上下文生成回复，并获得可比较的 GPU、延迟、吞吐和显存基线。

本阶段只回答两个问题：

1. 当前机器和项目环境能否稳定执行 BF16 的 0.8B 推理？
2. 在固定数据、固定模板和固定生成配置下，模型的运行性能基线是什么？

它不回答“LoRA 是否提升角色风格”，也不把候选回复当成已批准训练数据。

## 与仓库 README 架构的对应关系

| README 架构部件 | 本阶段的落点 |
| --- | --- |
| JupyterLab | 只用于查看配置、结果和少量交互，不承载长期运行状态 |
| 参数化脚本 | 后续实现为 `train-model/llm-lora-playground/scripts/infer.py` |
| Ray | 20 条推理属于有界本地检查；脚本接口保留 Ray Job 包装能力，项目 4 再正式调度 |
| MLflow Tracking | 记录一次推理 Run 的参数、聚合指标、数据/代码/环境身份 |
| MLflow Artifact API | 保存运行清单、fixture 清单、指标报告和环境快照 |
| MinIO | 由 MLflow Artifact Store 承载；客户端不读服务端挂载目录 |
| Model Registry | 本阶段不注册、不更新任何 alias |

## 固定数据输入

本阶段固定使用已确认的最新预处理产物。数据由用户准备并放入当前主机的
`/data/ai/chenzhangyue/code/data`；实现只读该目录。正式运行前先执行数据交付预检，
预检失败时不加载模型、不创建 MLflow Run。

```text
数据 staging 根：/data/ai/chenzhangyue/code/data
数据集根：由 `WECHAT_DATA_ROOT` 覆盖；未设置时在 staging 根下解析
`output/wechat_aa807aaad90dc4463964`、`data-deal/output/wechat_aa807aaad90dc4463964`、
`wechat_aa807aaad90dc4463964` 或 staging 根本身。由于 staging 根同时包含多个历史数据集，
正式运行必须显式设置 `WECHAT_DATA_ROOT` 指向唯一的已解析数据集根。
解析出的数据集根会写入 Run Manifest。
dataset_id：wechat_aa807aaad90dc4463964
pipeline_version：wechat-preprocess-v1.2
source_sha256：63ce0e55db1dcefa69366db39bd752103edfb5ebf68aa1a2a10166ebc3891219
config_sha256：9a1d4a01e2926c9f6cd99e220609eb30c50a5d7582d45ccbfbd2cf3515fa3af1
```

数据目录只读使用。候选与切分状态如下：

- 295,202 条脱敏消息，4,505 个 session；
- 候选池 60,426 条，当前抽取 5,000 条；
- chronological session 切分为 train/validation/test = 1097/137/138 个 session；
- 候选计数为 3776/759/465；
- `datasets/train.jsonl`、`validation.jsonl`、`test.jsonl` 当前为空（这是正式 SFT 阻断，
  不影响本阶段从候选池构造推理 fixture）；
- 二次脱敏扫描和候选级泄漏检查通过；
- 人工审核尚未完成，正式 SFT 质量门禁仍为 blocked。

因此本阶段只从解析后的数据集根中的 `work/05_candidates/candidates.jsonl` 读取 validation
session，生成 20 条固定推理 fixture。每条 fixture 的最后一个 `assistant` 目标回复会被移除；
目标回复只作为受控的离线参考，不进入模型输入。

### 数据交付格式

请优先交付“已完成预处理、已脱敏”的数据集目录，而不是原始聊天导出。推荐布局如下：

```text
/data/ai/chenzhangyue/code/data/
└── data-deal/output/wechat_aa807aaad90dc4463964/
    ├── manifests/source_manifest.json
    ├── manifests/split_manifest.json
    ├── reports/privacy_report.json
    ├── reports/leakage_report.json
    ├── work/05_candidates/candidates.jsonl
    └── datasets/{train,validation,test}.jsonl
```

也可以直接将 `wechat_aa807aaad90dc4463964/` 放在 staging 根下，或通过
`WECHAT_DATA_ROOT` 指向它。不要把原始导出、身份映射、未脱敏中间文件或授权正文放入
Git；如果 staging 根同时包含原始数据和处理产物，必须显式设置 `WECHAT_DATA_ROOT`，
避免自动发现选错目录。

## 目录说明

```text
doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/
├── README.md
├── design.md
├── runbook.md
├── implementation-plan.md
├── configs/
│   ├── environment.yaml
│   ├── data.yaml
│   └── inference.yaml
├── schemas/
│   ├── run-manifest.schema.json
│   └── inference-record.schema.json
└── acceptance-checklist.md
```

这里是方案与运行契约目录，不是训练代码目录。实现阶段按仓库项目契约创建：

```text
train-model/llm-lora-playground/
├── README.md
├── conda.yaml
├── pyproject.toml
├── galatea.project.yaml
├── configs/
├── src/llm_lora_playground/
├── scripts/
├── tests/
└── notebooks/
```

数据、模型缓存、生成输出、运行状态和凭据不进入 Git。

## 验收摘要

全部满足以下条件才算项目 0+1 完成：

- GPU 张量计算、BF16 能力和 0.8B 模型加载通过；
- 2 条通用 prompt smoke 和 20 条固定脱敏上下文全部成功生成；
- 使用 tokenizer 的 `apply_chat_template`，不手拼特殊 token；
- thinking 模式关闭，seed、模板、模型 revision 和生成参数均进入 Run Manifest；
- 记录首 token 延迟、总延迟、tokens/s、峰值显存和解码错误数；
- MLflow Run/Artifact 可由 Run ID 回读，或服务不可用时明确阻断并留下本地诊断；
- 外部数据目录保持不变，已有 GPU 进程不被终止；
- 报告明确标注 `inference_baseline_only=true`；
- 正式训练仍受人工审核、授权流水线和空 SFT 数据集门禁阻断。

数据交付完成后，先运行 `runbook.md` 中的 `--check-data`。该步骤只读检查目录布局、
manifest、摘要 digest、split 计数、隐私/泄漏报告和授权引用；只有它通过，才进入模型 smoke。

详细架构见 [`design.md`](design.md)，逐步命令见 [`runbook.md`](runbook.md)，实施任务见 [`implementation-plan.md`](implementation-plan.md)。
