# Ray AIR (AI Runtime)

## Overview

Ray AI Runtime (AIR) is a unified toolkit for building end-to-end ML applications. It provides a consistent API across training, tuning, prediction, and serving.

## Architecture

```
+--------------------------------------------------+
|                   Ray AIR                        |
+----------+-----------+----------+----------------+
|          |           |          |                |
|  Train   |   Tune    |  Serve   |   Data        |
|          |           |          |                |
+----------+-----------+----------+----------------+
|          |           |          |                |
| Checkpoint Store     | BatchPred|  Preprocessor |
|          |           |          |                |
+----------+-----------+----------+----------------+
```

## Core Abstractions

### Checkpoint
```python
from ray.train import Checkpoint

# Create from directory
checkpoint = Checkpoint.from_directory("/path/to/checkpoint")

# Create from bytes
checkpoint = Checkpoint.from_bytes(b"checkpoint_data")

# Create from dictionary
checkpoint = Checkpoint.from_dict({
    "model_weights": weights,
    "optimizer_state": optimizer_state,
    "epoch": 10,
})

# Access checkpoint data
with checkpoint.as_directory() as path:
    model = load_model(path)

data = checkpoint.to_dict()
bytes_data = checkpoint.to_bytes()
```

### Checkpoint Operations
```python
# Get checkpoint metadata
path = checkpoint.path  # Local path (if from_directory)
metadata = checkpoint.get_metadata()

# Upload to external storage
checkpoint_uri = checkpoint.to_uri("s3://bucket/checkpoints/")
```

## BatchPredictor

### Creating a BatchPredictor
```python
from ray.train.batch_predictor import BatchPredictor
from ray.train.torch import TorchPredictor

# From checkpoint
predictor = BatchPredictor.from_checkpoint(
    checkpoint=checkpoint,
    predictor_cls=TorchPredictor,
    model=MyModelClass,
)

# From a trained result
predictor = BatchPredictor.from_checkpoint(
    result.checkpoint,
    TorchPredictor,
    model=MyModelClass,
)
```

### Running Batch Prediction
```python
# From a dataset
predictions = predictor.predict(
    data=test_dataset,
    feature_columns=["feature_1", "feature_2"],
    batch_size=1024,
    num_cpus_per_worker=2,
    num_gpus_per_worker=1,
    max_scoring_workers=4,
    min_scoring_workers=1,
    num_workers=4,
    separate_input_state=False,
)

# Predictions is a Dataset
results = predictions.to_pandas()
```

### BatchPredictor with ScalingConfig
```python
from ray.train import ScalingConfig

predictions = predictor.predict(
    data=test_dataset,
    scaling_config=ScalingConfig(
        num_workers=4,
        use_gpu=True,
        resources_per_worker={"CPU": 2, "GPU": 1},
    ),
)
```

## TorchPredictor

### Basic Usage
```python
from ray.train.torch import TorchPredictor
import torch

class MyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = torch.nn.Linear(10, 1)

    def forward(self, x):
        return self.fc(x)

predictor = TorchPredictor(
    model=MyModel(),
    # Optional: preprocess function
    preprocessor=my_preprocessor,
)
```

### From Checkpoint
```python
predictor = TorchPredictor.from_checkpoint(
    checkpoint=checkpoint,
    model=MyModel,
    # Optional arguments
    device="cuda",
)
```

### Predict Method
```python
# Single prediction
result = predictor.predict(
    data={"features": torch_tensor},
    # Optional feature columns
    feature_columns=["col1", "col2"],
)

# The predict method returns a dict with predictions
print(result)
# {"predictions": tensor([...])}
```

## Integrations

### Train → Tune → Serve Pipeline
```python
from ray.train import ScalingConfig, CheckpointConfig
from ray.train.torch import TorchTrainer
from ray.tune import Tuner
from ray.tune.schedulers import ASHAScheduler
from ray import serve

# Step 1: Define training function
def train_func(config):
    model = build_model(config)
    for epoch in range(config["epochs"]):
        loss = train_epoch(model, config)
        ray.train.report({"loss": loss}, checkpoint=Checkpoint.from_dict({"model": model.state_dict()}))

# Step 2: Create trainer
trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    train_loop_config={"lr": 0.01, "epochs": 10},
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
)

# Step 3: Tune hyperparameters
tuner = Tuner(
    trainer,
    param_space={
        "train_loop_config": {
            "lr": tune.loguniform(1e-4, 1e-1),
            "batch_size": tune.choice([16, 32, 64]),
        },
    },
    tune_config=tune.TuneConfig(
        metric="loss",
        mode="min",
        num_samples=10,
        scheduler=ASHAScheduler(max_t=10, grace_period=3),
    ),
    run_config=train.RunConfig(
        checkpoint_config=CheckpointConfig(
            checkpoint_score_attribute="loss",
            checkpoint_score_order="min",
        ),
    ),
)
results = tuner.fit()
best_checkpoint = results.get_best_result().checkpoint

# Step 4: Batch prediction
predictor = BatchPredictor.from_checkpoint(
    best_checkpoint,
    TorchPredictor,
    model=MyModel,
)
predictions = predictor.predict(test_dataset, batch_size=512)

# Step 5: Deploy to Serve
@serve.deployment
class ModelDeployment:
    def __init__(self, checkpoint):
        self.model = TorchPredictor.from_checkpoint(checkpoint, model=MyModel)

    async def __call__(self, request):
        data = await request.json()
        return self.model.predict(data)

app = ModelDeployment.bind(best_checkpoint)
serve.run(app)
```

