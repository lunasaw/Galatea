# 项目 2–4 运行手册

本手册是已实现代码的标准运行顺序。实验、Trial 和 Champion 必须使用同一架构；区别只能是数据身份、
角色、授权、最终测试访问和可晋级状态。`scripts/train_lora.py` 只允许配置检查和只读计划，所有实际
GPU 训练必须经过 `immutable release -> Galatea plan -> Galatea authorization -> Ray Job ->
Driver-owned MLflow Run -> Artifact API round-trip`。任何本地训练或直接 Ray 提交都不能形成规范证据。
Training Run 分类和跨项目证据要求以
[`governed-training-workflow`](../../../.codex/skills/governed-training-workflow/SKILL.md) 为准；
`experimental_only`、私有脱敏、owner approval 和不可晋级都不是执行后端例外。

## 0. 变量与目录

```bash
cd /data/ai/chenzhangyue/code/galatea

export LLM_PROJECT_ROOT="$PWD/train-model/llm-lora-playground"
export LLM_TOY_DATA_ROOT="$PWD/platform-data/llm-baselines/toy-lora"
export QWEN35_MODEL_PATH=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=llm-lora-playground
```

合成数据、adapter、checkpoint、metadata 和评估报告写入 `platform-data/llm-baselines/`；
不得写入项目源码目录。真实微信数据路径不作为项目 2–4 的输入。

## 1. 环境和平台只读检查

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
python -m pip check

systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
nvidia-smi -L
```

正式训练使用项目自己的 `conda.yaml` 或等价环境；共享环境仅可用于 schema、配置和不加载训练权重
的兼容性检查。GPU、BF16、Transformers `qwen3_5` 支持或 MLflow/MinIO 不满足时，保持 blocked。
不杀进程、不 reset GPU、不清理其他任务的显存。

## 2. 项目 2：只读契约检查（不训练）

### 2.1 配置检查

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-smoke.yaml" \
  --check-config
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-baseline.yaml" \
  --check-config
```

预期：模型、dtype/device、single-GPU resources、assistant-only loss、LoRA target modules、seed、
objective metric/mode、输出根和凭据检查通过；不创建 MLflow Run、不加载完整模型。

### 2.2 schema 和生成器检查

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/check" \
  --count 8 --seed 42 --check-only
python -m pytest "$LLM_PROJECT_ROOT/tests/test_synthetic_data.py" \
  "$LLM_PROJECT_ROOT/tests/test_loss_mask.py" \
  "$LLM_PROJECT_ROOT/tests/test_lora_roundtrip.py" \
  "$LLM_PROJECT_ROOT/tests/test_checkpoint_metadata.py" -q
```

预期：数据只包含虚构咖啡店角色，digest 可复算；system/user labels 全为 `-100`，assistant labels
有效；adapter/checkpoint 测试覆盖失败不覆盖成功的路径。

## 3. 项目 2：生成正式 Toy 数据

获得启动确认后执行：

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/v1" \
  --count 400 --seed 42 --version toy-v1
```

生成后只读核对 `dataset_manifest.json`：`dataset_id`、generator/preprocessing version、count、
scenario 分布、source（synthetic）、文件 SHA-256 和 schema version。若数据 digest 变化，生成
新的 config/data identity；不覆盖旧目录。

## 4. 项目 2：只读完整计划

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/ray-job-smoke.yaml" --plan
```

该步骤验证不可变数据和 split digest、模型 snapshot、项目环境、资源、主目标、role/promotability、
test untouched 和完整性声明；不加载完整权重、不创建 MLflow Run、不训练。任一项失败即停止。

## 5. 构建并发布不可变 Release

```bash
/data/conda/envs/llm-lora-ray-py312/bin/python \
  "$LLM_PROJECT_ROOT/scripts/build_release.py" \
  --output-dir "$PWD/platform-data/llm-lora-playground-release"
