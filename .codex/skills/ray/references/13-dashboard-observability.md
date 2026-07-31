# Dashboard & Observability

## Overview

Ray Dashboard provides a web-based UI for monitoring and debugging Ray clusters. It exposes cluster state, resource usage, task/actor details, logs, and metrics.

## Dashboard Architecture

```
+------------------+       +------------------+
|  Browser UI      |<----->|  Dashboard Agent |
|  (React)         | HTTP  |  (head:8265)     |
+------------------+       +------------------+
                                    |
                            +-------+-------+
                            |       |       |
                         GCS    Raylet   Workers
```

## Accessing the Dashboard

### Default Access
```
http://<head-node-ip>:8265
```

### Configuration
```bash
ray start --head \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=8265 \
    --dashboard-agent-listen-port=52365
```

### Dashboard URL from Code
```python
import ray
ray.init()
print(ray.get_dashboard_url())
# http://<head-ip>:8265
```

## Dashboard Pages

### Overview Page
- Cluster resource summary (CPU/GPU/memory)
- Active nodes count
- Pending/running tasks
- Logical view of applications

### Jobs Page
- List of submitted jobs
- Job status, entrypoint, timestamps
- View logs per job

### Actors Page
- All actors in the cluster
- State: ALIVE, RESTARTING, DEAD
- Resource consumption per actor
- Actor creation task details

### Tasks Page
- Running and recent tasks
- Task state, function name
- Execution duration
- Resource requirements

### Objects Page
- Object store memory usage
- Spilled objects count
- ObjectRef details
- Primary vs secondary copies

### Nodes Page
- All nodes in cluster
- Resource capacity per node
- Raylet status
- Worker processes per node

### Logs Page
- Aggregated logs from all nodes
- Filter by node, job, actor
- Real-time log streaming

### Memory Page
- Object store memory pressure
- Memory usage by category
- Spilling statistics
- ObjectRef reference counts

### Metrics Page
- Prometheus metrics endpoint
- Custom metric charts
- Performance counters

### Tune / Training Page
- Trial progress for Tune experiments
- Training metrics visualization
- Best trial tracking

## State API

### Cluster Status
```python
import ray

# Get cluster resources
resources = ray.cluster_resources()
# {'CPU': 16.0, 'GPU': 4.0, 'memory': 8000000000.0, 'object_store_memory': 2000000000.0}

# Get available resources
available = ray.available_resources()
# {'CPU': 12.0, 'GPU': 2.0}

# Get alive nodes
alive_nodes = ray.nodes()
for node in alive_nodes:
    print(f"Node: {node['NodeManagerAddress']}")
    print(f"  Alive: {node['Alive']}")
    print(f"  Resources: {node['Resources']}")
    print(f"  ObjectStoreSocketName: {node['ObjectStoreSocketName']}")
```

### Runtime Context
```python
ctx = ray.get_runtime_context()

ctx.get_worker_id()         # Current worker ID
ctx.get_job_id()            # Current job ID
ctx.get_actor_id()          # Current actor ID (if in actor)
ctx.get_task_id()           # Current task ID
ctx.get_node_id()           # Current node ID
ctx.get_placement_group_id() # Current placement group ID
ctx.get_namespace()         # Current namespace
ctx.get_runtime_env()       # Current runtime environment
ctx.should_capture_child_tasks_in_placement_group  # PG capture flag
```

### Node Info
```python
# Get all nodes
nodes = ray.nodes()

for node in nodes:
    print(f"Address: {node['NodeManagerAddress']}")
    print(f"Alive: {node['Alive']}")
    print(f"Resources: {node['Resources']}")
    print(f"Labels: {node.get('Labels', {})}")
    print(f"State: {node.get('State', 'UNKNOWN')}")
```

## Metrics

### Prometheus Integration

Ray exposes Prometheus metrics at:
```
http://<head-ip>:8265/metrics
http://<worker-ip>:<agent-port>/metrics
```

### Key Metrics

#### Ray Core Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `ray_tasks` | Counter | Total tasks submitted |
| `ray_tasks_finished` | Counter | Tasks completed |
| `ray_tasks_failed` | Counter | Tasks failed |
| `ray_actors` | Gauge | Live actors |
| `ray_actors_restart` | Counter | Actor restarts |
| `ray_objects` | Gauge | Objects in store |
| `ray_object_store_memory` | Gauge | Object store bytes |
| `ray_object_store_spill` | Counter | Spilled objects |
| `ray_object_store_restore` | Counter | Restored objects |

#### Ray Serve Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `serve_num_requests` | Counter | Total requests |
| `serve_request_latency_ms` | Histogram | Request latency |
| `serve_num_deployment_requests` | Counter | Per-deployment requests |
| `serve_batch_size` | Histogram | Batch sizes |
| `serve_replica_starts` | Counter | Replica starts |

#### Ray Data Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `ray_data_rows_read` | Counter | Rows read |
| `ray_data_rows_written` | Counter | Rows written |
| `ray_data_bytes_spilled` | Counter | Bytes spilled |
| `ray_data_blocks` | Gauge | Current blocks |

#### Resource Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `ray_node_cpu_usage` | Gauge | CPU usage |
| `ray_node_mem_usage` | Gauge | Memory usage |
| `ray_node_gpu_usage` | Gauge | GPU usage |
| `ray_node_cpu_available` | Gauge | Available CPU |
| `ray_node_mem_available` | Gauge | Available memory |
| `ray_node_gpu_available` | Gauge | Available GPU |

