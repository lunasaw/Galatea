# Qwen3.5 Toy LoRA and Ray Job playground

This project contains the bounded project-0+1 inference baseline and the synthetic,
privacy-safe project 2–4 Toy LoRA workflow. Project 2 validates SFT chat templating,
assistant-only loss masks, PEFT adapter/checkpoint lifecycle, and deterministic synthetic
data. Project 3 freezes group splits and compares Base, Prompt-only, and LoRA variants.
Project 4 submits the same training boundary through a single-GPU Ray Job with atomic
metadata and safe checkpoint recovery.

Model weights, generated data, adapters, checkpoints, manifests and reports stay outside
the source tree under `platform-data/llm-baselines/`. This project never reads the real
WeChat dataset for project 2–4 and never changes a model registry alias.

Contract-only checks do not require torch, Transformers, Ray or MLflow:

```bash
export PYTHONPATH="$PWD/train-model/llm-lora-playground/src"
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config train-model/llm-lora-playground/configs/toy-lora-smoke.yaml --check-config
python train-model/llm-lora-playground/scripts/generate_synthetic.py \
  --output-dir platform-data/llm-baselines/toy-lora/check --count 8 --seed 42 --check-only
python -m unittest discover -s train-model/llm-lora-playground/tests -p 'test_*.py'
```

The `--run` and Ray submission paths are intentionally guarded by model/GPU/service
preflight. A missing dependency or resource returns `blocked`; it is never represented as
training evidence.

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

Toy LoRA lifecycle:

```bash
python scripts/train_lora.py --config configs/toy-lora-smoke.yaml --check-config
python scripts/generate_synthetic.py --output-dir ../../platform-data/llm-baselines/toy-lora/v1 --count 400
python scripts/train_lora.py --config configs/toy-lora-smoke.yaml \
  --data ../../platform-data/llm-baselines/toy-lora/v1 --run
python scripts/evaluate.py --config configs/reproducible-eval.yaml --variant base --split validation
python job/submit_train.py --config configs/ray-job-smoke.yaml
```

The documented runbook in `doc/train-llm/2026-09-05-project-2-4-toy-lora-ray/` is the
authoritative order for GPU execution, validation-only candidate selection, test-once
evaluation, MLflow Artifact API round-trip, and interruption/recovery drills.
