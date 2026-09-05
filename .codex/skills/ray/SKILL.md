---
name: ray
description: >
  Comprehensive reference documentation and skill for Ray - a unified framework for scaling AI and Python applications.
  Covers Ray Core (tasks, actors, objects, scheduling, placement groups, namespaces, runtime environment,
  fault tolerance, compiled graphs, direct transport), Ray Data (datasets, transformations, datasources,
  preprocessors, execution engine, streaming), Ray Serve (model serving, deployments, HTTP handling,
  autoscaling, model composition, multi-app, multiplexing, monitoring, architecture), Ray Train (distributed
  training with PyTorch, TensorFlow, HuggingFace, XGBoost, LightGBM, Horovod, DeepSpeed, JAX; scaling config,
  checkpointing, training iterators, collective operations), Ray Tune (hyperparameter tuning, search algorithms,
  schedulers, analysis, logging, stoppers, trainables, CLI, experiment execution), Ray RLlib (reinforcement
  learning algorithms, RL modules, learners, environments, connectors, replay buffers, callbacks, multi-agent,
  offline training, fault tolerance), Ray Cluster (setup, configuration, autoscaling, cloud providers AWS/GCP/Azure,
  KubeRay, job submission, runtime environments, observability, dashboard, security, governance), Ray DAG
  (directed acyclic graphs, compiled graphs, DAG execution), Ray AIR (AI runtime, batch prediction,
  checkpoints, integrations), Ray Workflow (workflow orchestration, durable execution), Ray LLM (LLM serving
  integration), Ray Client (remote cluster connection), Ray utility modules (placement groups, scheduling
  strategies, state API, tracing, collective operations), and Ray internal architecture (GCS, raylet, worker,
  object store, plasma, memory management). Based on Ray source code analysis.
version: 2.47.0
---

# Ray - Unified Framework for Scaling AI and Python Applications

## Execution routing and blocking rules

Ray is preferred for formal, distributed, long-running, resource-intensive, or recoverable workloads, but it
is not mandatory for every quick local check. Use local execution for read-only inspection, configuration
validation, bounded smoke tests, and low-risk experiments that are expected to finish quickly. Use the
project's declared Ray Job path for formal Trials/Champions and durable MLflow or competition evidence.

Before execution, verify the project layout, fixed entrypoint, dependencies, runtime environment/release,
data identity, split identity, and resource declaration. If any contract is missing, stale, or inconsistent,
block the run and repair it; do not substitute an ad-hoc local command or bypass the failed preflight. A local
result must never be labeled as a governed Ray Job result or final evidence.

## Overview

Ray is an open-source unified framework for scaling AI and Python applications. It provides a simple, universal API for building distributed applications, enabling parallel processing of compute-heavy workloads across clusters of machines. Ray powers some of the most complex and demanding AI workloads in production.

**Key Capabilities:**
- **Ray Core**: Distributed computing primitives - tasks, actors, objects, and scheduling
- **Ray Data**: Scalable data loading, transformation, and processing
- **Ray Train**: Distributed model training with framework-agnostic APIs
- **Ray Tune**: Scalable hyperparameter tuning with state-of-the-art algorithms
- **Ray Serve**: Scalable model serving with composition and autoscaling
- **Ray RLlib**: Industry-grade reinforcement learning library
- **Ray Cluster**: Multi-node cluster management with autoscaling
- **Ray Workflow**: Long-running, durable workflow execution
- **Ray DAG**: Directed acyclic graph execution and compiled graphs
- **Ray AIR**: Unified AI runtime for end-to-end ML workflows
- **Ray Client**: Remote cluster connection and execution
- **Ray LLM**: LLM serving and deployment integration

**Supported Languages**: Python, Java, C++ (cross-language support)

**Ray Version:** 2.47.0 | **Python:** 3.9+ | **License:** Apache 2.0

## Architecture Overview

```
+------------------------------------------------------------------+
|                     Application Layer                              |
|  Ray Train  |  Ray Tune  |  Ray Serve  |  Ray RLlib  |  Ray Data  |
+------------------------------------------------------------------+
|                      Ray AIR (AI Runtime)                          |
|  Checkpoints  |  Batch Prediction  |  Integrations  |  Metrics    |
+------------------------------------------------------------------+
|                      Ray Core                                      |
|  Tasks  |  Actors  |  Objects  |  Scheduling  |  Placement Groups |
|  Namespaces  |  Runtime Env  |  Fault Tolerance  |  DAGs           |
+------------------------------------------------------------------+
|                    Cluster Management                              |
|  GCS (Global Control Service)  |  Autoscaler  |  Job Submission   |
|  Ray Dashboard  |  KubeRay  |  Ray Client  |  Runtime Env        |
+------------------------------------------------------------------+
|                    Distributed Runtime                              |
|  Raylet (per-node)  |  Worker Processes  |  Object Store (Plasma) |
|  gRPC Communication  |  Memory Management  |  Resource Isolation  |
+------------------------------------------------------------------+
|                    Infrastructure                                   |
|  AWS  |  GCP  |  Azure  |  Kubernetes  |  On-Premise  |  Local   |
+------------------------------------------------------------------+
```

