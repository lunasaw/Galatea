# Qwen3.5 LoRA training project

This project contains the bounded project-0+1 inference baseline and one governed LoRA
training architecture for both synthetic and authorized private datasets. Project 2 validates SFT chat templating,
assistant-only loss masks, PEFT adapter/checkpoint lifecycle, and deterministic synthetic
data. Project 3 freezes group splits and compares Base, Prompt-only, and LoRA variants.
Project 4 submits the same training boundary through an immutable release, Galatea readiness
plan and single-GPU Ray Job with Driver-owned MLflow metadata and safe checkpoint recovery.
Experiments and production candidates use the same code and execution path; only dataset identity,
role, authorization, holdout access and promotability differ.

The reusable open-ended chat comparison contract is documented in
[`doc/train-llm/fine-tuning-evaluation-protocol.md`](../../doc/train-llm/fine-tuning-evaluation-protocol.md).
It defines the four evaluation layers, the fair Base/Prompt-only/LoRA matrix, validation-only
selection, test-once evaluation, human blind preference, and privacy/safety hard gates.
The repository-wide execution classification and evidence contract are defined by
[`governed-training-workflow`](../../.codex/skills/governed-training-workflow/SKILL.md).

Model weights, generated data, adapters, checkpoints, manifests and reports stay outside
the source tree under `platform-data/`. Private redacted data may be referenced only by an
explicitly authorized config. A trial remains non-promotable when its config declares
`run.promotable: false`; no training command changes a model registry alias.

Contract-only checks do not require torch, Transformers, Ray or MLflow:

```bash
export PYTHONPATH="$PWD/train-model/llm-lora-playground/src"
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config train-model/llm-lora-playground/configs/toy-lora-smoke.yaml --check-config
python train-model/llm-lora-playground/scripts/generate_synthetic.py \
  --output-dir platform-data/llm-baselines/toy-lora/check --count 8 --seed 42 --check-only
python -m unittest discover -s train-model/llm-lora-playground/tests -p 'test_*.py'
```

`scripts/train_lora.py` and `scripts/evaluate.py` are read-only planning/check boundaries; their
`--run` modes always fail closed. Actual training and durable evaluation evidence have one path:

```text
immutable release -> Galatea plan -> evidence-bound authorization -> Galatea Ray submission
-> Driver-owned MLflow Run -> train/validation -> checkpoint/model/report upload
-> MLflow Artifact API round-trip -> terminal Run/Job metadata
```

The fixed Driver accepts training only when Galatea supplies the complete `galatea.*`
release/readiness/execution binding. A direct Ray Job or shell call with `--run` is
rejected; `job/submit_train.py` is an internal Driver boundary. Trial/evaluation Runs
remain non-promotable, while a separately authorized Champion may claim the final test
split exactly once.

A missing dependency, data/split identity, resource, release or service returns `blocked`;
local execution is never represented as governed Ray evidence. `experimental_only`, private data,
owner approval, and `promotable=false` do not change this execution path. The final test partition is
not loaded by smoke/trial training or validation quality evaluation.

## Standard Ray LLM inference

The experimental adapter is served through the official Ray LLM integration, rather than a
project-specific Transformers HTTP wrapper:

```text
Galatea planInference
  -> evidence-bound Galatea authorization
  -> Ray Job (immutable Release, Ray 2.58.0 runtime)
  -> ray.serve.llm + vLLM (Ray Serve)
  -> OpenAI-compatible /v1/chat/completions
```

The pinned serving stack is Ray `2.58.0`, vLLM `0.26.0`, Transformers `5.16.1`, Torch `2.11.0`,
and PEFT `0.20.0`. The current trial service is loopback-only at `http://127.0.0.1:8000` and
exposes these model IDs:

```text
qwen35-wechat-trial
qwen35-wechat-trial:wechat-private-5k-standard-20260906-v2-step-944
```

After the first vLLM compilation/warmup, call the adapter with the OpenAI protocol:

```bash
curl -sS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen35-wechat-trial:wechat-private-5k-standard-20260906-v2-step-944",
    "messages": [{"role": "user", "content": "你好，请用一句话介绍你自己。"}],
    "max_tokens": 64,
    "temperature": 0,
    "stream": false
  }'
```

Future inference starts must use the Galatea `galatea_plan_inference` →
`galatea_submit_inference` path with the same immutable release and unchanged config. Do not run
`ray job submit`, `job/submit_inference.py`, or `scripts/serve_lora.py --run` manually; the serving
entrypoint requires the Galatea binding and rejects local execution. The adapter remains a Trial
(`promotable=false`, `test_access=untouched`) even while it is available for controlled inference.

### Architecture and adapter readiness

Training and official Ray Serve/vLLM serving now both pin the concrete
`Qwen3_5ForConditionalGeneration` class and record
`model_architecture: qwen3_5_conditional_generation` in adapter metadata. Its text backbone is
under `language_model.model.layers.*` when PEFT is attached to the multimodal wrapper. The
historical adapter was exported through the pure-text `Qwen3_5ForCausalLM` tree
(`model.model.layers.*`), so it is intentionally blocked at readiness until it is re-exported from
the same class used by serving. No key-prefix rewrite is supported.

