# Ray Architecture Overview

## System Architecture

Ray is a distributed computing framework consisting of several key components that work together to provide a unified platform for scaling AI applications.

```
+------------------------------------------------------------------+
|                     User Application                               |
|  Python API  |  Java API  |  C++ API  |  CLI  |  Dashboard       |
+------------------------------------------------------------------+
|                     Ray Libraries                                  |
|  Ray Data  |  Ray Train  |  Ray Tune  |  Ray Serve  |  RLlib     |
+------------------------------------------------------------------+
|                     Ray AIR (AI Runtime)                           |
|  Checkpoints  |  Batch Prediction  |  Metrics  |  Integrations   |
+------------------------------------------------------------------+
|                     Ray Core                                       |
|  Tasks  |  Actors  |  Objects  |  Scheduling  |  Placement Groups|
|  Namespaces  |  Runtime Env  |  Fault Tolerance  |  DAGs          |
+------------------------------------------------------------------+
|                  Cluster Management Layer                          |
|  GCS (Global Control Service)                                     |
|  Autoscaler  |  Job Submission  |  Ray Client  |  KubeRay        |
+------------------------------------------------------------------+
|                  Node-Level Runtime                                |
|  Raylet (per node)  |  Object Store (Plasma)  |  Worker Pool     |
+------------------------------------------------------------------+
|                  Communication Layer                               |
|  gRPC  |  Shared Memory  |  NCCL  |  Direct Actor Calls         |
+------------------------------------------------------------------+
```

## Core Concepts

### Tasks
Tasks are stateless remote functions. When a function decorated with `@ray.remote` is called, Ray schedules it as a task on a remote worker. Tasks are the fundamental unit of parallelism in Ray.

**Key properties:**
- Stateless - each invocation is independent
- Can return one or more ObjectRefs
- Automatically retried on failure (configurable)
- Support resource requirements (CPU, GPU, custom)
- Execute asynchronously

### Actors
Actors are stateful remote objects created from classes decorated with `@ray.remote`. An actor maintains mutable state across method calls.

**Key properties:**
- Stateful - methods share mutable state
- Created with `.remote()` constructor call
- Methods called with `.method.remote()`
- Support concurrency models (threaded, async)
- Can be named, detached, or have custom lifetimes
- Support restart on failure

### Objects
Objects are values stored in Ray's distributed shared-memory object store. They are referenced by `ObjectRef` handles.

**Key properties:**
- Immutable once created
- Stored in shared memory (Plasma store)
- Automatically garbage collected
- Can be spilled to disk
- Support lineage-based reconstruction

### Scheduling
Ray uses a distributed scheduling strategy. Tasks and actors are scheduled based on:
- Resource requirements (CPU, GPU, memory, custom)
- Scheduling strategy (DEFAULT, SPREAD, affinity-based)
- Placement group constraints
- Data locality (tasks scheduled near input data)

## Ray Runtime Components

### GCS (Global Control Service)
The GCS is a centralized service running on the head node that manages:
- **Job table**: Tracks all running jobs
- **Actor table**: Tracks actor creation and state
- **Placement group table**: Manages placement groups
- **Node table**: Tracks cluster membership
- **Pub/Sub**: Event notification system

### Raylet
The raylet runs on every node and handles:
- **Task scheduling**: Local task dispatch and execution
- **Resource management**: Tracks available resources on the node
- **Object store management**: Manages the local Plasma store
- **Worker pool**: Manages worker process lifecycle
- **Heartbeat**: Reports node health to GCS

### Worker
Workers are processes that execute Ray tasks and actor methods:
- **Driver**: The main Python process that creates tasks
- **Worker**: Process that executes remote functions
- **Actor worker**: Process hosting an actor instance
- **I/O worker**: Handles object spilling/restoring

### Object Store (Plasma)
The Plasma object store provides shared-memory object storage:
- **In-memory storage**: Primary storage for objects
- **Spilling**: Overflow to disk when memory is full
- **Reference counting**: Automatic memory management
- **Cross-language**: Supports Python, Java, C++ objects

## Communication Patterns

### Direct Actor Calls
Actor methods are invoked via direct gRPC connections between the caller and the actor's worker process, bypassing the raylet for low latency.

### Object Transfer
Objects are transferred between nodes via:
- **Local**: Shared memory (Plasma) for same-node access
- **Remote**: gRPC-based transfer between nodes
- **RDMA**: Direct GPU-to-GPU transfer (experimental)

### Task Submission
Tasks are submitted to the local raylet, which either executes them locally or forwards them to a remote raylet based on resource availability and scheduling strategy.

## Memory Management

### Object Store Memory
- Default size: 30% of system RAM or 1GB (whichever is larger)
- Configurable via `object_store_memory` parameter in `ray.init()`
- Objects are automatically evicted when memory pressure increases
- Pinned objects (referenced by active ObjectRefs) cannot be evicted

