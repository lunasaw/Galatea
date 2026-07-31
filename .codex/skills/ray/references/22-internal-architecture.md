# Internal Architecture

## Overview

Ray's internal architecture consists of several key components that work together to provide distributed computing capabilities. Understanding these internals is essential for debugging, performance tuning, and extending Ray.

## Process Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       Head Node                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │   GCS     │  │ Raylet   │  │Dashboard │  │Workers │ │
│  │  Server   │  │          │  │  Agent   │  │        │ │
│  └─────┬────┘  └─────┬────┘  └──────────┘  └───┬────┘ │
│        │             │                         │       │
│        └──────gRPC───┴─────────────────────────┘       │
│  ┌──────────┐                                           │
│  │  Plasma   │  Shared Memory Object Store              │
│  │  Store    │                                           │
│  └──────────┘                                           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                      Worker Node                        │
│  ┌──────────┐  ┌──────────┐                             │
│  │ Raylet   │  │ Workers  │                             │
│  │          │  │          │                             │
│  └─────┬────┘  └─────┬────┘                             │
│        │             │                                   │
│  ┌─────┴─────────────┴─────┐                            │
│  │    Plasma Object Store   │                            │
│  └─────────────────────────┘                            │
└─────────────────────────────────────────────────────────┘
```

## GCS (Global Control Service)

### Overview
GCS is the central metadata store and cluster coordinator.

### Responsibilities
- Cluster membership management
- Actor/Task metadata storage
- Resource tracking
- Placement group management
- Runtime environment management
- Job management

### GCS Data
| Key Space | Content |
|-----------|---------|
| `JobTable` | Job metadata |
| `ActorTable` | Actor state and metadata |
| `NodeTable` | Node info and status |
| `TaskTable` | Task info (limited) |
| `PlacementGroupTable` | PG state |
| `RuntimeEnvTable` | Runtime env caching |

### GCS Communication
```
GCS Server ←→ Redis (external storage, optional)
     ↕
Raylet (gRPC: port 6379 for client, varies for internal)
     ↕
Workers
```

### GCS Configuration
```python
ray.init(
    _system_config={
        "gcs_rpc_server_reconnect_timeout_s": 60,
        "gcs_service_redis_address": "redis://...",
        "gcs_server_request_timeout_seconds": 30,
        "health_check_initial_delay_ms": 5000,
        "health_check_period_ms": 10000,
        "health_check_timeout_ms": 30000,
    }
)
```

## Raylet

### Overview
The Raylet is the per-node agent responsible for local scheduling, resource management, and object store coordination.

### Raylet Components
```
Raylet
├── Local Scheduler
│   ├── Task Queue
│   ├── Resource Manager
│   └── Worker Pool Manager
├── Object Manager
│   ├── Object Directory
│   ├── Pull Manager
│   ├── Push Manager
│   └── Spilling Manager
├── Worker Pool
│   ├── IOWorker (for ray.put/get)
│   ├── Worker Processes
│   └── Dynamic Workers
└── gRPC Server
    ├── TaskReceiver
    ├── ObjectManager
    └── NodeManager
```

### Local Scheduling
1. Task arrives at Raylet
2. Resource check (local resources available?)
3. If resources available → dispatch to idle worker
4. If no resources → queue the task
5. When resources freed → dequeue and dispatch

### Worker Pool
```python
ray.init(
    _system_config={
        "num_workers_soft_limit": 5,      # Soft limit for idle workers
        "num_workers_hard_limit": 100,     # Hard limit
        "worker_startup_hook": "",         # Hook on worker start
        "max_worker_startup_delay_s": 600, # Max delay for worker startup
    }
)
```

## Worker Process

### Worker Lifecycle
```
Created → Registered → Idle → Executing Task → Idle → ... → Exiting
   ↓                                    ↓
 Driver                          Task Execution
 (Main Process)                  (@ray.remote function)
