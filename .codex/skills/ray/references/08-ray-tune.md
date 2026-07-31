# Ray Tune

## Overview

Ray Tune is a distributed hyperparameter tuning library that scales hyperparameter search across a cluster.

## Tuner API

```python
from ray import tune
from ray.tune import Tuner

def trainable(config):
    for epoch in range(100):
        score = train_and_evaluate(config)
        tune.report({"score": score, "epoch": epoch})

tuner = Tuner(
    trainable,
    param_space={
        "lr": tune.loguniform(1e-4, 1e-1),
        "batch_size": tune.choice([16, 32, 64]),
        "layers": tune.randint(1, 5),
        "dropout": tune.uniform(0.1, 0.5),
    },
    tune_config=tune.TuneConfig(
        metric="score",
        mode="max",
        num_samples=50,
        search_alg=OptunaSearch(),
        scheduler=ASHAScheduler(),
    ),
    run_config=train.RunConfig(
        name="my_experiment",
        storage_path="/tmp/tune",
        stop={"training_iteration": 100},
    ),
)
results = tuner.fit()
best = results.get_best_result()
print(best.metrics)
```

## Search Space API

```python
tune.uniform(low, high)                  # Uniform continuous
tune.quniform(low, high, q)              # Quantized uniform
tune.loguniform(low, high)               # Log-uniform
tune.qlouniform(low, high, q)            # Quantized log-uniform
tune.choice(categories)                  # Categorical choice
tune.randint(low, high)                  # Random integer
tune.qrandint(low, high, q)              # Quantized integer
tune.randn(mean, std)                    # Normal distribution
tune.qrandn(mean, std, q)                # Quantized normal
tune.grid_search(values)                 # Grid search (exhaustive)
tune.sample_from(func)                   # Custom sampler
```

### Conditional Search Spaces
```python
param_space = {
    "model": tune.choice(["cnn", "rnn"]),
    "cnn_config": {
        "filters": tune.choice([32, 64, 128]),
        "kernel_size": tune.choice([3, 5]),
    },
    "rnn_config": {
        "hidden_size": tune.choice([64, 128, 256]),
        "num_layers": tune.randint(1, 4),
    },
}
```

## Search Algorithms

### OptunaSearch
```python
from ray.tune.search.optuna import OptunaSearch

search = OptunaSearch(
    metric="score",
    mode="max",
    points_to_evaluate=[{"lr": 0.01}],  # Warm start
)
```

### HyperOptSearch
```python
from ray.tune.search.hyperopt import HyperOptSearch

search = HyperOptSearch(
    metric="score",
    mode="max",
    n_initial_points=20,
    random_state_seed=42,
)
```

### BayesOptSearch
```python
from ray.tune.search.bayesopt import BayesOptSearch

search = BayesOptSearch(
    metric="score",
    mode="max",
    utility_kwargs={"kind": "ucb", "kappa": 2.5},
)
```

### FLAML / BlendSearch / CFO
```python
from ray.tune.search.flaml import CFO, BlendSearch

search = BlendSearch(metric="score", mode="max")
search = CFO(metric="score", mode="max")
```

### Others
```python
from ray.tune.search.bohb import TuneBOHB
from ray.tune.search.nevergrad import NevergradSearch
from ray.tune.search.zoopt import ZOOptSearch
from ray.tune.search.hebo import HEBOSearch
from ray.tune.search.sigopt import SigOptSearch
```

## Schedulers

### ASHAScheduler
```python
from ray.tune.schedulers import ASHAScheduler

scheduler = ASHAScheduler(
    max_t=100,                    # Max training iterations
    grace_period=10,              # Min iterations before stop
    reduction_factor=3,           # Keep top 1/reduction_factor
    brackets=1,                   # Number of brackets
)
```

### HyperBandScheduler
```python
from ray.tune.schedulers import HyperBandScheduler

scheduler = HyperBandScheduler(
    max_t=100,
    reduction_factor=3,
)
```

