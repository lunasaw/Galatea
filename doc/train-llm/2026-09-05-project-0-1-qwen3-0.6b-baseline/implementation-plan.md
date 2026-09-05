# 项目 0+1 基线实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `train-model/llm-lora-playground/` 中实现可复现的 Qwen3-0.6B 环境/GPU 检查、固定脱敏 fixture 推理、MLflow 追踪和 Artifact 回读。

**Architecture:** 以参数化 `scripts/infer.py` 编排四个职责包：数据 manifest/fixture、GPU/runtime、模型/template、MLflow tracking。默认执行是有界本地单进程推理；Ray Job 只作为同一入口的可选包装。外部聊天数据只读引用，模型输出默认不上传。

**Tech Stack:** Python 3.12、PyTorch、Transformers >=4.51.0、Datasets、Accelerate、MLflow Tracking/Artifact API、Ray（可选 Job 包装）、JSON Schema、unittest/pytest。

## Global Constraints

- 固定数据：`wechat_aa807aaad90dc4463964`，`pipeline_version=wechat-preprocess-v1.2`。
- 只从 `work/05_candidates/candidates.jsonl` 的 validation session 构造 20 条诊断 fixture；不把空的 `datasets/*.jsonl` 当作可训练集。
- 模型：`Qwen/Qwen3-0.6B`，BF16，`cuda:0`，`enable_thinking=false`，最大输入 512 token。
- 生成：`temperature=0.7`、`top_p=0.9`、`max_new_tokens=128`、seed 42。
- 必须使用 tokenizer `apply_chat_template`，不手写特殊 token。
- 记录数据/source/split/pipeline/config/code/model/environment/resource 身份；失败 Run 不覆盖成功 Run。
- MLflow 和 MinIO 通过 API 使用；客户端不得读取 `mlflow.db` 或服务端 MinIO 文件系统。
- 本阶段不训练、不注册模型、不更新 alias，不终止现有 GPU 进程。

---

### Task 1: 创建项目骨架与配置契约

**Files:**
- Create: `train-model/llm-lora-playground/README.md`
- Create: `train-model/llm-lora-playground/conda.yaml`
- Create: `train-model/llm-lora-playground/pyproject.toml`
- Create: `train-model/llm-lora-playground/galatea.project.yaml`
- Create: `train-model/llm-lora-playground/configs/inference.yaml`
- Create: `train-model/llm-lora-playground/configs/data.yaml`
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/config.py`
- Test: `train-model/llm-lora-playground/tests/test_config.py`

**Interfaces:**
- `ProjectConfig`: parsed `environment`, `data`, and `inference` mappings with attribute access used by the CLI.
- `load_config(path: Path) -> ProjectConfig`
- `validate_config(config: ProjectConfig) -> list[str]`
- `canonical_config_digest(config: ProjectConfig) -> str`

- [ ] **Step 1: Write the failing test**

```python
def test_config_rejects_non_single_gpu(tmp_path):
    path = tmp_path / "inference.yaml"
    path.write_text("resources:\n  num_gpus: 2\n", encoding="utf-8")
    config = load_config(path)
    assert "num_gpus must be 1" in validate_config(config)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_config.py -q`

Expected: FAIL because the project package and validator do not exist.

- [ ] **Step 3: Implement typed configuration**

Use dataclasses or Pydantic models. Enforce the exact model ID, BF16 dtype, `cuda:0`, `max_new_tokens=128`, one-GPU resource declaration, explicit data root, and no credentials in YAML. Produce a canonical JSON digest after parsing defaults.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground
git commit -m "training: scaffold llm inference baseline project"
```

### Task 2: 实现数据 manifest 校验与确定性 fixture