```

### Worker Types
| Type | Description |
|------|-------------|
| **Driver** | Main Python process, calls ray.init() |
| **Worker** | Executes remote tasks and actor methods |
| **IOWorker** | Handles ray.put/get for large objects |

### Worker Configuration
```python
ray.init(
    _system_config={
        "worker_register_timeout_seconds": 30,
        "worker_heartbeat_timeout_milliseconds": 30000,
        "kill_idle_workers_interval_ms": 1000,
        "idle_worker_killing_time_threshold_ms": 60000,
    }
)
```

## Plasma Object Store

### Architecture
```
Plasma Store (per node)
├── Memory-mapped region
│   ├── Object Allocation Table
│   ├── Object Data (sealed objects)
│   └── Free List
├── Plasma Client (in worker)
│   ├── Create
│   ├── Get (blocking/non-blocking)
│   └── Release
└── Spilling (to disk when full)
    ├── Local filesystem
    ├── S3
    └── NFS
```

### Object Lifecycle
1. **Create**: Worker allocates buffer in Plasma
2. **Seal**: Object data written, sealed for reading
3. **Get**: Workers read object from Plasma
4. **Release**: Worker releases reference (may trigger eviction)
5. **Delete**: Object removed from store

### Object Store Configuration
```python
ray.init(
    object_store_memory=4 * 1024 * 1024 * 1024,  # 4 GB
    _system_config={
        "automatic_object_spilling_enabled": True,
        "max_object_size_in_memory": 100 * 1024 * 1024,  # 100 MB
        "object_store_full_delay_ms": 1000,
        "object_spilling_config": json.dumps({
            "type": "filesystem",
            "params": {"directory_path": "/tmp/ray_spill"}
        }),
    }
)
```

### Object Transfer Protocol
```
Node A (source)                    Node B (destination)
    │                                    │
    │  1. Lookup object location (GCS)   │
    │  ──────────────────────────────→   │
    │  2. Object info response           │
    │  ←──────────────────────────────   │
    │  3. Pull request                   │
    │  ──────────────────────────────→   │
    │  4. Object chunks (TCP)            │
    │  ←──────────────────────────────   │
    │  5. Acknowledgment                 │
    │  ──────────────────────────────→   │
```

## Communication Protocols

### gRPC (Inter-node)
```
Protobuf definitions:
  ray/protobuf/ray.proto
  ray/protobuf/gcs.proto
```

### Channels
| Channel | Purpose |
|---------|---------|
| GCS → Raylet | Cluster updates, task distribution |
| Raylet ↔ Raylet | Object transfer, scheduling |
| Raylet → Worker | Task dispatch |
| Worker → Raylet | Task completion, object info |
| Worker → GCS | Actor registration |

### Serialization
| Serializer | Use Case |
|------------|----------|
| **cloudpickle** | Python functions and objects |
| **Apache Arrow** | Cross-language, Data |
| **MessagePack** | Metadata |
| **Protobuf** | gRPC messages |

## Memory Management

### Memory Pools
```
Total Node Memory
├── System Memory
│   ├── OS / Raylet / GCS
│   └── Worker Processes
└── Ray Object Store Memory
    ├── Active Objects
    ├── Pinned Objects (in-use)
    └── Free Space
```

### Object Spilling Flow
```
Object Store Full?
    │
    ├── Yes → Select objects to spill (LRU)
    │         │
    │         ├── Serialize to disk
    │         │
    │         └── Mark as spilled in Object Directory
    │
    └── No → Continue

Object Requested but Spilled?
    │
    ├── Yes → Restore from disk
    │         │
    │         └── Load back into Object Store
    │
    └── No → Return from memory
```

### Memory Pressure Handling
```python
ray.init(
    _system_config={
        "memory_monitor_interval_ms": 1000,
        "memory_usage_threshold_fraction": 0.9,
        "task_failure_entry_ts_percentage": 0.8,
    }
)
```

## Task Execution Flow

### Complete Flow
```
1. Driver calls task.remote(args)
       │
