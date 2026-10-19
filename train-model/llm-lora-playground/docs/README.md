# 项目 0–4：llm-lora-playground 合并实施指南

> 本文是项目 0–4 的主导航，合并了原先的项目 0+1 README/design/runbook/checklist 与项目 2–4
> README/design/runbook/checklist 的共同流程。旧的阶段文档已经合并并删除，历史变化通过 Git 追溯。
> 跨项目治理规则以
> [总指南](../../../doc/train-llm/2026-09-10-governed-llm-finetuning-complete-guide.md)、项目契约和代码测试为准。

## 1. 范围和结果

项目 0–4 用合成数据或其他明确获准的非敏感数据，建立一条可迁移的 LLM 训练能力链：

```text
推理兼容性
  -> assistant-only SFT/LoRA
  -> 固定 split 和公平比较
  -> MLflow/Artifact round-trip
  -> immutable Release + Galatea + Ray Job
  -> 失败重试和 checkpoint 恢复证据
```

代码落点是 [`train-model/llm-lora-playground/`](../)。本阶段不把
任何结果自动注册为生产模型，也不把未审核的真实聊天数据升级为训练证据。

## 2. 阶段合并视图

| 阶段 | 目标 | 允许的输入 | 主要产物 | 成功后的下一步 |
| --- | --- | --- | --- | --- |
| 0+1 推理基线 | 验证模型、环境、GPU、chat template 和性能 | 固定脱敏 validation fixture | inference manifest、延迟/吞吐/显存报告 | Toy SFT/LoRA |
| 2 Toy LoRA | 验证数据格式、assistant-only mask、adapter 和 checkpoint | 合成 scenario-group 数据 | adapter、checkpoint manifest、loss 和 fresh-load 证据 | 1 epoch baseline |
| 3 可复现实验 | 验证 Base/Prompt-only/LoRA 可公平比较 | 冻结 train/validation/test split | protocol、ranking、validation evidence、test-once 记录 | Ray 调度 |
| 4 Ray Job | 验证固定 Driver、资源、MLflow owner 和恢复 | 与项目 2/3 相同的 workload 代码 | Release、Plan、Job metadata、MLflow Run、Artifact round-trip | 后续真实项目复用 |

阶段不能跳跃。2-sample/2-step 需要真正更新参数或产生 checkpoint 时，它本身就是 governed Training
Run；纯 forward-only fixture 才能留在本地组件测试。

## 3. 项目契约和配置映射

项目契约见 [`galatea.project.yaml`](../galatea.project.yaml)。配置变体位于 [`configs/`](../configs/)：

| 配置 | 角色 | 说明 |
| --- | --- | --- |
| `toy-lora-smoke.yaml` | smoke | 合成数据、10 steps、验证训练边界 |
| `toy-lora-baseline.yaml` | baseline/trial | 合成数据、1 epoch、建立可比较基线 |
| `reproducible-eval.yaml` | evaluation/trial | 冻结评测协议和 validation-only 选择 |
| `ray-job-smoke.yaml` | smoke | 单 GPU Ray Job 和尾部 Artifact 验证 |
| `wechat-owner-bulk-approved-5k.yaml` | private trial | 明确 non-promotable；不代表 formal training eligible |

每份 config 都必须显式声明：

```text
run role/promotable
model architecture/revision/dtype/device
dataset content digest/split digest/preprocessing
assistant-only loss、packing、sequence length
LoRA method parameters
optimizer、scheduler、batch、accumulation、seed、steps/epochs
objective metric/direction、evaluation protocol、test access
resources、tracking、output root
```

## 4. 一条标准执行顺序

### 4.1 只读检查

```bash
export PYTHONPATH="$PWD/train-model/llm-lora-playground/src"

python train-model/llm-lora-playground/scripts/train_lora.py \
  --config train-model/llm-lora-playground/configs/toy-lora-smoke.yaml \
  --check-config

python train-model/llm-lora-playground/scripts/generate_synthetic.py \
  --output-dir platform-data/llm-baselines/toy-lora/v1 \
  --count 400 --seed 42 --check-only
```

检查必须不创建 MLflow Run、checkpoint 或持久训练证据。先通过 schema、split、mask、target module、
秘密键和环境版本检查，再进入受控执行。

### 4.2 生成数据和计划

合成数据生成器必须记录 generator version、seed、schema、scenario 分组、样本数和内容 digest。
正式计划通过项目脚本的 `--plan` 生成；计划至少包含：

