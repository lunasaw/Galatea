# Scheduling & Placement Groups

## Overview

Ray's scheduler places tasks and actors across the cluster based on resource requirements, scheduling strategies, and placement group constraints.

## Scheduling Architecture

```
                  +-----------------+
                  |   GCS Server    |
                  | (Global State)  |
                  +--------+--------+
                           |
              +------------+------------+
              |            |            |
        +-----+----+ +----+-----+ +----+-----+
        |  Raylet   | |  Raylet  | |  Raylet  |
        | (Local    | | (Local   | | (Local   |
        | Scheduler)| |Scheduler)| |Scheduler)|
        +-----+-----+ +----+----+ +----+-----+
              |            |            |
        Workers       Workers       Workers
```

## Resource Types

### Built-in Resources
```python
# CPU
@ray.remote(num_cpus=4)
def cpu_task():
    pass

# GPU
@ray.remote(num_gpus=2)
def gpu_task():
    pass

# Memory
@ray.remote(memory=4 * 1024 * 1024 * 1024)  # 4 GB
def memory_task():
    pass

# Object Store Memory
@ray.remote(object_store_memory=1 * 1024 * 1024 * 1024)
def store_task():
    pass
```

### Custom Resources
```python
# Define at node startup
ray.init(resources={"TPU": 4, "SSD": 1})

# Or via CLI
# ray start --head --resources='{"TPU": 4}'

# Use in tasks
@ray.remote(resources={"TPU": 1})
def tpu_task():
    pass

# Use in actors
@ray.remote(resources={"SSD": 1})
class StorageActor:
    pass
```

### Resource Labels
```python
@ray.remote(
    num_cpus=2,
    num_gpus=1,
    memory=2e9,
    resources={"TPU": 2},
    accelerator_type="A100",
)
def resource_heavy_task():
    pass
```

## Scheduling Strategies

### Default Strategy
```python
@ray.remote
def default_task():
    pass
# Uses "DEFAULT" strategy - spread across nodes
```

### Available Strategies

| Strategy | Description |
|----------|-------------|
| `DEFAULT` | Default Ray scheduler, considers load |
| `SPREAD` | Spread tasks across nodes |
| `PACK` | Pack tasks on fewest nodes |
| `STRICT_PACK` | All tasks on same node |
| `STRICT_SPREAD` | Each task on different node |
| `NODE_AFFINITY` | Pin to specific node |
| `PLACEMENT_GROUP` | Use placement group |

### SPREAD Strategy
```python
@ray.remote(scheduling_strategy="SPREAD")
def spread_task():
    pass
```

### PACK Strategy
```python
@ray.remote(scheduling_strategy="PACK")
def pack_task():
    pass
```

### STRICT_SPREAD Strategy
```python
@ray.remote(scheduling_strategy="STRICT_SPREAD")
def strict_spread_task():
    pass
```

### STRICT_PACK Strategy
```python
@ray.remote(scheduling_strategy="STRICT_PACK")
def strict_pack_task():
    pass
```

### NODE_AFFINITY Strategy
```python
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

# Pin to specific node
@ray.remote(
    scheduling_strategy=NodeAffinitySchedulingStrategy(
        node_id=ray.get_runtime_context().get_node_id(),
        soft=False,  # Hard constraint
    )
)
def pinned_task():
    pass

# Soft affinity (prefer but don't require)
@ray.remote(
    scheduling_strategy=NodeAffinitySchedulingStrategy(
        node_id=node_id,
        soft=True,
    )
)
def soft_pinned_task():
    pass
```

### Placement Group Strategy
```python
from ray.util.scheduling_strategies import PlacementGroupSchedulingStrategy

@ray.remote(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
        placement_group_capture_child_tasks=True,
    )
)
def pg_task():
    pass
```

## Placement Groups

### Creating Placement Groups
```python
import ray
from ray.util.placement_group import placement_group

# Basic placement group
pg = placement_group(
    bundles=[
        {"CPU": 4, "GPU": 1},
        {"CPU": 4, "GPU": 1},
        {"CPU": 2},
    ],
    strategy="PACK",
)

# Wait for placement group to be ready
ray.get(pg.ready())

# Check status
table = ray.util.placement_group_table(pg)
print(table)
```

### Placement Group Strategies

| Strategy | Description |
|----------|-------------|
| `PACK` | Pack bundles on fewest nodes |
| `SPREAD` | Spread bundles across nodes |
| `STRICT_PACK` | All bundles on one node |
| `STRICT_SPREAD` | Each bundle on different node |