## Quick Reference

### Initialization & Shutdown
```python
import ray

# Initialize Ray
ray.init()                                    # Local cluster
ray.init(address="auto")                      # Connect to existing cluster
ray.init(address="ray://cluster:10001")       # Ray Client
ray.init(num_cpus=8, num_gpus=2,              # Resource specification
         object_store_memory=10**9,
         dashboard_host="0.0.0.0",
         dashboard_port=8265,
         namespace="my_app",
         runtime_env={"pip": ["requests"]})

# Shutdown
ray.shutdown()
ray.is_initialized()  # Check if initialized
```

### Tasks (Remote Functions)
```python
@ray.remote
def my_function(x):
    return x * 2

# Execute remote
result_ref = my_function.remote(42)
result = ray.get(result_ref)  # Retrieve result

# Multiple returns
@ray.remote(num_returns=3)
def return_three():
    return 1, 2, 3
refs = return_three.remote()

# Options
result = my_function.options(
    num_cpus=2, num_gpus=1,
    resources={"TPU": 4},
    memory=2**31,
    max_retries=3,
    retry_exceptions=True,
    scheduling_strategy="SPREAD",
    name="my_task",
    runtime_env={"pip": ["numpy"]}
).remote(42)

# Batch execution
results = ray.get([my_function.remote(i) for i in range(100)])

# Streaming generators
@ray.remote(num_returns="streaming")
def generate_data(n):
    for i in range(n):
        yield i

gen = generate_data.remote(10)
for ref in gen:
    print(ray.get(ref))
```

### Actors
```python
@ray.remote
class Counter:
    def __init__(self, initial=0):
        self.count = initial

    def increment(self, n=1):
        self.count += n
        return self.count

    def get_count(self):
        return self.count

# Create actor
counter = Counter.remote(0)

# Call methods
count_ref = counter.increment.remote(5)
count = ray.get(count_ref)

# Actor options
counter = Counter.options(
    num_cpus=2, num_gpus=1,
    memory=2**31,
    max_restarts=3,
    max_task_retries=2,
    max_concurrency=10,
    name="my_counter",
    namespace="my_app",
    lifetime="detached",
    scheduling_strategy="SPREAD"
).remote(0)

# Named actors (detached)
counter = ray.get_actor("my_counter", namespace="my_app")

# Kill actor
ray.kill(counter)

# Async actors
@ray.remote
class AsyncActor:
    async def slow_method(self):
        await asyncio.sleep(1)
        return "done"

# Threaded actors
@ray.remote(max_concurrency=4)
class ThreadedActor:
    def method(self):
        pass

# Concurrency groups
@ray.remote(concurrency_groups={"io": 2, "compute": 4})
class GroupedActor:
    @ray.method(concurrency_group="io")
    def io_method(self):
        pass

    @ray.method(concurrency_group="compute")
    def compute_method(self):
        pass
```

### Objects
```python
# Put objects in object store
ref = ray.put(42)
value = ray.get(ref)

# Get multiple
values = ray.get([ref1, ref2, ref3])

# Get with timeout
value = ray.get(ref, timeout=5.0)

# Wait for objects
ready, remaining = ray.wait(
    [ref1, ref2, ref3],
    num_returns=2,
    timeout=10.0
)
```

### Placement Groups
```python
from ray.util.placement_group import placement_group

pg = placement_group(
    bundles=[{"CPU": 2, "GPU": 1}, {"CPU": 2}],
    strategy="STRICT_SPREAD"
)
ray.get(pg.ready())

@ray.remote
def task():
    pass

task.options(placement_group=pg,
             placement_group_bundle_index=0).remote()
```

### Runtime Environment
```python
ray.init(runtime_env={
    "pip": ["numpy==1.24.0", "pandas"],
    "conda": {"dependencies": ["scipy"]},
    "env_vars": {"OMP_NUM_THREADS": "4"},
    "working_dir": "./my_project",
    "py_modules": ["./my_module"],
    "working_dir": "s3://bucket/path",
    "images": {"my_image": "docker.io/myimage:latest"},
})

@ray.remote(runtime_env={"pip": ["torch"]})
def train():
    pass
```

## Core API Reference

