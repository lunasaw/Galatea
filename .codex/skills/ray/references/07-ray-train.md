# Ray Train

## Overview

Ray Train is a distributed training library that scales ML training workloads from a single machine to large clusters.

## Framework-Specific Trainers

### TorchTrainer
```python
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig

def train_func(config):
    import torch
    model = torch.nn.Linear(10, 1)
    model = ray.train.torch.prepare_model(model)
    optimizer = torch.optim.SGD(model.parameters(), lr=config["lr"])
    for epoch in range(config["epochs"]):
        for batch in ray.train.get_dataset_shard("train").iter_batches():
            optimizer.zero_grad()
            output = model(batch["x"])
            loss = torch.nn.functional.mse_loss(output, batch["y"])
            loss.backward()
            optimizer.step()
        ray.train.report({"loss": loss.item()})

trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    train_loop_config={"lr": 0.01, "epochs": 10},
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
    datasets={"train": train_ds},
)
result = trainer.fit()
```

### TensorFlowTrainer
```python
from ray.train.tensorflow import TensorflowTrainer

def train_func(config):
    import tensorflow as tf
    strategy = tf.distribute.MultiWorkerMirroredStrategy()
    with strategy.scope():
        model = build_model()
    # Training loop...

trainer = TensorflowTrainer(
    train_loop_per_worker=train_func,
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
)
```

### HuggingFaceTrainer
```python
from ray.train.huggingface import HuggingFaceTrainer

def train_func(config):
    from transformers import Trainer, TrainingArguments
    args = TrainingArguments(
        output_dir=".",
        num_train_epochs=config["epochs"],
        per_device_train_batch_size=config["batch_size"],
    )
    trainer = Trainer(model=model, args=args, train_dataset=train_ds)
    trainer.train()

trainer = HuggingFaceTrainer(
    train_loop_per_worker=train_func,
    train_loop_config={"epochs": 3, "batch_size": 16},
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
)
```

### XGBoostTrainer / LightGBMTrainer
```python
from ray.train.xgboost import XGBoostTrainer
from ray.train.lightgbm import LightGBMTrainer

trainer = XGBoostTrainer(
    params={"objective": "binary:logistic", "max_depth": 6},
    label_column="target",
    datasets={"train": train_ds, "eval": eval_ds},
    scaling_config=ScalingConfig(num_workers=4),
)
result = trainer.fit()
```

## ScalingConfig

```python
from ray.train import ScalingConfig

ScalingConfig(
    num_workers=1,                          # Number of training workers
    use_gpu=False,                          # Use GPUs
    use_tpu=False,                          # Use TPUs
    resources_per_worker=None,              # {"CPU": 2, "GPU": 1}
    placement_strategy="PACK",              # PACK, SPREAD, STRICT_PACK, STRICT_SPREAD
    accelerator_type=None,                  # "A100", "T4", etc.
    trainer_resources=None,                 # Resources for trainer actor
)
```

## RunConfig

```python
from ray.train import RunConfig

RunConfig(
    name="my_experiment",
    storage_path="/tmp/results",
    stop={"training_iteration": 100},
    checkpoint_config=CheckpointConfig(
        checkpoint_frequency=5,
        checkpoint_score_attribute="loss",
        checkpoint_score_order="min",
        checkpoint_at_end=True,
    ),
    failure_config=FailureConfig(
        max_failures=3,
        fail_fast=False,
    ),
    verbose=1,
    callbacks=[],
    progress_reporter=None,
)
```

## Session API

```python
import ray.train

# Report metrics (and optionally save checkpoint)
ray.train.report(
    {"loss": 0.05, "accuracy": 0.98},
    checkpoint=ray.train.Checkpoint.from_directory("/tmp/ckpt"),
)

# Get current checkpoint
checkpoint = ray.train.get_checkpoint()

# Get dataset shard
shard = ray.train.get_dataset_shard("train")
for batch in shard.iter_batches(batch_size=256):
    process(batch)

# World information
rank = ray.train.get_world_rank()        # Global rank (0-indexed)
size = ray.train.get_world_size()         # Total number of workers
local_rank = ray.train.get_local_rank()   # Rank within node
local_size = ray.train.get_local_world_size()  # Workers on this node
node_rank = ray.train.get_node_rank()     # Node index

# Experiment metadata
name = ray.train.get_experiment_name()
trial_name = ray.train.get_trial_name()
trial_id = ray.train.get_trial_id()
metadata = ray.train.get_metadata()
```

## Checkpointing

```python
from ray.train import Checkpoint

# Create from directory
ckpt = Checkpoint.from_directory("/tmp/checkpoint")

# Create from bytes
ckpt = Checkpoint.from_bytes(b"data")

# Access
path = ckpt.to_directory()
with ckpt.as_directory() as path:
    model.load_state_dict(torch.load(f"{path}/model.pt"))

metadata = ckpt.get_metadata()
ckpt.set_metadata({"epoch": 10, "loss": 0.05})

# Save checkpoint during training
ray.train.save_checkpoint(
    epoch=epoch,
    model_state_dict=model.state_dict(),
    optimizer_state_dict=optimizer.state_dict(),
)
```

## PyTorch-Specific Utilities

```python
from ray.train.torch import prepare_model, prepare_data_loader

# Prepare model for distributed training (wraps with DDP)
model = prepare_model(model, parallel_strategy="auto")

# Prepare data loader for distributed sampling
loader = prepare_data_loader(
    loader,
    add_dist_sampler=True,
    auto_transfer=True,
)
```

## DeepSpeed Integration
```python
from ray.train.huggingface import HuggingFaceTrainer

def train_func(config):
    from transformers import TrainingArguments
    args = TrainingArguments(
        deepspeed="ds_config.json",
        # or deepspeed config dict
    )
```

## Collective Operations
```python
from ray.train.collective import barrier, broadcast_from_rank_zero

# Synchronize all workers
barrier()

# Broadcast data from rank 0
config = broadcast_from_rank_zero(config)
```

## Result

```python
result = trainer.fit()

# Access results
result.metrics                     # Best metrics
result.checkpoint                  # Best checkpoint
result.path                        # Storage path
result.error                       # Error if failed
result.metrics_dataframe           # All metrics as DataFrame
```