### Prometheus Configuration
```yaml
scrape_configs:
  - job_name: 'ray'
    static_configs:
      - targets: ['<head-ip>:8265']
    metrics_path: '/metrics'
    scrape_interval: 10s
```

### Grafana Dashboard

Ray provides pre-built Grafana dashboards:
- **Cluster Overview**: Resource utilization across nodes
- **Task Metrics**: Task throughput and latency
- **Actor Metrics**: Actor lifecycle and resource usage
- **Object Store**: Memory usage and spilling
- **Serve Metrics**: Request throughput and latency
- **Data Metrics**: Data pipeline performance

## Logging

### Log Structure
```
/tmp/ray/session_latest/logs/
├── raylet.out              # Raylet logs
├── raylet.err
├── gcs_server.out          # GCS logs
├── gcs_server.err
├── dashboard.log           # Dashboard logs
├── dashboard_agent.log     # Agent logs
├── worker-[id]-[job].out   # Worker stdout
├── worker-[id]-[job].err   # Worker stderr
├── runtime_env-[id].log    # Runtime env setup logs
└── pb_[id].log             # Placement group logs
```

### Log Configuration
```python
import ray

ray.init(logging_level=logging.INFO)  # Python logging level
```

```bash
# Environment variables
export RAY_BACKEND_LOG_LEVEL=info
export RAY_LOG_TO_STDERR=1
export RAY_DEDUP_LOGS=0  # Disable log deduplication
```

### Structured Logging
```python
import ray
import logging

# Configure Ray worker logging
ray.init(log_to_driver=True)

# Custom logger in tasks
@ray.remote
def my_task():
    logger = logging.getLogger("my_task")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    logger.info("Task running")
```

### Log Aggregation
```python
# Access logs via dashboard API
import requests

# Get job logs
response = requests.get("http://<head>:8265/api/jobs/<job_id>/logs")
logs = response.json()

# Get actor logs
response = requests.get("http://<head>:8265/api/actors/<actor_id>/logs")
```

## Debugging Tools

### ray.debug
```python
import ray

# Get debug string for the cluster
debug_string = ray.internal.internal_api.global_state
print(debug_string)
```

### Memory Debugging
```python
# Force object store memory info
ray.memory_monitor.get_memory_info()

# Check reference counts
refs_info = ray.internal.internal_api.get_object_ref_info()
for ref_info in refs_info:
    print(f"Ref: {ref_info.object_id}")
    print(f"  Call site: {ref_info.call_site}")
    print(f"  Local ref count: {ref_info.local_ref_count}")
    print(f"  Pinned: {ref_info.pinned}")
    print(f"  Submitted task ref: {ref_info.submitted_task_ref_count}")
```

### Timeline / Profiling
```python
import ray

ray.init()

# Enable task profiling
@ray.remote
def my_task():
    pass

# Generate timeline
result = my_task.remote()
ray.get(result)

# Export timeline to Chrome trace format
ray.timeline("trace.json")
# Open chrome://tracing and load the file
```

### State API (Experimental)
```python
from ray.experimental.state.api import list_tasks, list_actors, list_objects

# List all tasks
tasks = list_tasks()
for task in tasks:
    print(f"Task: {task['task_id']}, State: {task['state']}")

# List all actors
actors = list_actors()
for actor in actors:
    print(f"Actor: {actor['actor_id']}, State: {actor['state']}")

# List objects
objects = list_objects()
for obj in objects:
    print(f"Object: {obj['object_id']}, Size: {obj['object_size']}")
```

## Dashboard REST API

### Endpoints
```
GET /api/cluster_status          # Cluster overview
GET /api/jobs/                   # List jobs
GET /api/jobs/<job_id>           # Job details
GET /api/jobs/<job_id>/logs      # Job logs
GET /api/actors/                 # List actors
GET /api/actors/<actor_id>       # Actor details
GET /api/nodes/                  # List nodes
GET /api/nodes/<node_id>         # Node details
GET /api/tasks/                  # List tasks
GET /api/placement_groups/       # List placement groups
GET /api/workers/                # List workers
GET /api/memory/                 # Memory stats
GET /api/log_files               # Log file listing
GET /api/cpu_profile             # CPU profiling
GET /api/memory_profile          # Memory profiling
GET /api/accelerator             # GPU stats
GET /metrics                     # Prometheus metrics
```

### Example Usage
```python
import requests

base_url = "http://<head>:8265"

# Get cluster status
status = requests.get(f"{base_url}/api/cluster_status").json()

# List all jobs
jobs = requests.get(f"{base_url}/api/jobs/").json()

# Get specific job
job = requests.get(f"{base_url}/api/jobs/{job_id}").json()

# Get actor details
actors = requests.get(f"{base_url}/api/actors/").json()
```

## Best Practices

1. **Enable dashboard** on head node with `--dashboard-host=0.0.0.0`
2. **Use Prometheus + Grafana** for production monitoring
3. **Set up alerts** for task failures, actor deaths, memory pressure
4. **Check object store memory** regularly to detect leaks
5. **Use State API** for programmatic cluster introspection
6. **Enable log aggregation** for multi-node debugging
7. **Use ray.timeline()** for performance profiling
8. **Monitor autoscaling** via dashboard during scale-up/down
9. **Check placement groups** status for GPU scheduling issues
10. **Use debug flags** (`RAY_DEBUG=1`, `RAY_LOG_TO_STDERR=1`) for troubleshooting