Readiness now performs three checks: concrete base architecture, adapter tensor-key architecture,
and a multi-probe Base-vs-LoRA logits check. A message such as “Loaded new LoRA adapter” only proves
download/registration. If all probes are identical, readiness fails and the service is not exposed.
`max_loras: 1` remains a hard constraint; the project does not assume online composition of style and
memory adapters.

### Long-term memory

Style SFT and memory-grounded behavior are separate contracts. Style samples redact identifiers,
deduplicate by session, preserve multi-turn context, and train assistant-only loss. Memory is kept
outside model parameters in owner-scoped records with `candidate/confirmed/superseded/deleted`
status, source message IDs, confidence, sensitivity, and validity windows. Serving retrieves only the
current owner's confirmed, non-expired records, injects them in an explicit `<memory>` block, and
must say it does not know when evidence is absent. Model-generated text is never written back as a
fact automatically.

The reference implementation is in `src/llm_lora_playground/memory.py` and
`src/llm_lora_playground/data_prep.py`; it supports lexical BM25-like retrieval and an optional
semantic scorer so a production vector index can be added without changing the prompt contract.
`memory-grounded-v1` evaluates retrieval and answer safety separately: evidence support, unsupported
claim/refusal behavior, conflict recency, cross-owner leakage, and PII/canary leakage (the latter two
must be zero). The current Trial remains `formal_training_eligible=false` and `promotable=false`.

The serving ingress owns a versioned `prompt.persona_system_prompt`. It is combined with the memory
evidence policy on the server, and client-provided system messages are discarded before forwarding to
the model. This makes the intended intimate, conversational style a runtime contract instead of an
accidental property of the training transcript. Retrieval also requires either a lexical anchor or a
strong semantic match; generic questions are left without memory context so unrelated chat chunks do
not steer the answer. Updating this prompt or retrieval rule changes the inference config digest and
therefore requires a new immutable Release and a new governed inference plan.

The Ray Runtime Environment reader requires only `ListBucket` and `GetObject` below
`s3://training-data/ray-runtime/llm-lora-playground/`; it must not receive upload, delete,
dataset or MLflow artifact permissions. Add this prefix before publishing the first release,
because Ray may cache a failed Runtime Env URI.

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=llm-lora-playground
export WECHAT_DATA_ROOT=/data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964
export QWEN35_MODEL_PATH=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B

python scripts/infer.py --config configs/inference.yaml --check-config
python scripts/infer.py --config configs/inference.yaml --smoke-only
```

The same smoke can be submitted to Ray Jobs (the driver remains the only process that could
write tracking state):

```bash
python job/submit.py --submission-id ray-llm-qwen35-project01-smoke-<timestamp>
```

Toy LoRA preparation and read-only planning:

```bash
python scripts/train_lora.py --config configs/toy-lora-smoke.yaml --check-config
python scripts/generate_synthetic.py --output-dir ../../platform-data/llm-baselines/toy-lora/v1 --count 400
python scripts/train_lora.py --config configs/ray-job-smoke.yaml --plan
python scripts/build_release.py
```

Use the Galatea tools to select `llm-lora-playground`, call `galatea_plan_run` with the
immutable `<release-id>/release.json`, and then call `galatea_submit_job` with the exact same
config, role, release and attempt. Do not run `ray job submit`, `job/submit_train.py` or
`scripts/submit_train.py` from a shell; those are fixed internal Driver boundaries.

The governed Ray Run logs optimization curves, validation loss/perplexity, phase timings,
training and generation throughput, GPU/process memory, Base-versus-LoRA generation metrics,
an explicitly auxiliary quality score, output hashes, checkpoint identity and round-trip status.
Generated validation text is not persisted.

The former `scripts/wechat_full_baseline.py` local full-dataset path is disabled. Base
comparison is evaluated inside the same governed Run, on the same validation population and
generation protocol as LoRA. Historical results produced by that legacy local path remain
diagnostic-only and are not valid Galatea evidence.

The consolidated project guide in
[`docs/README.md`](docs/README.md)
is the primary navigation for GPU execution, validation-only candidate selection, test-once
evaluation, MLflow Artifact API round-trip, and interruption/recovery drills. Superseded dated
documents were merged into that guide and removed; Git history remains the audit source.

## WeChat local review UI and provisional baseline

The redacted review export can be copied into a private, auto-saving local review
workspace. The initial copy is deliberately all `keep` for pipeline/UI smoke only;
it is marked `baseline_only` and never changes the governed dataset or claims that
human review and consent verification are complete:

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
python scripts/wechat_review_app.py init
python scripts/wechat_review_app.py serve
```

Open <http://127.0.0.1:8765/>. Each label is saved immediately to
`platform-data/llm-private/wechat-review-baseline/wechat_aa807aaad90dc4463964/review_state.json`;
the source review snapshot remains unchanged. The copied baseline files live below
the same private workspace under `baseline/datasets/` and are not formal SFT input.

To run a bounded base-model baseline on 20 validation rows from that copy (using the
project environment, which contains the Qwen3.5-compatible Transformers build):

```bash
conda activate llm-lora-playground-py312
python scripts/wechat_baseline.py --count 20
```

This records only hashes, timings, token counts, and errors in
`platform-data/llm-baselines/wechat-baseline/`; generated text is not persisted.