### ray.init()
```python
ray.init(
    address: Optional[str] = None,        # Cluster address or "auto"
    num_cpus: Optional[int] = None,       # Number of CPUs
    num_gpus: Optional[int] = None,       # Number of GPUs
    resources: Optional[Dict] = None,     # Custom resources
    object_store_memory: Optional[int] = None,  # Object store bytes
    dashboard_host: str = "127.0.0.1",    # Dashboard bind host
    dashboard_port: Optional[int] = None, # Dashboard port
    dashboard_agent_listen_port: Optional[int] = None,
    ignore_reinit_error: bool = False,
    namespace: Optional[str] = None,      # Ray namespace
    runtime_env: Optional[Dict] = None,   # Runtime environment
    storage: Optional[str] = None,        # Storage URI
    job_config: Optional[JobConfig] = None,
    log_to_driver: bool = True,
    enable_object_reconstruction: bool = False,
    _node_ip_address: Optional[str] = None,
    _driver_object_store_memory: Optional[int] = None,
    _system_config: Optional[Dict] = None,
    _ plasma_directory: Optional[str] = None,
    _temp_dir: Optional[str] = None,
    **kwargs,
) -> dict
```

### ray.remote()
```python
@ray.remote(
    num_cpus: Optional[int] = None,       # CPUs per task/actor
    num_gpus: Optional[int] = None,       # GPUs per task/actor
    memory: Optional[int] = None,         # Memory in bytes
    object_store_memory: Optional[int] = None,  # Actor object store
    resources: Optional[Dict] = None,     # Custom resources
    accelerator_type: Optional[str] = None,  # Accelerator type
    label_selector: Optional[Dict] = None,   # Node label selector
    num_returns: Optional[Union[int, str]] = None,  # Return values
    max_calls: Optional[int] = None,      # Max calls per worker
    max_retries: Optional[int] = None,    # Max retries (tasks)
    retry_exceptions: Optional[Union[bool, list]] = None,
    concurrency_groups: Optional[Dict] = None,  # Actor concurrency
    max_concurrency: Optional[int] = None,  # Actor max concurrency
    max_restarts: Optional[int] = None,   # Actor restarts
    max_task_retries: Optional[int] = None,  # Actor task retries
    max_pending_calls: Optional[int] = None,  # Pending call limit
    allow_out_of_order_execution: Optional[bool] = None,
    name: Optional[str] = None,
    namespace: Optional[str] = None,
    lifetime: Optional[str] = None,       # "detached" or "non_detached"
    runtime_env: Optional[Dict] = None,
    scheduling_strategy: Optional[Union[str, SchedulingStrategy]] = None,
    enable_task_events: Optional[bool] = None,
    _labels: Optional[Dict] = None,
)
def my_function():
    pass
```

### ray.get()
```python
ray.get(
    object_refs: Union[ObjectRef, List[ObjectRef]],
    timeout: Optional[float] = None,
) -> Any
```

### ray.wait()
```python
ray.wait(
    object_refs: List[ObjectRef],
    num_returns: int = 1,
    timeout: Optional[float] = None,
    fetch_local: bool = True,
) -> Tuple[List[ObjectRef], List[ObjectRef]]
```

### ray.put()
```python
ray.put(value: Any, _owner: Optional[ActorHandle] = None) -> ObjectRef
```

### ray.cancel()
```python
ray.cancel(
    object_ref: ObjectRef,
    force: bool = False,
    recursive: bool = True,
)
```

### ray.kill()
```python
ray.kill(actor: ActorHandle, no_restart: bool = True)
```

### ray.get_actor()
```python
ray.get_actor(
    name: str,
    namespace: Optional[str] = None,
) -> ActorHandle
```

### Scheduling Strategies
```python
from ray.util.scheduling_strategies import (
    PlacementGroupSchedulingStrategy,
    NodeAffinitySchedulingStrategy,
    NodeLabelSchedulingStrategy,
)

# Placement group scheduling
PlacementGroupSchedulingStrategy(
    placement_group=pg,
    placement_group_bundle_index=0,
    placement_group_capture_child_tasks=True,
)

# Node affinity
NodeAffinitySchedulingStrategy(
    node_id=node_id,
    soft=False,  # Soft constraint
)

# Label-based scheduling
NodeLabelSchedulingStrategy(
    hard={"region": "us-west"},
    soft={"zone": "1a"},
)
```

## Exception Types