### Spilling
When the object store is full:
1. Unreferenced objects are evicted first
2. If still full, referenced objects are spilled to disk
3. Spilled objects are automatically restored when accessed
4. Spilling configuration: `RAY_object_spilling_config`

### Garbage Collection
- Reference counting tracks ObjectRef liveness
- Distributed reference counting across worker processes
- When all references to an object are gone, it becomes eligible for eviction
- Lineage-based reconstruction can recreate lost objects from task dependencies

## Cluster Lifecycle

### Initialization
1. `ray.init()` starts local cluster or connects to existing one
2. GCS starts on head node
3. Raylet starts on each node
4. Object store initialized
5. Worker pool created
6. Dashboard starts (if enabled)

### Job Submission
1. Job registered with GCS
2. Driver process connects to cluster
3. Tasks/actors created and scheduled
4. Results collected via ObjectRefs

### Shutdown
1. `ray.shutdown()` disconnects driver
2. Reference counts decremented
3. Objects eligible for eviction
4. Actors cleaned up
5. Resources released

## Design Principles

1. **Simple API**: Core API has only a handful of functions (`ray.init`, `@ray.remote`, `.remote()`, `ray.get`, `ray.wait`)
2. **Flexible scheduling**: Multiple scheduling strategies for different workloads
3. **Fault tolerance**: Automatic retry and reconstruction on failure
4. **Efficient data sharing**: Shared memory for zero-copy data access
5. **Scalability**: From single machine to thousands of nodes
6. **Heterogeneous computing**: Support for CPUs, GPUs, TPUs, and custom accelerators

## Process Architecture

```
Head Node                                    Worker Node
+------------------+                        +------------------+
|   GCS Server     |<---gRPC--->            |   Raylet         |
|   - Job Table    |                        |   - Scheduler    |
|   - Actor Table  |                        |   - Resource Mgr |
|   - Node Table   |                        |   - Worker Pool  |
|   - Pub/Sub      |                        +------------------+
+------------------+                        |   Plasma Store   |
|   Raylet         |                        |   - Shared Memory|
|   - Scheduler    |                        |   - Spilling     |
|   - Worker Pool  |                        +------------------+
|   - Plasma Store |                        |   Workers        |
+------------------+                        |   - Task Workers |
|   Workers        |                        |   - Actor Workers|
|   - Driver       |                        |   - I/O Workers  |
|   - Workers      |                        +------------------+
+------------------+                        |   Dashboard Agent|
|   Dashboard      |                        +------------------+
|   - Web UI       |
+------------------+
```

## Namespace and Isolation

Ray provides logical isolation through namespaces:
- Each job can specify a namespace
- Named actors are scoped to namespaces
- Namespaces enable multi-tenant cluster sharing
- Default namespace is the job ID

## Runtime Environment

Runtime environments provide dependency isolation:
- **pip**: Python packages
- **conda**: Conda environments
- **working_dir**: Working directory (local or remote URI)
- **env_vars**: Environment variables
- **container**: Docker container support
- Inherited from parent by default
- Can be overridden per task/actor

## Cluster Sizing Guidelines

| Workload Type | CPUs per Node | GPUs per Node | Memory | Nodes |
|--------------|---------------|---------------|--------|-------|
| Data Processing | 8-16 | 0 | 32-64GB | 2-10 |
| Training (GPU) | 8-16 | 4-8 | 64-128GB | 2-100 |
| Serving | 4-8 | 1-4 | 32-64GB | 2-50 |
| RL Training | 8-32 | 1-8 | 64-128GB | 2-50 |
| Hyperparameter Tuning | 4-8 | 0-4 | 32-64GB | 2-20 |

## Key Metrics

| Metric | Description |
|--------|-------------|
| `ray_tasks` | Number of tasks submitted/executed |
| `ray_actors` | Number of actors created |
| `ray_object_store_memory` | Object store memory usage |
| `ray_cluster_resources` | Total cluster resources |
| `ray_available_resources` | Available cluster resources |
| `ray_node_count` | Number of nodes in cluster |

## Error Handling Strategy

1. **Task failure**: Retry up to `max_retries` times (default: 3)
2. **Actor failure**: Restart up to `max_restarts` times (default: 0)
3. **Node failure**: Tasks rescheduled, actors restarted
4. **Object loss**: Lineage-based reconstruction if enabled
5. **GCS failure**: Cluster becomes unavailable (single point of failure)

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `RAY_address` | Cluster address to connect to | None |
| `RAY_namespace` | Default namespace | Job ID |
| `RAY_RUNTIME_ENV` | Runtime env as JSON | None |
| `RAY_record_ref_creation_sites` | Track ObjectRef creation | False |
| `RAY_LOG_TO_STDERR` | Log to stderr | False |
| `RAY_BACKEND_LOG_LEVEL` | C++ log level | info |
| `RAY_graceful_shutdown_timeout_s` | Graceful shutdown wait | 60 |
| `RAY_max_lineage_bytes` | Max lineage for reconstruction | 1GB |
| `RAY_object_spilling_config` | Object spilling config | None |