## Predictor Classes

### TorchPredictor
```python
from ray.train.torch import TorchPredictor
# PyTorch models
```

### TfPredictor
```python
from ray.train.tensorflow import TfPredictor
# TensorFlow/Keras models
```

### HuggingFacePredictor
```python
from ray.train.huggingface import HuggingFacePredictor

predictor = HuggingFacePredictor(
    model=transformers.AutoModelForSequenceClassification.from_pretrained("bert-base"),
    tokenizer=transformers.AutoTokenizer.from_pretrained("bert-base"),
)
```

### XGBoostPredictor
```python
from ray.train.xgboost import XGBoostPredictor

predictor = XGBoostPredictor(
    model=xgb_model,
    feature_columns=["col1", "col2", "col3"],
)
```

### LightGBMPredictor
```python
from ray.train.lightgbm import LightGBMPredictor

predictor = LightGBMPredictor(
    model=lgb_model,
    feature_columns=["col1", "col2", "col3"],
)
```

## Preprocessors

### Built-in Preprocessors
```python
from ray.data.preprocessors import *

# Standard Scaling
scaler = StandardScaler(columns=["feature_1", "feature_2"])

# Min-Max Scaling
scaler = MinMaxScaler(columns=["feature_1"])

# Max Absolute Scaling
scaler = MaxAbsScaler(columns=["feature_1"])

# One-Hot Encoding
encoder = OneHotEncoder(columns=["category_col"])

# Label Encoding
encoder = LabelEncoder(columns=["category_col"])

# Ordinal Encoding
encoder = OrdinalEncoder(columns=["ordinal_col"])

# Simple Imputer
imputer = SimpleImputer(columns=["feature_1"], strategy="mean")

# Chain Preprocessors
from ray.data.preprocessors import Chain
chain = Chain(scaler, encoder)

# HashingVectorizer
vectorizer = HashingVectorizer(columns=["text_col"], num_features=1000)

# TF-IDF Vectorizer
vectorizer = TfidfVectorizer(columns=["text_col"])

# Tokenizer
tokenizer = Tokenizer(columns=["text_col"])

# Batch Mapper
mapper = BatchMapper(fn=my_transform_fn, batch_format="pandas")
```

### Using Preprocessors with Trainers
```python
trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    datasets={"train": train_dataset, "valid": valid_dataset},
    preprocessor=scaler,  # Preprocessor applied before training
    scaling_config=ScalingConfig(num_workers=4),
)
```

### Preprocessor Fit & Transform
```python
# Fit on training data
scaler.fit(train_dataset)

# Transform datasets
train_transformed = scaler.transform(train_dataset)
test_transformed = scaler.transform(test_dataset)

# Fit and transform in one step
train_transformed = scaler.fit_transform(train_dataset)
```

## Session API

### Inside Training Loop
```python
import ray.train

def train_func(config):
    # Get distributed training info
    rank = ray.train.get_context().get_world_rank()
    world_size = ray.train.get_context().get_world_size()
    local_rank = ray.train.get_context().get_local_rank()

    # Get dataset shard
    shard = ray.train.get_dataset_shard("train")

    # Report metrics and checkpoint
    for epoch in range(config["epochs"]):
        loss = train_one_epoch(model, shard)
        ray.train.report(
            {"loss": loss, "epoch": epoch},
            checkpoint=Checkpoint.from_dict({"model": model.state_dict()}),
        )

    # Get checkpoint
    checkpoint = ray.train.get_checkpoint()
    if checkpoint:
        state = checkpoint.to_dict()
        model.load_state_dict(state["model"])
```

## Best Practices

1. **Use Checkpoint** for model persistence across Train/Tune/Serve
2. **Use BatchPredictor** for offline prediction on datasets
3. **Chain preprocessors** for complex feature engineering
4. **Use ScalingConfig** for consistent resource configuration
5. **Integrate with Tune** for hyperparameter optimization before deployment
6. **Use the Session API** for distributed training coordination
7. **Save checkpoints regularly** during training for fault tolerance
8. **Test end-to-end pipeline** from data ingestion to serving
9. **Use appropriate Predictor class** for your framework
10. **Monitor training metrics** via Ray dashboard or logging integrations