| Exception | Description |
|-----------|-------------|
| `RayError` | Base class for all Ray exceptions |
| `RayTaskError` | Task threw an exception during execution |
| `RayActorError` | Actor died unexpectedly |
| `ActorDiedError` | Actor process died |
| `ActorUnavailableError` | Actor temporarily unavailable |
| `RaySystemError` | Ray encountered a system error |
| `WorkerCrashedError` | Worker died unexpectedly |
| `LocalRayletDiedError` | Local raylet died |
| `ObjectStoreFullError` | Object store is full |
| `ObjectLostError` | Object lost from distributed memory |
| `ObjectFetchTimedOutError` | Object fetch timed out |
| `OwnerDiedError` | Object owner died |
| `ObjectReconstructionFailedError` | Object reconstruction failed |
| `GetTimeoutError` | ray.get() timed out |
| `TaskCancelledError` | Task was cancelled |
| `RuntimeEnvSetupError` | Runtime env setup failed |
| `TaskPlacementGroupRemoved` | Placement group removed |
| `ActorPlacementGroupRemoved` | Actor placement group removed |
| `PendingCallsLimitExceeded` | Pending calls exceeded limit |
| `TaskUnschedulableError` | Task cannot be scheduled |
| `ActorUnschedulableError` | Actor cannot be scheduled |
| `AuthenticationError` | Authentication error |
| `OutOfMemoryError` | Node out of memory |
| `OutOfDiskError` | Local disk full |
| `CrossLanguageError` | Exception from another language |
| `AsyncioActorExit` | Asyncio actor exited intentionally |
| `RayChannelError` | Compiled graph channel error |
| `RayChannelTimeoutError` | Channel operation timed out |
| `RayCgraphCapacityExceeded` | Compiled graph buffer full |
| `RayDirectTransportError` | Direct transport error |
| `UnserializableException` | Exception deserialization failed |
| `ActorAlreadyExistsError` | Named actor already exists |
| `ActorHandleNotFoundError` | Actor handle not found |

## Ray Data

### Creating Datasets
```python
import ray.data as rd

# From Python objects
ds = rd.from_items([1, 2, 3, 4, 5])
ds = rd.from_pandas(pd.DataFrame({"a": [1, 2, 3]}))
ds = rd.from_numpy(np.array([1, 2, 3]))
ds = rd.from_arrow(pa.table({"a": [1, 2, 3]}))

# From files
ds = rd.read_parquet("s3://bucket/data/")
ds = rd.read_json("path/to/json/")
ds = rd.read_csv("path/to/csv/")
ds = rd.read_text("path/to/text/")
ds = rd.read_binary("path/to/bin/")
ds = rd.read_images("path/to/images/")
ds = rd.read_tfrecords("path/to/tfrecords/")
ds = rd.read_webdataset("path/to/wds/")
ds = rd.read_numpy("path/to/numpy/")
ds = rd.read_parquet_bulk("path/to/parquet/")

# From datasources
ds = rd.read_datasource(
    MyCustomDatasource(),
    parallelism=100,
)

# Range
ds = rd.range(1000)
```

### Transformations
```python
# Map
ds = ds.map(lambda row: {"x": row["a"] * 2})
ds = ds.map_batches(lambda df: df * 2, batch_size=256)
ds = ds.flat_map(lambda row: [{"x": v} for v in row["a"]])

# Filter
ds = ds.filter(lambda row: row["x"] > 0)

# Aggregation
ds = ds.sum("x")
ds = ds.mean("x")
ds = ds.min("x")
ds = ds.max("x")
ds = ds.count()

# GroupBy
ds = ds.groupby("key").mean("value")
ds = ds.groupby("key").aggregate(lambda g: g.sum())

# Sorting
ds = ds.sort("column")
ds = ds.sort(["col1", "col2"])

# Repartitioning
ds = ds.repartition(100)
ds = ds.random_shuffle()
ds = ds.randomize_block_order()
ds = ds.split_at_indices([100, 200])
ds = ds.split(n=3, equal=True)
ds = ds.union([ds2, ds3])

# Joining
ds = ds.join(ds2, on="key", how="inner")
ds1.join(ds2, on="key", how="left")
ds1.join(ds2, on="key", how="right")
ds1.join(ds2, on="key", how="outer")

# Streaming
ds = ds.iter_rows()
ds = ds.iter_batches()
ds = ds.iter_batches(batch_size=128)

# Write
ds.write_parquet("path/to/output/")
ds.write_json("path/to/output/")
ds.write_csv("path/to/output/")
ds.write_tfrecords("path/to/output/")
ds.write_numpy("path/to/output/")
ds.write_datasource(MyDatasource())
```

### Preprocessors
```python
from ray.data.preprocessors import (
    StandardScaler, MinMaxScaler, MaxAbsScaler,
    Normalizer, BatchMapper, Chain, SimpleImputer,
    OneHotEncoder, OrdinalEncoder, LabelEncoder,
    Tokenizer, HashingVectorizer, CountVectorizer,
    TFIDFVectorizer, PowerTransformer, QuantileTransformer,
    RobustScaler, ImageSimpleAugmentations,
)

scaler = StandardScaler(columns=["feature1", "feature2"])
scaler.fit(ds)
ds = scaler.transform(ds)

chain = Chain(
    SimpleImputer(columns=["a"]),
    StandardScaler(columns=["a"]),
)
```

## Ray Serve

### Basic Deployment
```python
from ray import serve

@serve.deployment
class MyModel:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    async def __call__(self, request):
        data = await request.json()
        result = self.model.predict(data)
        return {"result": result}

# Deploy
app = MyModel.bind("/path/to/model")
serve.run(app)

# HTTP query
import requests
response = requests.post("http://localhost:8000/MyModel", json={"input": [1, 2, 3]})
```

