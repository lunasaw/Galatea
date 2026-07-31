# Ray Client

## Overview

Ray Client allows connecting to a remote Ray cluster from a local Python process. It provides a lightweight way to submit tasks and interact with a remote cluster without running a full Ray instance locally.

## Architecture

```
Local Python Process                Remote Ray Cluster
┌─────────────────┐                ┌──────────────────┐
│  Ray Client      │   gRPC        │  Ray Server       │
│  (ray.init)      │ ───────────→  │  (Dashboard API)  │
│                  │                │                    │
│  - submit tasks  │                │  - execute tasks   │
│  - ray.get/put   │                │  - manage actors   │
│  - actor calls   │                │  - object store    │
└─────────────────┘                └──────────────────┘
```

## Connecting to a Cluster

### Basic Connection
```python
import ray

# Connect via Ray Client
ray.init("ray://<head-node-ip>:10001")

# Or with full address
ray.init(address="ray://head.example.com:10001")
```

### Connection Options
```python
ray.init(
    address="ray://head:10001",
    namespace="my_namespace",
    runtime_env={
        "pip": ["torch", "transformers"],
        "working_dir": "./",
    },
    # Client-specific options
    _redis_password="password",
    _tls_config={
        "cert_chain": "/path/to/cert.pem",
        "private_key": "/path/to/key.pem",
        "ca_cert": "/path/to/ca.pem",
    },
)
```

### Environment Variable
```python
import os
os.environ["RAY_ADDRESS"] = "ray://head:10001"

# Auto-connect using env var
import ray
ray.init()  # Uses RAY_ADDRESS
```

```bash
# Set environment variable
export RAY_ADDRESS="ray://head:10001"
python my_script.py
```

## Starting the Ray Server

### On Head Node
```bash
# Start head with client port
ray start --head \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=8265 \
    --ray-client-server-port=10001

# Default client port is 10001
```

### Custom Port
```bash
ray start --head \
    --ray-client-server-port=10001
```

## Usage

### Remote Tasks
```python
import ray

ray.init("ray://head:10001")

@ray.remote
def process(data):
    return transform(data)

# Submit task - runs on remote cluster
ref = process.remote(input_data)

# Get result - fetches from remote cluster
result = ray.get(ref)
```

### Remote Actors
```python
@ray.remote
class RemoteModel:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    def predict(self, input_data):
        return self.model(input_data)

# Actor created on remote cluster
model = RemoteModel.remote("model.pt")
result = ray.get(model.predict.remote(data))
```

### Object Operations
```python
# Put object to remote cluster
ref = ray.put(large_array)

# Get object from remote cluster
data = ray.get(ref)

# Wait for multiple objects
refs = [task.remote() for _ in range(10)]
ready, remaining = ray.wait(refs, num_returns=5)
results = ray.get(ready)
```

### Named Actors
```python
# Create named actor
actor = MyActor.options(
    name="shared_actor",
    namespace="production",
).remote()

# Get existing actor from another client
actor = ray.get_actor("shared_actor", namespace="production")
```

### Placement Groups
```python
import ray
from ray.util.placement_group import placement_group

ray.init("ray://head:10001")

pg = placement_group(
    bundles=[{"GPU": 1}, {"GPU": 1}],
    strategy="PACK",
)
ray.get(pg.ready())

@ray.remote(num_gpus=1)
def gpu_task():
    return compute()

ref = gpu_task.options(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
    )
).remote()
```

## Limitations

### Supported Operations
| Operation | Supported |
|-----------|-----------|
| `ray.init()` | Yes |
| `ray.get()` | Yes |
| `ray.put()` | Yes |
| `ray.wait()` | Yes |
| `ray.cancel()` | Yes |
| `ray.kill()` | Yes |
| `@ray.remote` functions | Yes |
| `@ray.remote` classes | Yes |
| `ray.get_actor()` | Yes |
| `ray.get_runtime_context()` | Limited |
| `ray.timeline()` | No |
| `ray.cluster_resources()` | Yes |
| `ray.available_resources()` | Yes |

