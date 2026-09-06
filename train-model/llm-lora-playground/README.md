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

`scripts/train_lora.py` is read-only: it supports `--check-config` and `--plan`, while `--run`
always fails closed. Actual training has one path:

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
local execution is never represented as governed Ray evidence. The final test partition is not
loaded by smoke/trial training or validation quality evaluation.

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

The documented runbook in `doc/train-llm/2026-09-05-project-2-4-toy-lora-ray/` is the
authoritative order for GPU execution, validation-only candidate selection, test-once
evaluation, MLflow Artifact API round-trip, and interruption/recovery drills.

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