### Deployment Options
```python
@serve.deployment(
    num_replicas=4,
    ray_actor_options={
        "num_cpus": 2,
        "num_gpus": 1,
        "memory": 2**31,
    },
    autoscaling_config=serve.config.AutoscalingConfig(
        min_replicas=1,
        max_replicas=10,
        target_num_ongoing_requests_per_replica=5,
    ),
    user_config={"param": "value"},
    max_concurrent_queries=100,
    route_prefix="/predict",
    health_check_period_s=10,
    health_check_timeout_s=30,
    graceful_shutdown_timeout_s=20,
    graceful_shutdown_wait_loop_s=2,
)
class Model:
    pass
```

### Model Composition
```python
@serve.deployment
class Preprocessor:
    async def __call__(self, request):
        data = await request.json()
        return preprocess(data)

@serve.deployment
class Model:
    def __init__(self, preprocessor):
        self.preprocessor = preprocessor

    async def __call__(self, request):
        processed = await self.preprocessor(request)
        return predict(processed)

app = Model.bind(Preprocessor.bind())
serve.run(app)
```

### Serve REST API
```python
# Deployed applications expose HTTP endpoints
# GET  / serve app info
# POST /<deployment_name>  invoke deployment
# GET  /-/healthz  health check
```

## Ray Train

### PyTorch Training
```python
from ray.train import ScalingConfig
from ray.train.torch import TorchTrainer

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
        ray.train.report({"loss": loss.item(), "epoch": epoch})

trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    train_loop_config={"lr": 0.01, "epochs": 10},
    scaling_config=ScalingConfig(
        num_workers=4,
        use_gpu=True,
        resources_per_worker={"CPU": 2, "GPU": 1},
    ),
    datasets={"train": train_dataset},
)
result = trainer.fit()
```

### TensorFlow Training
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
result = trainer.fit()
```

### HuggingFace Training
```python
from ray.train.huggingface import HuggingFaceTrainer

def train_func(config):
    from transformers import Trainer, TrainingArguments
    training_args = TrainingArguments(
        output_dir=".",
        num_train_epochs=config["epochs"],
        per_device_train_batch_size=config["batch_size"],
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
    )
    trainer.train()

trainer = HuggingFaceTrainer(
    train_loop_per_worker=train_func,
    train_loop_config={"epochs": 3, "batch_size": 16},
    scaling_config=ScalingConfig(num_workers=4, use_gpu=True),
)
```

### ScalingConfig
```python
from ray.train import ScalingConfig

ScalingConfig(
    num_workers: int = 1,                   # Number of training workers
    use_gpu: bool = False,                  # Whether to use GPUs
    use_tpu: bool = False,                  # Whether to use TPUs
    resources_per_worker: Optional[Dict] = None,  # Custom resources
    placement_strategy: str = "PACK",       # PACK, SPREAD, STRICT_PACK, STRICT_SPREAD
    accelerator_type: Optional[str] = None, # Accelerator type
    trainer_resources: Optional[Dict] = None,  # Resources for trainer
)
```

### Checkpointing
```python
from ray.train import Checkpoint

# Save checkpoint
ray.train.save_checkpoint(
    epoch=epoch,
    model_state_dict=model.state_dict(),
    optimizer_state_dict=optimizer.state_dict(),
)

# Report metrics with checkpoint
ray.train.report(
    {"loss": loss.item()},
    checkpoint=Checkpoint.from_directory("/tmp/checkpoint"),
)

# Load checkpoint
checkpoint = ray.train.get_checkpoint()
if checkpoint:
    with checkpoint.as_directory() as dir:
        model.load_state_dict(torch.load(f"{dir}/model.pt"))
```

## Ray Tune

### Basic Usage
```python
from ray import tune
from ray.tune import Tuner

def trainable(config):
    for epoch in range(10):
        score = objective(config, epoch)
        tune.report({"score": score, "epoch": epoch})

tuner = tune.Tuner(
    trainable,
    param_space={
        "lr": tune.loguniform(1e-4, 1e-1),
        "batch_size": tune.choice([16, 32, 64]),
        "layers": tune.randint(1, 5),
    },
    tune_config=tune.TuneConfig(
        metric="score",
        mode="max",
        num_samples=50,
    ),
)
results = tuner.fit()
best_result = results.get_best_result()
```

### Search Spaces
```python
tune.uniform(-5, 5)              # Uniform float
tune.quniform(-5, 5, q=0.5)     # Quantized uniform
tune.loguniform(1e-4, 1e-1)     # Log-uniform
tune.qlouniform(1e-4, 1e-1, q=1e-5)
tune.choice([1, 2, 3])          # Categorical
tune.randint(1, 100)            # Random integer
tune.qrandint(1, 100, q=5)      # Quantized integer
tune.randn(0, 1)                # Normal distribution
tune.qrandn(0, 1, q=0.1)        # Quantized normal
tune.grid_search([1, 2, 3])     # Grid search
```

### Search Algorithms
```python
from ray.tune.search import (
    BasicVariantGenerator,    # Default grid/random
)
from ray.tune.search.optuna import OptunaSearch
from ray.tune.search.hyperopt import HyperOptSearch
from ray.tune.search.bayesopt import BayesOptSearch
from ray.tune.search.flaml import CFO, BlendSearch
from ray.tune.search.bohb import TuneBOHB
from ray.tune.search.nevergrad import NevergradSearch
from ray.tune.search.zoopt import ZOOptSearch
from ray.tune.search.sigopt import SigOptSearch
from ray.tune.search.hebo import HEBOSearch