### Bundle Resources
```python
# GPU training placement group
pg = placement_group(
    bundles=[
        {"CPU": 8, "GPU": 4},   # Bundle 0: Training node
        {"CPU": 8, "GPU": 4},   # Bundle 1: Training node
        {"CPU": 4, "GPU": 1},   # Bundle 2: Eval node
        {"CPU": 2},             # Bundle 3: Data loading
    ],
    strategy="PACK",
)
```

### Using Placement Groups

#### With Tasks
```python
@ray.remote(num_cpus=2)
def train_step(data):
    pass

# Schedule task on specific bundle
ref = train_step.options(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
    )
).remote(data)
```

#### With Actors
```python
@ray.remote(num_gpus=1)
class GPUActor:
    def compute(self):
        pass

# Place actor on bundle 0
actor = GPUActor.options(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
    )
).remote()
```

#### Child Task Capture
```python
@ray.remote
def parent_task():
    # Child tasks are captured by default if parent is in a PG
    child_ref = child_task.remote()  # Runs in same PG
    return ray.get(child_ref)

parent_ref = parent_task.options(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
        placement_group_capture_child_tasks=True,  # Default
    )
).remote()
```

### Placement Group Management

#### Named Placement Groups
```python
pg = placement_group(
    bundles=[{"CPU": 2}],
    strategy="PACK",
    name="my_pg",
    lifetime="detached",
)

# Retrieve by name
pg = ray.util.get_placement_group("my_pg")
```

#### Remove Placement Group
```python
# Remove a placement group
ray.util.remove_placement_group(pg)

# Placement group will transition to REMOVED state
```

#### Placement Group Table
```python
# Get all placement groups
table = ray.util.placement_group_table()
for pg_id, info in table.items():
    print(f"PG: {pg_id}")
    print(f"  State: {info['state']}")
    print(f"  Bundles: {info['bundles']}")
    print(f"  Strategy: {info['strategy']}")
```

### Placement Group Lifetime
```python
# Default: dies with creator
pg = placement_group(bundles=[{"CPU": 2}], strategy="PACK")

# Detached: survives job exit
pg = placement_group(
    bundles=[{"CPU": 2}],
    strategy="PACK",
    lifetime="detached",
)

# Explicitly non-detached
pg = placement_group(
    bundles=[{"CPU": 2}],
    strategy="PACK",
    lifetime="non_detached",
)
```

## Resource Management

### Viewing Resources
```python
# Total cluster resources
total = ray.cluster_resources()
# {'CPU': 32.0, 'GPU': 8.0, 'memory': 64e9, ...}

# Available resources
available = ray.available_resources()
# {'CPU': 24.0, 'GPU': 4.0, ...}

# Per-node resources
nodes = ray.nodes()
for node in nodes:
    print(f"Node {node['NodeID']}:")
    print(f"  Resources: {node['Resources']}")
    print(f"  Alive: {node['Alive']}")
```

### Resource Limits
```python
# Request specific resources per task
@ray.remote(
    num_cpus=2,
    num_gpus=1,
    memory=4e9,
    resources={"TPU": 1},
)
def resource_intensive_task():
    import ray
    ctx = ray.get_runtime_context()
    # Access assigned GPU
    return ctx.get_assigned_resources()
```

### Fractional Resources
```python
# Fractional GPU (multi-tenancy)
@ray.remote(num_gpus=0.25)
def quarter_gpu_task():
    pass

# Fractional CPU
@ray.remote(num_cpus=0.5)
def half_cpu_task():
    pass
```

## Scheduling Priority

### Priority Levels
```python
# Higher runtime_env's .priority value = higher scheduling priority
# Tasks with higher priority are scheduled first
@ray.remote
def high_priority_task():
    pass
```

### Concurrency Groups
```python
@ray.remote(concurrency_groups={
    "io_pool": 4,     # 4 concurrent threads for IO
    "compute_pool": 2, # 2 concurrent threads for compute
})
class ConcurrentActor:
    def default_method(self):
        pass  # Uses default concurrency (1)

    @ray.method(concurrency_group="io_pool")
    def io_operation(self):
        pass  # Uses io_pool (4 threads)

    @ray.method(concurrency_group="compute_pool")
    def compute(self):
        pass  # Uses compute_pool (2 threads)
```

## Best Practices

1. **Use placement groups** for co-located GPU tasks to minimize data transfer
2. **Use STRICT_SPREAD** to ensure fault isolation across nodes
3. **Use PACK** for data-locality-sensitive workloads
4. **Set resource requirements accurately** to avoid over/under-provisioning
5. **Use `placement_group_capture_child_tasks=True`** (default) to keep related tasks together
6. **Name placement groups** for cross-job sharing
7. **Use detached PGs** for long-lived shared resources
8. **Clean up PGs** when no longer needed to free resources
9. **Use fractional GPUs** for inference workloads with lower GPU requirements
10. **Monitor scheduling delays** via dashboard to detect resource contention
