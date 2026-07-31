# Ray Serve

## Architecture

Ray Serve is a scalable model serving library built on Ray. It composes multiple models, frameworks, and languages into a single serving application.

```
Client Request -> HTTP Proxy -> Router -> Replica Pool -> Response
                                      -> Deployment A.handle
                                      -> Deployment B.handle
```

## Core API

### @serve.deployment Decorator
```python
from ray import serve

@serve.deployment(
    name=DEFAULT.VALUE,                    # Deployment name
    num_replicas=1,                         # Number of replicas
    route_prefix=DEFAULT.VALUE,            # HTTP route prefix
    ray_actor_options={                     # Actor resource options
        "num_cpus": 1,
        "num_gpus": 0,
        "memory": None,
        "resources": {},
        "runtime_env": None,
    },
    autoscaling_config=None,               # AutoscalingConfig
    user_config=None,                       # Dynamic config (passed to reconfigure)
    max_ongoing_requests=5,                 # Max concurrent requests per replica
    max_queued_requests=-1,                 # Max queued requests (-1 = unlimited)
    graceful_shutdown_wait_loop_s=2,        # Wait before shutdown
    graceful_shutdown_timeout_s=20,         # Force kill timeout
    health_check_period_s=10,              # Health check interval
    health_check_timeout_s=30,             # Health check timeout
    logging_config=None,                    # LoggingConfig
    max_replicas_per_node=100,             # Max replicas per node
    placement_group_bundles=None,           # PG bundles per replica
    placement_group_strategy="PACK",        # PG strategy
    request_router_config=None,             # RequestRouterConfig
    rolling_update_percentage=0.2,          # Fraction to update at once
)
class MyModel:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    async def __call__(self, request):
        data = await request.json()
        return self.model.predict(data)
```

### Application Building
```python
# Single deployment
app = MyModel.bind("/path/to/model")
handle = serve.run(app)

# Model composition
app = Pipeline.bind(
    preprocessor=Preprocessor.bind(),
    model=Model.bind("/path/to/model"),
)
handle = serve.run(app)
```

### serve.run() / serve.delete()
```python
# Deploy application
handle = serve.run(app, name="my_app", route_prefix="/predict")

# Deploy multiple apps
from ray.serve.config import RunTarget
handles = serve.run_many([
    RunTarget(target=app1, name="app1", route_prefix="/app1"),
    RunTarget(target=app2, name="app2", route_prefix="/app2"),
])

# Delete application
serve.delete("my_app")

# Blocking mode (runs until Ctrl-C)
serve.run(app, blocking=True)
```

## DeploymentHandle

```python
# Get handle
handle = serve.run(app)
handle = serve.get_deployment_handle("deployment_name", "app_name")

# Call deployment
response = handle.remote(data)

# With options
response = handle.options(
    method_name="predict",
    multiplexed_model_id="model_v2",
    stream=True,
).remote(data)

# Broadcast to all replicas
broadcast_response = handle.broadcast("method", data)
```

## AutoscalingConfig

```python
from ray.serve.config import AutoscalingConfig

@serve.deployment(
    autoscaling_config=AutoscalingConfig(
        min_replicas=1,
        max_replicas=10,
        initial_replicas=None,
        target_ongoing_requests=5.0,
        look_back_period_s=30.0,
        upscale_delay_s=30.0,
        downscale_delay_s=600.0,
        upscaling_factor=None,              # Scaling multiplier
        downscaling_factor=None,
        aggregation_function="mean",         # mean, max, min
    )
)
class AutoScaled:
    pass
```

## HTTP Handling

### Basic HTTP
```python
@serve.deployment(route_prefix="/predict")
class Predictor:
    async def __call__(self, request):
        data = await request.json()
        result = self.model.predict(data)
        return {"result": result}
```

### FastAPI Integration
```python
from fastapi import FastAPI
from starlette.requests import Request

app = FastAPI()

@serve.deployment
@serve.ingress(app)
class FastAPIApp:
    @app.get("/health")
    def health(self):
        return {"status": "ok"}

    @app.post("/predict")
    async def predict(self, request: Request):
        data = await request.json()
        return {"prediction": self.model(data)}

serve.run(FastAPIApp.bind())
```

### HTTPOptions
```python
serve.start(http_options={
    "host": "0.0.0.0",
    "port": 8000,
    "location": "EveryNode",      # Disabled, HeadOnly, EveryNode
    "root_path": "",
    "request_timeout_s": None,
    "keep_alive_timeout_s": 90,
    "ssl_keyfile": None,
    "ssl_certfile": None,
})
```

## Model Multiplexing

```python
@serve.deployment
class MultiModel:
    @serve.multiplexed(max_num_models_per_replica=3)
    async def get_model(self, model_id: str):
        return load_model(model_id)

    async def __call__(self, request):
        model_id = serve.get_multiplexed_model_id()
        model = self.get_model(model_id)
        return model.predict(request.data)
```

## Batching

```python
@serve.deployment
class BatchedModel:
    @serve.batch(max_batch_size=10, batch_wait_timeout_s=0.1)
    async def predict(self, inputs: List[str]) -> List[str]:
        return self.model.batch_predict(inputs)

    async def __call__(self, request):
        result = await self.predict(request.data)
        return result
```

## Model Composition

```python
@serve.deployment
class Preprocessor:
    async def __call__(self, request):
        data = await request.json()
        return preprocess(data)

@serve.deployment
class Model:
    def __init__(self, preprocessor: DeploymentHandle):
        self.preprocessor = preprocessor

    async def __call__(self, request):
        processed = await self.preprocessor.remote(request)
        return self.model.predict(processed)

app = Model.bind(Preprocessor.bind())
serve.run(app)
```

## Dynamic Configuration

```python
@serve.deployment(user_config={"param": "value"})
class Configurable:
    def reconfigure(self, config: dict):
        self.param = config["param"]

    async def __call__(self, request):
        return {"param": self.param}

# Update config at runtime
serve.run(Configurable.bind())
# Update via new deployment with different user_config
```

## Health Checks

```python
@serve.deployment(
    health_check_period_s=10,
    health_check_timeout_s=30,
)
class Healthy:
    def check_health(self):
        if not self.is_healthy():
            raise RuntimeError("Unhealthy")
```

## gRPC Support

```python
serve.start(grpc_options={
    "port": 9000,
    "grpc_servicer_functions": ["my_module:add_servicer"],
    "request_timeout_s": None,
})
```

## Monitoring

```python
# Get replica context
ctx = serve.get_replica_context()
print(f"Deployment: {ctx.deployment}")
print(f"Replica: {ctx.replica_tag}")

# Custom metrics
from ray import metrics
counter = metrics.Counter("my_counter", tag_keys=("deployment",))
counter.inc(tags={"deployment": "my_deployment"})
```