tune_config = tune.TuneConfig(
    search_alg=OptunaSearch(),
    # or
    search_alg=HyperOptSearch(metric="score", mode="max"),
)
```

### Schedulers
```python
from ray.tune.schedulers import (
    ASHAScheduler,              # Asynchronous Successive Halving
    HyperBandScheduler,         # HyperBand
    MedianStoppingRule,         # Median stopping
    HyperBandForBOHB,           # HyperBand for BOHB
    FIFOScheduler,              # FIFO (default)
    PopulationBasedTraining,    # PBT
    PopulationBasedTrainingReplay,
)

tune_config = tune.TuneConfig(
    scheduler=ASHAScheduler(
        max_t=100,
        grace_period=10,
        reduction_factor=3,
    ),
)
```

### Tuner API
```python
from ray.tune import Tuner

tuner = Tuner(
    trainable,
    run_config=train.RunConfig(
        name="my_experiment",
        storage_path="/tmp/tune_results",
        stop={"training_iteration": 100},
        checkpoint_config=train.CheckpointConfig(
            checkpoint_score_attribute="score",
            checkpoint_score_order="max",
            checkpoint_frequency=5,
        ),
        failure_config=train.FailureConfig(
            max_failures=3,
            fail_fast=False,
        ),
        verbose=1,
    ),
    param_space={...},
    tune_config=tune.TuneConfig(
        metric="score",
        mode="max",
        num_samples=50,
        search_alg=OptunaSearch(),
        scheduler=ASHAScheduler(),
    ),
)
results = tuner.fit()
```

## Ray RLlib

### Quick Start
```python
from ray.rllib.algorithms.ppo import PPOConfig

config = (
    PPOConfig()
    .environment("CartPole-v1")
    .framework("torch")
    .rollouts(num_rollout_workers=4)
    .training(
        lr=3e-4,
        gamma=0.99,
        train_batch_size=4000,
        sgd_minibatch_size=128,
        num_sgd_iter=30,
    )
    .resources(num_gpus=1)
)

algo = config.build()

for i in range(100):
    result = algo.train()
    print(f"Iteration {i}: reward={result['episode_reward_mean']}")

algo.save("checkpoint")
algo = config.build()
algo.restore("checkpoint")
```

### Available Algorithms
- **PPO** - Proximal Policy Optimization
- **APPO** - Asynchronous PPO
- **DDPG** - Deep Deterministic Policy Gradient
- **DQN** - Deep Q-Network
- **A3C** - Asynchronous Advantage Actor-Critic
- **A2C** - Advantage Actor-Critic
- **IMPALA** - Importance Weighted Actor-Learner Architecture
- **SAC** - Soft Actor-Critic
- **TD3** - Twin Delayed DDPG
- **ES** - Evolution Strategies
- **ARS** - Augmented Random Search
- **PG** - Policy Gradient
- **MARWIL** - Multi-Agent Offline RL
- **CQL** - Conservative Q-Learning
- **BC** - Behavioral Cloning
- **DREAM** - DREAM for Offline RL
- **Decision Transformer** - DT for Offline RL

### RL Modules API (New API Stack)
```python
from ray.rllib.core import RLModuleSpec
from ray.rllib.algorithms.ppo import PPOConfig

config = (
    PPOConfig()
    .environment("CartPole-v1")
    .training(
        rl_module_spec=RLModuleSpec(
            module_class=MyCustomRLModule,
            model_config_dict={"hidden_dim": 256},
        ),
    )
)
```

### Multi-Agent
```python
config = (
    PPOConfig()
    .environment(env="multi_agent_env")
    .multi_agent(
        policies={
            "policy_1": (None, obs_space, act_space, {}),
            "policy_2": (None, obs_space, act_space, {}),
        },
        policy_mapping_fn=lambda agent_id, episode, **kw: "policy_1",
    )
)
```

## Ray Cluster

### Starting a Cluster
```bash
# Head node
ray start --head --port=6379 --dashboard-host=0.0.0.0 --dashboard-port=8265 \
    --num-cpus=8 --num-gpus=4 --object-store-memory=1000000000