```

Release 同时包含 working-dir ZIP、仅作 provenance 的项目 wheel、项目 Python 解释器、固定代码 revision、稳定仓库数据根和
MLflow 配置，并以 content-addressed ID 发布到 MinIO；同名不同内容必须拒绝覆盖。源码、训练入口、配置
或环境发生改变后必须构建新 Release，旧 Release 不删除。

Runtime Env 不通过 `py_modules` 安装 wheel；Driver 直接运行 working-dir 中的冻结源码，并使用节点预装的
项目解释器。这样不会在 Job 启动时重复解析或安装 Torch、Transformers 等大型依赖。

## 6. 通过 Galatea 执行 10-step Toy LoRA smoke

在 Galatea 客户端中依次：

1. `galatea_select_project(projectId="llm-lora-playground")`；
2. `galatea_inspect_project()`；
3. `galatea_plan_run(configPath="configs/ray-job-smoke.yaml", releaseManifestPath="<release-id>/release.json", role="smoke", attempt="<unique-attempt>")`；
4. 使用同一组参数调用 `galatea_submit_job`；完全权限或一次性批准仍必须绑定本次 readiness digest；
5. 使用 `galatea_observe_job` 观察到 Ray `SUCCEEDED`，再通过 MLflow Tracking/Artifact API 验证 Run。

禁止用 shell 直接调用 `ray job submit`、`job/submit_train.py` 或 `scripts/submit_train.py`。Galatea
从 plan 透传 `num_gpus=1`、`cpus=4`、`memory_gb=8`，并把 release、readiness、execution identity、
submission 和 `promotable=false` 写入受治理 metadata。

运行结束检查：

- 不含首次下载控制在 10 分钟内；
- train/validation loss、learning rate、gradient norm 和 step 数存在；
- adapter 只含 PEFT 工件，完整 base 未被复制；
- 全新 Python 进程加载 base revision + adapter 成功；
- checkpoint metadata、文件 digest、run/config/data identity 一致；
- base 与 base+adapter 在固定风格 fixture 上存在可解释差异；
- 失败训练没有覆盖任何成功 adapter/Run。

## 7. 项目 2：1 epoch baseline

只有 smoke 全部通过后才执行：

```bash
使用第 6 节相同的 Galatea 调用顺序，只把 configPath 改为
`configs/toy-lora-baseline.yaml`，role 保持配置声明的值，并使用新的 attempt。
```

目标是不含首次下载控制在 30 分钟内。此 Run 仍是学习 baseline，不是最终模型，不进行 Registry alias
更新。若 loss 不合理下降、风格检查不优于 base 或资源超限，保留诊断并修复契约/数据后新建 Run。

## 8. 项目 3：冻结数据和评估协议

### 7.1 生成约 1,000 条数据并分组切分

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/v2" \
  --count 1000 --seed 42 --version toy-v2
python "$LLM_PROJECT_ROOT/scripts/evaluate.py" \
  --config "$LLM_PROJECT_ROOT/configs/reproducible-eval.yaml" \
  --build-split --data "$LLM_TOY_DATA_ROOT/v2"
```

冻结前检查：同一 `scenario_id`/近重复族没有跨 split；split manifest digest 可重复；source、
preprocessing、model revision、seed 和 sample/group count 一致。冻结后复制 manifest 到受控 artifact，
不要手工改 test 清单。

### 7.2 运行比较

为每个需要声明的 Base、Prompt-only、LoRA 变体准备继承同一冻结协议的 canonical config，并逐个使用
第 6 节的 immutable Release、Galatea plan/authorization 和固定 Ray Driver 提交。不得用
`scripts/evaluate.py --run`、Notebook 或 shell 生成比较证据；该脚本的 `--run` 会 fail closed。

三组必须共享 split、prompt、tokenizer、generation、seed、输入和 max tokens。当前 Driver 原生记录
同一 validation population 上的 Base 与 LoRA；若要声明独立 Prompt-only 结果，必须先在同一 Driver
和 Artifact 契约中实现并测试该 variant，不能用本地输出补齐矩阵。候选只用 train/validation 证据选择；
记录 validation loss、生成长度、格式/风格规则、重复率、性能和资源指标。

### 7.3 冻结候选并只评估 test 一次

候选冻结由 Galatea 绑定 Trial Run ID、validation evidence digest、checkpoint、prompt、metric
definition、split/config digest。随后使用 `role=champion`、`evaluate_test=true` 的新 canonical config，
经新的 readiness 和授权提交同一个固定 Ray Driver。Driver 必须先取得原子 test-once claim；
`scripts/evaluate.py --split test --run` 永远不是允许的执行入口。

输出必须带 `test_evaluation_id`、candidate/config/split digest。若后续改 prompt、阈值、checkpoint、
数据或评估规则，旧 test 结果作废，不能继续作为最终证据。

## 9. 项目 3：MLflow Artifact round-trip