**Files:**
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/data.py`
- Create: `train-model/llm-lora-playground/tests/test_data.py`
- Create: `train-model/llm-lora-playground/tests/fixtures/candidate.jsonl`

**Interfaces:**
- `DatasetExpectation`: expected `dataset_id`, `source_sha256`, `config_sha256`, and `pipeline_version`.
- `DatasetIdentity`: validated expectation plus `split_digest` and source root reference.
- `InferenceFixture`: `fixture_id`, `sample_id`, `session_id`, `input_messages`, `reference_text`, `reference_output_tokens`.
- `DataContractError`: exception raised for digest, split, role, or target-removal violations.
- `validate_dataset(root: Path, expected: DatasetExpectation) -> DatasetIdentity`
- `build_validation_fixtures(root: Path, split: str, count: int) -> list[InferenceFixture]`
- `fixture_digest(fixtures: Sequence[InferenceFixture]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_fixture_drops_final_assistant_target(tmp_path):
    candidate_path = tmp_path / "work/05_candidates/candidates.jsonl"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text(json.dumps({
        "sample_id": "sample-1", "session_id": "session-1",
        "messages": [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "目标回复"},
        ],
        "metadata": {"target_speaker": "target"},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    write_minimal_manifests(tmp_path, validation_sessions=["session-1"])
    fixture = build_validation_fixtures(tmp_path, "validation", 1)[0]
    assert fixture.input_messages[-1]["role"] != "assistant"
    assert fixture.reference_text == "目标回复"

def test_dataset_digest_mismatch_blocks(tmp_path):
    with pytest.raises(DataContractError, match="source_sha256"):
        validate_dataset(tmp_path, DatasetExpectation(
            dataset_id="wechat_aa807aaad90dc4463964",
            source_sha256="0" * 64,
            config_sha256="9a1d4a01e2926c9f6cd99e220609eb30c50a5d7582d45ccbfbd2cf3515fa3af1",
            pipeline_version="wechat-preprocess-v1.2",
        ))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_data.py -q`

Expected: FAIL because data contracts are not implemented.

- [ ] **Step 3: Implement streaming validation**

Read candidate JSONL incrementally, verify source/config/split reports, map validation session IDs from `split_manifest.json`, sort by `sample_id`, remove only the final target from model input, and preserve only IDs/hashes in the fixture manifest. Refuse `text_original_ref` and unknown speaker roles.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_data.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground/src/llm_lora_playground/data.py train-model/llm-lora-playground/tests
git commit -m "training: validate wechat baseline data and fixtures"
```

### Task 3: 实现 runtime 与 GPU preflight

**Files:**
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/runtime.py`
- Create: `train-model/llm-lora-playground/tests/test_runtime.py`

**Interfaces:**
- `GpuCheck`: `status`, `reason`, `device`, `bf16_matmul_passed`.
- `GpuProcess`: `pid`, `command`, `memory_mib` (with command redacted if unavailable).
- `collect_environment_snapshot() -> dict[str, Any]`
- `check_gpu_capabilities(device: str, dtype: torch.dtype) -> GpuCheck`
- `collect_visible_gpu_processes() -> list[GpuProcess]`

- [ ] **Step 1: Write the failing test**

```python
def test_gpu_check_reports_unavailable_device(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    result = check_gpu_capabilities("cuda:0", torch.bfloat16)
    assert result.status == "blocked"
    assert result.reason == "cuda_unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_runtime.py -q`

Expected: FAIL because runtime checks do not exist.

- [ ] **Step 3: Implement read-only capture**

Capture Python, package versions, GPU name/count, memory, Torch CUDA runtime, BF16 matmul result and `nvidia-smi` process text if available. Never invoke kill/reset/empty-cache on unrelated processes. Return structured blocked reasons.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground/src/llm_lora_playground/runtime.py train-model/llm-lora-playground/tests/test_runtime.py
git commit -m "training: add gpu and environment preflight"
```

### Task 4: 实现 Qwen3 loader、chat template 与生成指标

**Files:**
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/models/causal_lm.py`
- Create: `train-model/llm-lora-playground/tests/test_chat_template.py`
- Create: `train-model/llm-lora-playground/tests/test_generation_metrics.py`

**Interfaces:**
- `ModelConfig`: `model_id`, `revision_policy`, `dtype`, `device`, `max_input_tokens`, `enable_thinking`.
- `GenerationConfig`: `do_sample`, `temperature`, `top_p`, `max_new_tokens`, `repetition_penalty`, `seed`.
- `ModelInputs`: tokenized tensors and `prompt_tokens`.
- `LoadedCausalLM`: model, tokenizer, resolved revision, and device.
- `GenerationResult`: `prompt_tokens`, `generated_tokens`, `total_latency_ms`, `first_token_latency_ms`, `tokens_per_second`, `peak_gpu_memory_mib`, `output_sha256`, and decode status.
- `load_model_and_tokenizer(config: ModelConfig) -> LoadedCausalLM`
- `prepare_inputs(tokenizer: PreTrainedTokenizerBase, messages: list[dict[str, str]], enable_thinking: bool) -> ModelInputs`
- `generate_one(model: LoadedCausalLM, inputs: ModelInputs, generation: GenerationConfig) -> GenerationResult`

- [ ] **Step 1: Write the failing tests**

```python
def test_prepare_inputs_uses_chat_template(fake_tokenizer):
    prepare_inputs(fake_tokenizer, [{"role": "user", "content": "你好"}], False)
    assert fake_tokenizer.apply_chat_template.called

def test_generation_result_counts_new_tokens_only():
    result = GenerationResult(prompt_tokens=12, generated_tokens=5, total_latency_ms=100.0, first_token_latency_ms=20.0)
    assert result.tokens_per_second == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_chat_template.py train-model/llm-lora-playground/tests/test_generation_metrics.py -q`

Expected: FAIL because model interfaces are not implemented.

- [ ] **Step 3: Implement loader and generation**

Resolve and record an immutable model revision, load BF16 to `cuda:0`, call `apply_chat_template(..., add_generation_prompt=True)`, pass `enable_thinking=False` using the installed Transformers API, decode only generated IDs, and measure first-token/total latency plus peak memory. Raise a clear compatibility error if the installed template signature cannot disable thinking.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_chat_template.py train-model/llm-lora-playground/tests/test_generation_metrics.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground/src/llm_lora_playground/models train-model/llm-lora-playground/tests
git commit -m "training: add qwen3 inference loader and metrics"
```

### Task 5: 实现 MLflow tracking、manifest 与 Artifact round-trip

**Files:**
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/tracking.py`
- Create: `train-model/llm-lora-playground/tests/test_tracking.py`

**Interfaces:**
- `RunContext`: MLflow client, experiment ID, run ID, and manifest digest.
- `ArtifactIntegrityError`: exception raised when API-downloaded artifact bytes do not match the expected SHA-256.
- `build_run_manifest(...) -> dict[str, Any]`
- `start_inference_run(manifest: dict[str, Any]) -> RunContext`
- `log_baseline_metrics(context: RunContext, metrics: dict[str, float]) -> None`
- `verify_artifact_roundtrip(client: MlflowClient, run_id: str, artifact_path: str, expected_sha256: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
def test_manifest_marks_inference_only():
    manifest = build_run_manifest(**MINIMAL_MANIFEST_INPUT)
    assert manifest["inference_baseline_only"] is True
    assert manifest["model"]["enable_thinking"] is False

def test_roundtrip_rejects_hash_mismatch(fake_mlflow_client):
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact_roundtrip(fake_mlflow_client, "run-1", "reports/metrics.json", "0" * 64)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_tracking.py -q`

Expected: FAIL because tracking interfaces do not exist.

- [ ] **Step 3: Implement driver-owned tracking**

Require explicit Tracking URI and experiment name, log tags/params/metrics, upload JSON artifacts through MLflow APIs, compute the idempotency key, and verify downloaded artifact hashes. Store only output hash/length by default.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_tracking.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground/src/llm_lora_playground/tracking.py train-model/llm-lora-playground/tests/test_tracking.py
git commit -m "training: track inference baseline in mlflow"
```

### Task 6: 实现 `infer.py` CLI 和报告

**Files:**
- Create: `train-model/llm-lora-playground/scripts/infer.py`
- Create: `train-model/llm-lora-playground/tests/test_infer_cli.py`
- Modify: `train-model/llm-lora-playground/README.md`

**Interfaces:**
- CLI flags: `--config`, `--data-config`, `--check-config`, `--plan-fixtures`, `--smoke-only`, `--run`, `--report RUN_ID`, `--output-dir`。
- `BaselineResult`: run ID, status, manifest path, metrics path, and record count.
- `run_baseline(config_path: Path, data_config_path: Path, output_dir: Path) -> BaselineResult`

- [ ] **Step 1: Write the failing test**

```python
def test_check_config_does_not_create_mlflow_run(cli_runner):
    result = cli_runner.invoke([
        "--check-config",
        "--config",
        "doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml",
        "--data-config",
        "doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml",
    ])
    assert result.exit_code == 0
    assert "will_create_mlflow_run=false" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_infer_cli.py -q`

Expected: FAIL because the CLI is not implemented.

- [ ] **Step 3: Implement orchestration**

Implement the sequence from the design: config → data preflight → environment/GPU preflight → model smoke → fixture generation → 20 generations → local reports → MLflow Run/Artifact upload → API round-trip verification. `--check-config` must never read chat text or create a Run.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest train-model/llm-lora-playground/tests/test_infer_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground/scripts/infer.py train-model/llm-lora-playground/tests/test_infer_cli.py train-model/llm-lora-playground/README.md
git commit -m "training: add qwen3 inference baseline cli"
```

### Task 7: 端到端 smoke、API 回读和验收证据

**Files:**
- Create: `train-model/llm-lora-playground/tests/test_end_to_end_contract.py`
- Modify: `doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/acceptance-checklist.md`

- [ ] **Step 1: Run project tests**

Run: `python -m pytest train-model/llm-lora-playground/tests -q`

Expected: all unit and contract tests pass without loading the real model.

- [ ] **Step 2: Run repository tests**

Run: `/data/conda/envs/attend-ray-py312/bin/python -m unittest discover -s tests -p 'test_*.py'`

Expected: existing repository tests pass; no unrelated files are modified.

- [ ] **Step 3: Run the real 2-prompt smoke**

Run the `--smoke-only` command from `runbook.md`, save environment and GPU evidence under `platform-data/llm-baselines/<run_id>/`, and verify no external data file changed.

- [ ] **Step 4: Run the 20-fixture baseline**

Run the `--run` command from `runbook.md`, verify 20 records and MLflow Artifact round-trip, then complete the acceptance checklist.

- [ ] **Step 5: Commit**

```bash
git add train-model/llm-lora-playground doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/acceptance-checklist.md
git commit -m "training: verify qwen3 inference baseline contract"
```