# Worker node
ray start --address=<head-ip>:6379 --num-cpus=8 --num-gpus=4

# Stop
ray stop

# Status
ray status
```

### Cluster Configuration (YAML)
```yaml
cluster_name: my-cluster
max_workers: 10
upscaling_speed: 2
idle_timeout_minutes: 5

provider:
    type: aws
    region: us-west-2
    availability_zone: us-west-2a

auth:
    ssh_user: ubuntu
    ssh_private_key: ~/.ssh/id_rsa

available_node_types:
    ray.head.default:
        resources: {"CPU": 4}
        node_config:
            InstanceType: m5.xlarge
    ray.worker.default:
        min_workers: 2
        max_workers: 10
        resources: {"CPU": 8, "GPU": 1}
        node_config:
            InstanceType: p3.2xlarge

head_node_type: ray.head.default
```

### Autoscaler
```python
from ray.autoscaler.sdk import (
    request_cluster_resources,
    get_cluster_resources,
)
```

### Job Submission
```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://<head-ip>:8265")

job_id = client.submit_job(
    entrypoint="python train.py --epochs 10",
    runtime_env={
        "pip": ["torch", "transformers"],
        "working_dir": "./",
    },
    submission_id="my-job-1",
)

# Monitor
job_status = client.get_job_status(job_id)
job_logs = client.get_job_logs(job_id)

# List jobs
jobs = client.list_jobs()
```

### CLI Job Submission
```bash
ray job submit --address=http://<head-ip>:8265 \
    --runtime-env-json='{"pip": ["torch"]}' \
    -- python train.py

ray job status <job_id>
ray job logs <job_id>
ray job stop <job_id>
ray job list
ray job delete <job_id>
```

## Ray Dashboard

Accessible at `http://<head-ip>:8265`:

- **Overview**: Cluster state, resource usage, active jobs
- **Jobs**: Running/completed jobs with logs
- **Actors**: Actor lifecycle and state
- **Tasks**: Task execution timeline and metrics
- **Objects**: Object store usage
- **Nodes**: Node health and resources
- **Logs**: Centralized log viewer
- **Metrics**: Prometheus metrics dashboard
- **Serve**: Serve deployment status
- **Data**: Dataset statistics

## Ray Workflow

```python
import ray
from ray import workflow

@workflow.step
def step1(x):
    return x * 2

@workflow.step
def step2(x):
    return x + 1

@workflow.step
def combine(*args):
    return sum(args)

# Define workflow
dag = combine.step(step1.step(1), step2.step(2))

# Execute
result = dag.run()

# With checkpointing
result = dag.run(workflow_id="my_workflow")

# Resume after failure
result = workflow.resume(workflow_id="my_workflow")
```

## Ray DAG & Compiled Graphs

```python
import ray
from ray.dag import InputNode

@ray.remote
def process(x):
    return x * 2

@ray.remote
class Model:
    def predict(self, x):
        return x + 1

# Build DAG
with InputNode() as inp:
    a = process.bind(inp)
    model = Model.bind()
    b = model.predict.bind(a)

dag = b

# Execute DAG
result = ray.get(dag.execute(42))

# Compiled Graph (optimized execution)
compiled_graph = dag.experimental_compile()
result = ray.get(compiled_graph.execute(42))
```

## Ray AIR

```python
from ray.air import session, RunConfig
from ray.air.config import ScalingConfig, CheckpointConfig, FailureConfig

# Inside training function
def train_func(config):
    for epoch in range(10):
        loss = train_one_epoch(config)
        session.report(
            {"loss": loss, "epoch": epoch},
            checkpoint=ray.train.Checkpoint.from_directory(f"/tmp/ckpt_{epoch}"),
        )

# Preprocessors
from ray.data.preprocessors import StandardScaler
preprocessor = StandardScaler(columns=["feature1"])

# Batch prediction
from ray.train.batch_predictor import BatchPredictor
predictor = BatchPredictor.from_checkpoint(
    checkpoint,
    MyPredictorClass,
)
predictions = predictor.predict(test_dataset)
```

## Utility Modules

### State API
```python
from ray.util.state import (
    list_tasks, list_actors, list_objects,
    list_nodes, list_jobs, list_placement_groups,
    get_task, get_actor, get_object, get_node,
)

# List resources
tasks = list_tasks(detail=True, filters=[("name", "=", "train")])
actors = list_actors(detail=True, filters=[("state", "=", "ALIVE")])
nodes = list_nodes()

# Get specific resource
task = get_task(task_id)
actor = get_actor(actor_id)
```

### Collective Operations
```python
from ray.experimental.collective import (
    create_collective_group,
    allreduce, allgather, broadcast, reduce, sendsend,
)

# Create collective group
create_collective_group([actor1, actor2, actor3], backend="nccl")
```