```bash
python "$LLM_PROJECT_ROOT/scripts/roundtrip_artifact.py" \
  --run-id RUN_ID \
  --output-dir "$LLM_TOY_DATA_ROOT/roundtrip/RUN_ID"
```

脚本必须仅使用 MLflow Tracking/Artifact API：找到 Run、下载 adapter/manifest/report、计算 SHA-256、
在新进程加载 base+adapter、复跑冻结评估并比较指标/协议/digest。服务端 `mlflow.db` 和 MinIO 文件系统
不可读；哈希或结果不一致时 `roundtrip_status=failed`。

## 10. 私有脱敏实验 Trial

私有实验不允许改变训练代码或执行后端。先用 `configs/wechat-owner-bulk-approved-5k.yaml` 构建只读
plan，确认 5,000 条、train/validation/test=`3776/759/465`、data SHA-256、split SHA-256、
`role=trial`、`promotable=false`、`formal_training_eligible=false`、`evaluate_test=false`。随后按第 6 节
使用同一不可变 Release 和 Galatea 提交。训练循环只迭代 train，validation 仅推理，465 条 test 保持
untouched；不得用全部 5,000 条训练，不得读取 test 来调参。

训练 Driver 只接受 Galatea 注入的完整 `galatea.*` metadata（execution identity、release、readiness、
submission、execution mode 和 promotable）；普通 Ray Job 或 shell 直接调用即使带 `--run` 也会
fail closed。线上 Champion 仍复用同一 Driver，只在候选证据绑定且完成一次性 test claim 后读取 test。
旧的 `scripts/wechat_full_baseline.py` 全量本地基线入口也已 fail closed；Base 对照由同一个 governed
Run 在 validation 上完成。旧本地基线仅作历史诊断，不能与当前 Ray Trial 合并为规范实验结论。

当前项目声明 `pauseResume: false`；不要伪造跨 Job 恢复证据。失败后保留原 Run/attempt/checkpoint，
修复 contract 并用新 attempt、新 Ray submission 和新 MLflow Run 重试。

## 11. 指标、报告与故障排查

每个 governed Run 至少统计：

- 训练质量：逐 step `train_loss`、learning rate、gradient norm，LoRA/Base validation loss 与 perplexity；
- 训练性能：数据准备、模型加载、tokenize、优化计算、validation、checkpoint、Artifact 上传和 worker wall time；
- 吞吐/资源：samples/s、target tokens/s、optimizer steps/s、validation tokens/s、GPU allocated/reserved 峰值、进程 RSS；
- 生成质量：token F1、ROUGE-L F1、exact match、non-empty/format rate、3-gram repetition、max-length stop rate；
- 生成性能：成功率、生成 tokens/s、samples/s、p50/p95 batch latency、平均/p50/p95 长度、生成峰值显存；
- 辅助质量分：报告固定公式和 Base→LoRA 差值。它不是人工偏好、安全或生产资格的替代品；
- 隐私：不持久化生成正文，只保存聚合指标和输出 digest 集合。

推荐每个 Run 的本地临时结果结构：

```text
platform-data/llm-baselines/<project>/<run_id>/
├── manifests/run_manifest.json
├── manifests/dataset_manifest.json
├── manifests/split_manifest.json
├── checkpoints/<attempt_id>/step-<n>/
├── reports/metrics.json
├── reports/evaluation.json
├── reports/recovery.json
└── reports/environment.json
```

排查顺序：

1. 配置/schema：确认 digest、资源和 secret 检查；
2. 数据 manifest：确认 count、group split、近重复和 source；
3. tokenizer/mask：检查 assistant span 和 `-100` labels；
4. 模型/LoRA：检查 `q_proj/v_proj` 是否真实存在、revision 是否 immutable；
5. checkpoint：检查 `complete` 标记和 SHA-256；
6. MLflow/Artifact：确认 API 健康和下载校验；
7. Ray：确认 Job ID、attempt、Driver owner、资源和恢复指针；
8. 重试：新建 Run/attempt，记录 `retry_of`，不覆盖旧目录。

## 12. 结束与后续

项目 2–4 全部验收后，归档配置、manifest、Run ID、Artifact round-trip、Ray 恢复报告和风险清单。
只有这一步完成，才讨论项目 5 的真实微信数据工程；真实数据授权、脱敏、人工审核和撤回流程不会因
项目 2–4 的成功而自动放行。