### Known Limitations
1. **Serialization overhead**: All data passes through gRPC, slower than local
2. **No local object store**: Objects stored only on remote cluster
3. **Limited runtime context**: Some `get_runtime_context()` methods unavailable
4. **No timeline/profile**: Profiling tools not supported via client
5. **`ray.wait()`**: Different behavior for local vs remote objects
6. **Global variables**: Captured globals may not serialize correctly
7. **Large objects**: Transfer overhead can be significant for large data

## Performance Considerations

### Data Transfer
```python
# BAD: Transfer large data per call
@ray.remote
def process_large(data):
    return compute(data)

# Each call transfers data over network
for chunk in large_dataset:
    result = ray.get(process_large.remote(chunk))

# GOOD: Use ray.put to stage data once
ref = ray.put(large_dataset)
result = ray.get(process_large.remote(ref))
```

### Batching
```python
# BAD: Many small remote calls
results = [ray.get(task.remote(x)) for x in data]

# GOOD: Batch into fewer calls
@ray.remote
def batch_process(batch):
    return [process(x) for x in batch]

batches = [data[i:i+100] for i in range(0, len(data), 100)]
refs = [batch_process.remote(b) for b in batches]
results = ray.get(refs)
```

### Keep-Alive
```python
# Ray Client maintains persistent connection
# Reconnection is automatic if server restarts
ray.init(
    "ray://head:10001",
    # Connection timeout
    _client_server_connect_timeout_s=30,
)
```

## TLS Configuration

### Server Side
```bash
ray start --head \
    --tls-cert-file=/path/to/cert.pem \
    --tls-key-file=/path/to/key.pem \
    --tls-ca-file=/path/to/ca.pem \
    --ray-client-server-port=10001
```

### Client Side
```python
ray.init(
    "ray://head:10001",
    _tls_config={
        "cert_chain": "/path/to/cert.pem",
        "private_key": "/path/to/key.pem",
        "ca_cert": "/path/to/ca.pem",
    },
)
```

## Error Handling

### Connection Errors
```python
import ray

try:
    ray.init("ray://head:10001")
except ConnectionError:
    print("Cannot connect to Ray cluster")
except ray.exceptions.RaySystemError:
    print("Ray system error")
```

### Reconnection
```python
# Ray Client automatically reconnects
# If connection drops:
# 1. Client attempts reconnection
# 2. Pending tasks preserved on server
# 3. Results retrievable after reconnection

# Check connection
try:
    ray.cluster_resources()
except Exception:
    print("Connection lost, reconnecting...")
    ray.shutdown()
    ray.init("ray://head:10001")
```

## Comparison: Client vs Direct

| Aspect | Ray Client | Direct (`ray.init()`) |
|--------|-----------|----------------------|
| Connection | Remote (gRPC) | Local (shared memory) |
| Latency | Higher (~1-5ms) | Lower (~0.1ms) |
| Object store | Remote only | Local + Remote |
| Setup | No local Ray needed | Local Ray needed |
| Use case | Laptop → Cluster | Within cluster |
| Data transfer | Network | Shared memory |
| GPU access | Remote only | Local or Remote |

## Best Practices

1. **Use `ray.put()`** for large data to avoid repeated transfers
2. **Batch operations** to minimize network round trips
3. **Use named actors** for cross-client sharing
4. **Set `RAY_ADDRESS`** env var for convenience
5. **Handle connection errors** gracefully
6. **Use TLS** in production environments
7. **Keep local data small** - transfer only what's needed
8. **Prefer Ray Data** for large-scale data processing (automatic optimization)
9. **Use `ray.wait()`** instead of multiple `ray.get()` for parallel tasks
10. **Disconnect cleanly** with `ray.shutdown()`