### GPU Utilities
```python
ray.get_gpu_ids()                # Get GPU IDs for this worker
ray.available_resources()        # Available resources
ray.cluster_resources()          # Total cluster resources
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `RAY_address` | Cluster address |
| `RAY_namespace` | Default namespace |
| `RAY_job_id` | Job ID |
| `RAY_RUNTIME_ENV` | Runtime env JSON |
| `RAY_record_ref_creation_sites` | Track ObjectRef creation |
| `RAY_BACKEND_LOG_LEVEL` | Backend log level |
| `RAY_DEBUG` | Enable debug mode |
| `RAY_DISABLE_MEMORY_MONITOR` | Disable memory monitor |
| `RAY_memory_monitor_refresh_ms` | Memory monitor interval |
| `RAY_graceful_shutdown_timeout_s` | Graceful shutdown timeout |
| `RAY_SERVE_ENABLE_EXPERIMENTAL_STREAMING` | Enable serve streaming |
| `RAY_max_lineage_bytes` | Max lineage bytes |
| `RAY_object_spilling_config` | Object spilling config |
| `RAY_plasma_directory` | Plasma store directory |
| `RAY AuthService` | Authentication mode |
| `RAY_auth_token` | Auth token |
| `RAY_auth_token_path` | Auth token file path |
| `RAY_LOG_TO_STDERR` | Log to stderr |

## System Configuration

```python
ray.init(_system_config={
    "max_direct_call_object_size": 1024 * 1024,
    "task_retry_delay_ms": 500,
    "object_timeout_milliseconds": 30000,
    "num_heartbeats_timeout": 30,
    "heartbeat_timeout_milliseconds": 10000,
    "object_store_full_delay_ms": 100,
    "max_tasks_in_flight_per_worker": 100,
    "object_spilling_config": json.dumps({
        "type": "filesystem",
        "params": {"directory_path": "/tmp/spill"},
    }),
    "automatic_object_spilling_enabled": True,
    "max_bytes_reclaimable_per_object_spilling": 10**9,
    "object_pinning_enabled": True,
    "lineage_pinning_enabled": True,
})
```

## References

Detailed documentation is available in the following reference files:

- [01-overview-architecture.md](references/01-overview-architecture.md) - Architecture, core concepts, and system design
- [02-ray-core-tasks.md](references/02-ray-core-tasks.md) - Tasks (remote functions), options, scheduling, generators
- [03-ray-core-actors.md](references/03-ray-core-actors.md) - Actors, lifecycle, concurrency, threading, async
- [04-ray-core-objects.md](references/04-ray-core-objects.md) - Objects, ObjectRef, plasma store, serialization
- [05-ray-data.md](references/05-ray-data.md) - Ray Data: datasets, transformations, datasources, execution
- [06-ray-serve.md](references/06-ray-serve.md) - Ray Serve: deployments, HTTP, autoscaling, composition
- [07-ray-train.md](references/07-ray-train.md) - Ray Train: distributed training, frameworks, scaling
- [08-ray-tune.md](references/08-ray-tune.md) - Ray Tune: hyperparameter tuning, search, scheduling
- [09-ray-rllib.md](references/09-ray-rllib.md) - Ray RLlib: reinforcement learning algorithms
- [10-ray-cluster.md](references/10-ray-cluster.md) - Cluster setup, autoscaling, cloud providers
- [11-runtime-environment.md](references/11-runtime-environment.md) - Runtime environments, dependencies
- [12-job-submission.md](references/12-job-submission.md) - Job submission API and CLI
- [13-dashboard-observability.md](references/13-dashboard-observability.md) - Dashboard, monitoring, metrics
- [14-fault-tolerance.md](references/14-fault-tolerance.md) - Fault tolerance, recovery, reliability
- [15-scheduling-placement-groups.md](references/15-scheduling-placement-groups.md) - Scheduling strategies, placement groups
- [16-ray-workflow.md](references/16-ray-workflow.md) - Ray Workflow: durable execution
- [17-ray-dag-compiled-graphs.md](references/17-ray-dag-compiled-graphs.md) - DAGs, compiled graphs
- [18-ray-air.md](references/18-ray-air.md) - Ray AIR: unified AI runtime
- [19-cross-language.md](references/19-cross-language.md) - Cross-language support (Java, C++)
- [20-security.md](references/20-security.md) - Security, authentication, TLS
- [21-cli-reference.md](references/21-cli-reference.md) - Ray CLI commands reference
- [22-internal-architecture.md](references/22-internal-architecture.md) - Internal architecture, GCS, raylet, worker
- [23-ray-llm.md](references/23-ray-llm.md) - Ray LLM: LLM serving integration
- [24-ray-client.md](references/24-ray-client.md) - Ray Client: remote cluster connection
- [25-performance-tuning.md](references/25-performance-tuning.md) - Performance tuning and optimization