2. RemoteFunction.__call__()
       │
3. Serialize args → ObjectRefs / inline values
       │
4. Submit to Local Raylet (gRPC)
       │
5. Raylet forwards to GCS (for logging)
       │
6. Raylet schedules on local worker
       │ (or forwards to another node's Raylet)
7. Worker receives task spec
       │
8. Worker deserializes function (cloudpickle)
       │
9. Worker resolves args (ray.get ObjectRefs)
       │
10. Worker executes function
       │
11. Result serialized into Object Store
       │
12. ObjectRef returned to Driver
       │
13. Driver calls ray.get(ref) → retrieves result
```

### Task Spec (Internal)
```python
{
    "task_id": TaskID,
    "job_id": JobID,
    "function_descriptor": FunctionDescriptor,
    "args": [ObjectRef, inline_value, ...],
    "return_object_ids": [ObjectRef, ...],
    "required_resources": {"CPU": 1, "GPU": 0.5},
    "scheduling_strategy": "DEFAULT",
    "max_retries": 3,
    "num_returns": 1,
    "parent_task_id": TaskID,
    "caller_id": WorkerID,
    "depth": 0,
}
```

## Actor Execution Flow

```
1. Driver calls ActorClass.remote(*args)
       │
2. Actor creation task submitted to GCS
       │
3. GCS assigns to Raylet based on resources
       │
4. Raylet spawns Worker (or reuses idle)
       │
5. Worker deserializes actor class
       │
6. Worker calls __init__(*args)
       │
7. Actor registered in GCS → ALIVE
       │
8. ActorHandle returned to Driver
       │
9. Method calls: handle.method.remote(args)
       │
10. Method submitted as task to actor's Raylet
       │
11. Worker executes method
       │
12. Result placed in Object Store
```

## Autoscaler Internals

### v1 Autoscaler
```
GCS Monitor (head node)
    │
    ├── Polls GCS for resource demand
    │
    ├── Calculates needed nodes
    │
    ├── Calls cloud provider API
    │   ├── AWS: EC2 RunInstances
    │   ├── GCP: Compute Engine insert
    │   └── Azure: VirtualMachine create
    │
    └── Monitors node startup
```

### v2 Autoscaler
```
Autoscaler v2 (event-driven)
    │
    ├── Subscribes to GCS state changes
    │
    ├── Improved bin-packing algorithm
    │
    ├── Better scheduling decisions
    │
    └── Faster scale-up/down
```

## Performance Characteristics

### Latency (approximate)
| Operation | Latency |
|-----------|---------|
| task.remote() | ~1ms (local) |
| ray.get() (local) | ~0.1ms |
| ray.get() (remote) | ~1-10ms |
| Actor method call | ~1ms (local) |
| Object transfer (1MB) | ~10ms |
| Object transfer (1GB) | ~1s |
| Worker startup | ~500ms |
| Actor creation | ~1s |

### Throughput
| Operation | Throughput |
|-----------|------------|
| Task submission | ~10K-100K/sec |
| Object store ops | ~1M/sec |
| ray.put (small) | ~100K/sec |
| ray.get (small) | ~500K/sec |

## Debugging Internals

### Useful System Config
```python
ray.init(
    _system_config={
        "worker_cap_initial_backoff_delay_ms": 100,
        "worker_cap_max_backoff_delay_ms": 5000,
        "task_retry_delay_ms": 100,
        "max_num_rejected_task_resubmit": 100,
        "debug_dump_period_milliseconds": 10000,
        "event_stats_print_interval_ms": 60000,
        "event_stats": True,
    }
)
```

### Internal API
```python
import ray._private.usage as usage
import ray._private.state as state

# Global state
gs = ray._private.state.GlobalState()
gs._initialize("head:6379", "password")

# Task info
tasks = gs.task_table()
actors = gs.actor_table()
objects = gs.object_table()
```