### MedianStoppingRule
```python
from ray.tune.schedulers import MedianStoppingRule

scheduler = MedianStoppingRule(
    time_attr="training_iteration",
    grace_period=10,
    min_samples_required=5,
)
```

### PopulationBasedTraining (PBT)
```python
from ray.tune.schedulers import PopulationBasedTraining

scheduler = PopulationBasedTraining(
    time_attr="training_iteration",
    perturbation_interval=10,
    hyperparam_mutations={
        "lr": lambda: tune.loguniform(1e-4, 1e-1).func(None),
        "dropout": tune.uniform(0.1, 0.5),
    },
)
```

### FIFOScheduler (Default)
```python
from ray.tune.schedulers import FIFOScheduler
scheduler = FIFOScheduler()  # No early stopping
```

## Trainable API

### Function API
```python
def trainable(config):
    for i in range(100):
        score = train_step(config)
        tune.report({"score": score, "iteration": i})
```

### Class API
```python
from ray.tune.trainable import Trainable

class MyTrainable(Trainable):
    def setup(self, config):
        self.model = build_model(config)
        self.config = config

    def step(self):
        score = train_one_epoch(self.model)
        return {"score": score}

    def save_checkpoint(self, tmp_checkpoint_dir):
        return save_model(self.model, tmp_checkpoint_dir)

    def load_checkpoint(self, checkpoint_path):
        self.model = load_model(checkpoint_path)
```

## ResultGrid

```python
results = tuner.fit()

# Best result
best = results.get_best_result()
best.metrics                        # Best metrics dict
best.checkpoint                     # Best checkpoint
best.path                           # Storage path

# DataFrame
df = results.get_dataframe()

# Iterate
for result in results:
    print(result.metrics)

# Errors
for result in results.errors:
    print(result.error)
```

## CheckpointConfig
```python
from ray.train import CheckpointConfig

CheckpointConfig(
    checkpoint_frequency=5,                 # Save every N iterations
    checkpoint_score_attribute="score",     # Metric for best checkpoint
    checkpoint_score_order="max",           # "max" or "min"
    checkpoint_at_end=True,                # Save at end of trial
    checkpoint_per_trial_distance=None,     # Min distance between checkpoints
)
```

## FailureConfig
```python
from ray.train import FailureConfig

FailureConfig(
    max_failures=3,           # Max total failures across trials
    fail_fast=False,          # True = stop on first failure
)
```

## Logging Integrations

```python
from ray.tune.logger import (
    MLflowLoggerCallback,
    WandbLoggerCallback,
    TensorBoardLoggerCallback,
    JsonLoggerCallback,
    CSVLoggerCallback,
)

run_config = train.RunConfig(
    callbacks=[
        WandbLoggerCallback(
            project="my_project",
            api_key="...",
        ),
        MLflowLoggerCallback(
            tracking_uri="http://localhost:5000",
            experiment_name="my_experiment",
        ),
    ],
)
```

## Stoppers

```python
from ray.tune.stopper import (
    MaximumIterationStopper,
    TrialPlateauStopper,
    ExperimentPlateauStopper,
)

# Stop after N iterations
stopper = MaximumIterationStopper(max_iter=100)

# Stop if metric plateaus
stopper = TrialPlateauStopper(metric="score", std=0.01, num_results=5)
```

## Integration with Ray Train
```python
from ray.train import ScalingConfig
from ray.tune import Tuner
from ray.train.torch import TorchTrainer

trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
)

tuner = Tuner(
    trainer,
    param_space={
        "train_loop_config": {
            "lr": tune.loguniform(1e-4, 1e-1),
            "batch_size": tune.choice([16, 32, 64]),
        },
    },
    tune_config=tune.TuneConfig(metric="loss", mode="min", num_samples=20),
)
results = tuner.fit()
```