- dataset/split/preprocessing identity；
- model/tokenizer revision 和 environment digest；
- objective、role、test access、资源和预计尾部时间；
- execution backend、idempotency key 和 output root；
- integrity/preprocessing parity/migration contamination 报告。

### 4.3 Release、Galatea 和 Ray

实际训练不使用 `python train_lora.py --run`、通用 `ray job submit` 或 notebook。固定链路是：

```text
clean worktree
  -> build_release.py
  -> 管理员登记 project/release/campaign/资源
  -> Galatea plan_run/readiness
  -> 同一 plan_id submit_job
  -> Ray Job fixed Driver
  -> Driver-owned MLflow Run
```

Driver 必须拒绝缺少 release、readiness、execution identity、submission identity、role、attempt 或
promotability 的调用；worker 不单独创建父 Run 或发布共享 Artifact。

### 4.4 训练、验证和 Artifact

训练实现必须：

1. 使用 tokenizer 的 `apply_chat_template`，不得手拼特殊 token。
2. 只对目标 assistant token 计算 loss；system/user/history/padding/truncated span 为 `-100`。
3. 启动时验证 LoRA target modules 存在，不能静默跳过。
4. 在唯一 `run_id/attempt_id/step-N/` 下写 checkpoint，完成后才更新指针。
5. 复用 Trainer 的权威 validation metric，避免重复 validation 造成漂移。
6. 通过 MLflow Artifact API 上传、下载、SHA-256 校验和新进程加载。
7. 所有尾部操作完成后才把 Run/Job 标为成功。

建议 Artifact：

```text
manifests/run_manifest.json
reports/validation_quality.json
reports/evidence.json
model/adapter_config.json
model/adapter_model.safetensors
checkpoints/<attempt>/step-<N>/checkpoint_manifest.json
checkpoints/<attempt>/step-<N>/trainer_state.pt
```

## 5. 评测和候选生命周期

比较时固定：dataset/split、prompt、chat template、thinking 开关、generation config、seed、长度、
硬件和评估协议。最小矩阵是：

```text
Base          = base model + base prompt
Prompt-only   = base model + frozen optimized prompt
LoRA          = same base model + same optimized prompt + adapter
```

Trial 只用 train/validation 选择 checkpoint、超参数和 prompt。candidate freeze 至少绑定 Run ID、
validation evidence digest、config/release/split/protocol digest、checkpoint digest 和 selection rule。
只有冻结候选后，Champion/evaluate 才能原子 claim test 并执行一次；测试结果不自动触发 promotion。

## 6. 失败、重试和恢复

| 失败 | 处理 |
| --- | --- |
| 模型/Transformers 不兼容 | 停在 preflight，修复环境并产生新 Release |
| assistant mask 或 target module 错误 | 停止训练，修代码/测试，不以全序列 loss 代替 |
| 训练完成但尾部超时 | 保留 FAILED Run，重新估算 validation/Artifact/cleanup 预算 |
| Artifact basename/hash 不一致 | Run failed，修路径/服务，重新 plan/attempt |
| submit 网络超时 | 先用原 idempotency key 查询，不盲目重复提交 |
| Job 中断 | 仅从 complete checkpoint 恢复；新 attempt/new Run，记录 `retry_of`/`resumed_from` |
| test 提前访问 | 作废 test evidence，重新冻结候选和 test-once claim |

失败 Run、Operation、日志和不完整 checkpoint 都是审计证据，不得覆盖或事后改成成功。

## 7. 验收定义

- check/plan 是只读的；训练只能由声明的 Ray Driver 触发。
- 67 项项目测试在声明的 `ray-llm-py312` 环境下通过（当前验证结果）。
- run manifest 可关联 dataset、split、model、tokenizer、config、code、environment、seed、resource、
  Release、Plan、Operation、Submission、Run 和 Attempt。
- Ray/MLflow/Galatea/Artifact 终态一致，fresh-process load 和 SHA-256 round-trip 通过。
- test 在 baseline/Trial 阶段保持 untouched；没有 Registry alias 变更。

## 8. 相关文档

- [跨项目完整指南](../../../doc/train-llm/2026-09-10-governed-llm-finetuning-complete-guide.md)
- [开放式聊天微调评测协议](../../../doc/train-llm/fine-tuning-evaluation-protocol.md)
- [项目根 README](../README.md)
