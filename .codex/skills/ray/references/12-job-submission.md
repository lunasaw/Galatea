# Job Submission

## Overview

Ray Job Submission provides a mechanism to submit, monitor, and manage Ray jobs on a cluster via an HTTP API or CLI. A job is a Ray application that runs on the cluster, managed by the Ray dashboard server on the head node. Jobs are submitted to the Ray dashboard, which creates a driver process on the head node (or a worker node) to execute the entrypoint command.

**Key concepts:**
- **Job**: A single execution of a Ray application identified by a submission ID
- **Submission ID**: Unique identifier for a job (auto-generated or user-specified)
- **Entrypoint**: The command to execute (e.g., `python train.py`)
- **Runtime Environment**: Dependencies and configuration for the job
- **Job Driver**: The process that executes the entrypoint command

## JobSubmissionClient API

### Creating a Client

```python
from ray.job_submission import JobSubmissionClient

# Connect to Ray dashboard
client = JobSubmissionClient("http://<head-ip>:8265")

# With default address (from RAY_ADDRESS env var)
client = JobSubmissionClient()

# With TLS
client = JobSubmissionClient(
    "https://<head-ip>:8265",
    verify=True,                         # Verify TLS certificate
)
```

**Client Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `address` | str | None | Dashboard address (http://host:port) |
| `verify` | bool/str | True | TLS verification (True/False or CA path) |
| `headers` | dict | None | HTTP headers for requests |
| `create_cluster_if_needed` | bool | False | Create cluster if not running (Ray Client mode) |
| `cookies` | dict | None | HTTP cookies |
| `metadata` | dict | None | Default metadata for all requests |

### submit_job()

Submit a job to the Ray cluster.

```python
job_id = client.submit_job(
    # Required: command to execute
    entrypoint="python train.py --epochs 10 --lr 0.001",

    # Optional: custom job ID (auto-generated if None)
    job_id=None,
    submission_id="my-training-run-001",

    # Optional: runtime environment
    runtime_env={
        "pip": ["torch==2.1.0", "transformers>=4.30"],
        "working_dir": "./",
        "env_vars": {"CUDA_VISIBLE_DEVICES": "0,1"},
        "py_modules": ["./utils/"],
    },

    # Optional: job metadata (key-value pairs)
    metadata={
        "description": "Image classification training",
        "team": "ml-team",
        "experiment_id": "exp-42",
    },

    # Optional: entrypoint resource requirements
    entrypoint_num_cpus=1,
    entrypoint_num_gpus=0,
    entrypoint_memory=None,                        # Memory in bytes
    entrypoint_resources=None,                     # Custom resources dict
    entrypoint_label_selector=None,                # Node label selector

    # Optional: runtime env configuration
    _runtime_env_config=None,                      # RuntimeEnvConfig
)
```

**submit_job Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entrypoint` | str | required | Shell command to execute |
| `job_id` | str | None | Custom job ID (deprecated, use submission_id) |
| `submission_id` | str | None | Unique submission identifier |
| `runtime_env` | dict | None | Runtime environment specification |
| `metadata` | dict | None | Arbitrary key-value metadata |
| `entrypoint_num_cpus` | float | None | CPUs for entrypoint driver |
| `entrypoint_num_gpus` | float | None | GPUs for entrypoint driver |
| `entrypoint_memory` | int | None | Memory (bytes) for entrypoint driver |
| `entrypoint_resources` | dict | None | Custom resources for driver |
| `entrypoint_label_selector` | dict | None | Node labels for driver placement |
| `_runtime_env_config` | RuntimeEnvConfig | None | Runtime env configuration |

**Returns:** `str` - The submission ID of the submitted job.

**Raises:**
- `RuntimeEnvSetupError`: If runtime env setup fails
- `ValueError`: If submission_id is already used

### Idempotent Submission

```python
# Using submission_id for idempotent job submission
# If a job with the same submission_id already exists and is not terminal,
# submit_job returns the existing job's submission_id without resubmitting.

job_id = client.submit_job(
    entrypoint="python train.py",
    submission_id="unique-run-id-001",
)
```

### get_job_status()

Get the current status of a job.

```python
status = client.get_job_status(job_id)
# Returns JobStatus enum value:
# - JobStatus.PENDING
# - JobStatus.RUNNING
# - JobStatus.SUCCEEDED
# - JobStatus.FAILED
# - JobStatus.STOPPED
```

### get_job_info()

Get detailed information about a job.

```python
info = client.get_job_info(job_id)

# Job info fields
info.entrypoint           # "python train.py --epochs 10"
info.status               # JobStatus.RUNNING
info.message              # Human-readable status message
info.error_type           # Error type if failed (e.g., "RuntimeEnvSetupError")
info.start_time           # Unix timestamp when job started
info.end_time             # Unix timestamp when job ended (None if running)
info.runtime_env          # Runtime env dict used for this job
info.metadata             # Job metadata dict
info.submission_id        # Submission ID string
info.job_id               # Internal Ray job ID
info.driver_agent_http_address  # Dashboard agent address
info.driver_node_id      # Node ID where driver is running
info.driver_pid          # Process ID of driver
info.driver_info         # Additional driver information

# Type annotation
from ray.job_submission import JobInfo
```

**JobInfo Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `entrypoint` | str | The command being executed |
| `status` | JobStatus | Current job status |
| `message` | str | Status description |
| `error_type` | str | Error classification if failed |
| `start_time` | float | Unix timestamp of start |
| `end_time` | float | Unix timestamp of end |
| `runtime_env` | dict | Runtime environment used |
| `metadata` | dict | Job metadata |
| `submission_id` | str | Submission identifier |
| `job_id` | str | Internal Ray job ID |
| `driver_agent_http_address` | str | Dashboard agent URL |
| `driver_node_id` | str | Node ID of driver |
| `driver_pid` | int | Driver process ID |
| `driver_info` | dict | Additional driver details |

### get_job_logs()

Get the stdout/stderr logs of a job.

```python
# Get all logs (blocking)
logs = client.get_job_logs(job_id)
print(logs)

# Logs are returned as a string containing stdout and stderr
```

**Returns:** `str` - Complete job logs.

### tail_job_logs()

Stream job logs in real-time (async generator).

```python
import asyncio

async def watch_logs(job_id):
    async for line in client.tail_job_logs(job_id):
        print(line, end="")

# Run the async function
asyncio.run(watch_logs(job_id))

# Or use in an async context
async def main():
    job_id = client.submit_job(
        entrypoint="python train.py",
        runtime_env={"pip": ["torch"]},
    )

    async for line in client.tail_job_logs(job_id):
        print(line)
        if "Training complete" in line:
            break
```

**Returns:** `AsyncIterator[str]` - Async iterator of log lines.

### stop_job()

Stop a running job.

```python
# Stop a job
client.stop_job(job_id)

# Stop with graceful shutdown
client.stop_job(job_id)

# The job driver process is sent SIGTERM
# If it doesn't stop, it's killed after a timeout
```

**Returns:** `None`

### delete_job()

Delete a completed job's record from the cluster.

```python
# Delete a job (must be in terminal state)
client.delete_job(job_id)

# Raises error if job is still running
```

**Returns:** `None`

**Raises:** `RuntimeError` if job is not in a terminal state.

### list_jobs()

List all jobs on the cluster.

```python
# List all jobs
jobs = client.list_jobs()
for job in jobs:
    print(f"  {job.submission_id}: {job.status} - {job.entrypoint}")

# Filter by status
from ray.job_submission import JobStatus
running_jobs = [j for j in jobs if j.status == JobStatus.RUNNING]

# Each job is a JobInfo object with all fields
for job in jobs:
    print(f"ID: {job.submission_id}")
    print(f"  Status: {job.status}")
    print(f"  Entrypoint: {job.entrypoint}")
    print(f"  Start: {job.start_time}")
    print(f"  End: {job.end_time}")
    print(f"  Runtime Env: {job.runtime_env}")
    print(f"  Metadata: {job.metadata}")
```

**Returns:** `List[JobInfo]` - List of all jobs.

### get_job_status_cluster

```python
# Get aggregated cluster-level job status
result = client.get_job_status_cluster()
```

### Other Client Methods

```python
# Check if dashboard is reachable
client.check_address("http://head:8265")  # Returns bool

# Get cluster info
info = client.get_cluster_status_info()

# Get dashboard info
info = client.get_dashboard_info()
```

## Job Status Lifecycle

### Status Flow

A Ray job goes through the following lifecycle:

```
submit_job()
     |
     v
  PENDING  ----------->  RUNNING  ----------->  SUCCEEDED
                        (driver starts)          (exit code 0)
                              |
                              +---------------->  FAILED
                              |                   (non-zero exit code
                              |                    or exception)
                              |
                              +---------------->  STOPPED
                                                  (stop_job() called)
```

### Status Descriptions

| Status | Description |
|--------|-------------|
| `PENDING` | Job has been submitted but not yet started. Runtime env is being set up. |
| `RUNNING` | Job driver process is executing the entrypoint command. |
| `SUCCEEDED` | Job completed successfully (exit code 0). |
| `FAILED` | Job failed due to application error, runtime env error, or system error. |
| `STOPPED` | Job was manually stopped via `stop_job()`. |

### Status Transition Details

**PENDING -> RUNNING:**
- Runtime environment is installed on the head node
- Driver process is created
- Ray is initialized within the driver process

**RUNNING -> SUCCEEDED:**
- Entrypoint command exits with code 0
- All tasks and actors have completed

**RUNNING -> FAILED:**
- Entrypoint command exits with non-zero code
- Unhandled exception in driver
- Runtime environment setup fails
- Out of memory or system error

**RUNNING -> STOPPED:**
- `stop_job()` called by user
- Driver receives SIGTERM, then SIGKILL if unresponsive

### Monitoring Job Status

```python
import time
from ray.job_submission import JobSubmissionClient, JobStatus

client = JobSubmissionClient("http://head:8265")

job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={"pip": ["torch"]},
)

# Poll for completion
while True:
    status = client.get_job_status(job_id)
    print(f"Job {job_id}: {status}")

    if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.STOPPED}:
        break

    time.sleep(5)

# Get final result
info = client.get_job_info(job_id)
if status == JobStatus.SUCCEEDED:
    print("Job succeeded!")
    print(client.get_job_logs(job_id))
elif status == JobStatus.FAILED:
    print(f"Job failed: {info.error_type}")
    print(f"Message: {info.message}")
    print(client.get_job_logs(job_id))
```

## Job Drivers

### What is a Job Driver?

A job driver is the process that executes the entrypoint command. It runs on the head node by default and is responsible for:
- Initializing Ray within the job
- Creating tasks and actors
- Coordinating distributed execution
- Collecting results

### Driver Placement

```python
# Default: driver runs on head node
client.submit_job(entrypoint="python train.py")

# Specify resources for driver (runs on a node with those resources)
client.submit_job(
    entrypoint="python train.py",
    entrypoint_num_cpus=4,
    entrypoint_num_gpus=1,
    entrypoint_memory=8 * 1024 * 1024 * 1024,
    entrypoint_resources={"TPU": 4},
    entrypoint_label_selector={"region": "us-west"},
)
```

### Driver Behavior

- The driver is a Python subprocess that runs the entrypoint command
- It initializes `ray.init(address="auto")` automatically
- The driver's stdout and stderr are captured as job logs
- If the driver crashes, the job transitions to FAILED
- The driver can create tasks, actors, and use all Ray APIs

### Driver with GPU

```python
# Reserve GPU for the driver process itself
client.submit_job(
    entrypoint="python train.py",
    entrypoint_num_gpus=1,
    runtime_env={"pip": ["torch"]},
)
```

## Runtime Environment for Jobs

### Specifying Runtime Environment

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://head:8265")

# pip packages
job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "pip": [
            "torch==2.1.0",
            "transformers>=4.30",
            "datasets",
            "accelerate",
        ],
    },
)

# With working directory
job_id = client.submit_job(
    entrypoint="python src/train.py",
    runtime_env={
        "working_dir": "./",
        "pip": ["torch", "scikit-learn"],
    },
)

# With remote working directory
job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "working_dir": "s3://my-bucket/code/",
        "pip": ["torch"],
    },
)

# With environment variables
job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "pip": ["torch"],
        "env_vars": {
            "WANDB_API_KEY": "...",
            "CUDA_VISIBLE_DEVICES": "0,1,2,3",
            "TOKENIZERS_PARALLELISM": "false",
        },
    },
)

# With conda
job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "conda": {
            "dependencies": ["python=3.10", "scipy", "numpy"],
            "channels": ["conda-forge"],
        },
    },
)

# Combined runtime environment
job_id = client.submit_job(
    entrypoint="python train.py --epochs 50",
    runtime_env={
        "working_dir": "./my_project",
        "py_modules": ["../shared_lib/"],
        "pip": ["torch==2.1.0", "transformers"],
        "env_vars": {
            "OMP_NUM_THREADS": "4",
            "WANDB_PROJECT": "my-project",
        },
    },
    metadata={
        "experiment": "bert-finetune",
        "team": "nlp",
    },
    submission_id="bert-finetune-v3",
)
```

### Runtime Environment Setup Time

Runtime environment setup happens during the PENDING phase. The time depends on:
- Number and size of pip/conda packages
- Network speed for downloading packages
- Whether packages are cached from previous jobs
- Size of working_dir upload

```python
# Speed up by reusing cached environments
from ray.runtime_env import RuntimeEnvConfig

client.submit_job(
    entrypoint="python train.py",
    runtime_env={"pip": ["torch"]},
    _runtime_env_config=RuntimeEnvConfig(
        setup_timeout_seconds=1800,  # Allow 30 minutes for setup
        eager_install=True,          # Install immediately
    ),
)
```

## CLI Commands

### ray job submit

Submit a job to the cluster.

```bash
ray job submit \
    --address=http://<head-ip>:8265 \
    --entrypoint="python train.py --epochs 10" \
    --runtime-env-json='{"pip": ["torch", "transformers"]}' \
    --submission-id="my-job-001" \
    --entrypoint-num-cpus=2 \
    --entrypoint-num-gpus=1 \
    --entrypoint-memory=8589934592 \
    --metadata-json='{"team": "ml", "experiment": "v1"}' \
    --runtime-env working_dir="./" \
    --no-wait
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--address` | str | None | Dashboard address (or RAY_ADDRESS env var) |
| `--entrypoint` | str | required | Command to execute |
| `--submission-id` | str | None | Unique submission ID |
| `--runtime-env-json` | str | None | Runtime env as JSON string |
| `--runtime-env` | str | None | Runtime env key=value pairs (repeatable) |
| `--metadata-json` | str | None | Metadata as JSON string |
| `--entrypoint-num-cpus` | float | None | CPUs for driver |
| `--entrypoint-num-gpus` | float | None | GPUs for driver |
| `--entrypoint-memory` | int | None | Memory (bytes) for driver |
| `--entrypoint-resources` | str | None | Custom resources as JSON |
| `--entrypoint-label-selector` | str | None | Node labels as JSON |
| `--no-wait` | flag | False | Don't wait for job completion |
| `-v` / `--verbose` | flag | False | Verbose output |
| `--gcs-address` | str | None | GCS address (alternative to address) |

**Examples:**

```bash
# Basic submission
ray job submit --address=http://localhost:8265 \
    --entrypoint="python script.py"

# With pip packages
ray job submit \
    --address=http://localhost:8265 \
    --runtime-env-json='{"pip": ["numpy", "pandas"]}' \
    --entrypoint="python analysis.py"

# With working directory
ray job submit \
    --address=http://localhost:8265 \
    --runtime-env working_dir="./my_project" \
    --runtime-env-json='{"pip": ["torch"]}' \
    --entrypoint="python train.py"

# With submission ID (idempotent)
ray job submit \
    --address=http://localhost:8265 \
    --submission-id="unique-run-id" \
    --entrypoint="python train.py"

# With GPU resources
ray job submit \
    --address=http://localhost:8265 \
    --entrypoint-num-gpus=1 \
    --runtime-env-json='{"pip": ["torch"]}' \
    --entrypoint="python train.py --use-gpu"

# Non-blocking submission
ray job submit \
    --address=http://localhost:8265 \
    --no-wait \
    --entrypoint="python long_running.py"

# Using RAY_ADDRESS environment variable
export RAY_ADDRESS=http://localhost:8265
ray job submit --entrypoint="python script.py"
```

### ray job status

Get the status of a submitted job.

```bash
ray job status <job_id>
ray job status --address=http://<head>:8265 <job_id>
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `job_id` | positional | required | Submission ID of the job |
| `--address` | str | None | Dashboard address |
| `--gcs-address` | str | None | GCS address |

**Output Example:**
```
Job status for 'my-job-001'
Status: SUCCEEDED
Entrypoint: python train.py --epochs 10
Start time: 2024-01-15 10:30:00
End time: 2024-01-15 10:45:23
Runtime env: {"pip": ["torch"]}
```

### ray job logs

Get or stream logs for a job.

```bash
# Get all logs
ray job logs <job_id>

# Stream logs in real-time (follow)
ray job logs -f <job_id>

# Get last N lines
ray job logs --tail 100 <job_id>

# With address
ray job logs --address=http://<head>:8265 <job_id>
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `job_id` | positional | required | Submission ID of the job |
| `-f` / `--follow` | flag | False | Stream logs in real-time |
| `--tail` | int | None | Number of lines from end |
| `--address` | str | None | Dashboard address |
| `--gcs-address` | str | None | GCS address |

**Examples:**

```bash
# View current logs
ray job logs my-training-job

# Stream logs as they arrive
ray job logs -f my-training-job

# Last 50 lines
ray job logs --tail 50 my-training-job

# Combine follow with tail (stream from last 100 lines)
ray job logs -f --tail 100 my-training-job
```

### ray job stop

Stop a running job.

```bash
ray job stop <job_id>
ray job stop --address=http://<head>:8265 <job_id>
ray job stop --no-wait <job_id>
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `job_id` | positional | required | Submission ID of the job |
| `--address` | str | None | Dashboard address |
| `--no-wait` | flag | False | Don't wait for job to stop |
| `--gcs-address` | str | None | GCS address |

### ray job list

List all jobs on the cluster.

```bash
# List all jobs
ray job list

# Filter by status
ray job list --status RUNNING
ray job list --status SUCCEEDED
ray job list --status FAILED

# With address
ray job list --address=http://<head>:8265
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--address` | str | None | Dashboard address |
| `--status` | str | None | Filter by status (PENDING, RUNNING, SUCCEEDED, FAILED, STOPPED) |
| `--gcs-address` | str | None | GCS address |
| `--limit` | int | None | Maximum number of jobs to list |

**Output Example:**
```
Jobs:
  my-job-001   SUCCEEDED   python train.py --epochs 10
  my-job-002   RUNNING     python eval.py
  my-job-003   FAILED      python predict.py
```

### ray job delete

Delete a completed job's record.

```bash
ray job delete <job_id>
ray job delete --address=http://<head>:8265 <job_id>
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `job_id` | positional | required | Submission ID of the job |
| `--address` | str | None | Dashboard address |
| `--gcs-address` | str | None | GCS address |

**Note:** Only terminal jobs (SUCCEEDED, FAILED, STOPPED) can be deleted.

## Complete Job Submission Examples

### Training Job with GPU

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://head:8265")

job_id = client.submit_job(
    entrypoint="python train.py --model bert-base --epochs 50 --lr 2e-5",
    submission_id="bert-training-v1",
    runtime_env={
        "working_dir": "./",
        "pip": [
            "torch==2.1.0",
            "transformers>=4.35",
            "datasets>=2.14",
            "accelerate>=0.24",
            "wandb",
        ],
        "env_vars": {
            "WANDB_API_KEY": "...",
            "WANDB_PROJECT": "bert-finetune",
            "CUDA_VISIBLE_DEVICES": "0,1,2,3",
        },
    },
    entrypoint_num_cpus=2,
    entrypoint_num_gpus=4,
    entrypoint_memory=16 * 1024 * 1024 * 1024,
    metadata={
        "experiment": "bert-base-finetune",
        "team": "nlp",
        "dataset": "imdb",
    },
)

print(f"Submitted job: {job_id}")
```

### Data Processing Pipeline

```python
from ray.job_submission import JobSubmissionClient, JobStatus
import time

client = JobSubmissionClient("http://head:8265")

# Submit data processing job
job_id = client.submit_job(
    entrypoint="python process_data.py --input s3://bucket/raw/ --output s3://bucket/processed/",
    runtime_env={
        "pip": ["ray[data]", "pandas", "pyarrow"],
        "working_dir": "./pipeline/",
    },
    metadata={"type": "data-processing"},
)

# Monitor with log streaming
import asyncio

async def monitor():
    async for line in client.tail_job_logs(job_id):
        print(f"[{job_id}] {line}")

asyncio.run(monitor())
```

### Hyperparameter Tuning Job

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://head:8265")

job_id = client.submit_job(
    entrypoint="python tune.py --num-samples 100 --max-concurrent 20",
    submission_id="tune-experiment-42",
    runtime_env={
        "working_dir": "./tune_project/",
        "pip": [
            "ray[tune]>=2.47",
            "optuna",
            "torch",
        ],
        "env_vars": {
            "RAY_ADDRESS": "auto",
        },
    },
    entrypoint_num_cpus=4,
    metadata={
        "experiment_type": "hyperparameter-tuning",
        "search_algorithm": "optuna",
    },
)
```

### Batch Inference Job

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://head:8265")

job_id = client.submit_job(
    entrypoint="python batch_predict.py --model-path /models/latest --data-path s3://bucket/test/",
    runtime_env={
        "pip": ["torch", "transformers", "ray[serve]"],
        "working_dir": "s3://bucket/code/batch_inference/",
    },
    entrypoint_num_gpus=2,
    metadata={"job_type": "batch-inference"},
)
```

## Best Practices

### 1. Use submission_id for Idempotency

```python
# Good: idempotent submission
client.submit_job(
    entrypoint="python train.py",
    submission_id="unique-experiment-id",
)
# If called again with same submission_id, returns existing job

# Bad: no idempotency
client.submit_job(
    entrypoint="python train.py",
)
# Multiple calls create duplicate jobs
```

### 2. Set Runtime Environment Explicitly

```python
# Good: explicit runtime env
client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "pip": [
            "torch==2.1.0",
            "transformers==4.35.0",
        ],
        "working_dir": "./",
    },
)

# Bad: relies on pre-installed packages (not reproducible)
client.submit_job(entrypoint="python train.py")
```

### 3. Use Metadata for Organization

```python
client.submit_job(
    entrypoint="python train.py",
    metadata={
        "experiment_id": "exp-42",
        "team": "nlp",
        "description": "BERT fine-tuning on IMDB",
        "dataset": "imdb-v2",
        "author": "researcher@example.com",
    },
)
```

### 4. Stream Logs for Long-Running Jobs

```python
# Don't poll get_job_logs() repeatedly
# Use tail_job_logs() for streaming
async def monitor(job_id):
    async for line in client.tail_job_logs(job_id):
        print(line)
```

### 5. Handle Failed Jobs Properly

```python
from ray.job_submission import JobStatus

info = client.get_job_info(job_id)
if info.status == JobStatus.FAILED:
    print(f"Job failed: {info.error_type}")
    print(f"Message: {info.message}")
    logs = client.get_job_logs(job_id)
    # Parse logs for error details
    # Implement retry logic if needed
```

### 6. Use entrypoint Resources Appropriately

```python
# Reserve resources for the driver only if it needs them
client.submit_job(
    entrypoint="python train.py",
    entrypoint_num_cpus=2,   # Driver needs CPUs for data loading
    entrypoint_num_gpus=0,   # Driver doesn't need GPU (workers do)
)
```

### 7. Clean Up Completed Jobs

```python
# Delete old completed jobs to free resources
jobs = client.list_jobs()
for job in jobs:
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.STOPPED}:
        client.delete_job(job.submission_id)
```

### 8. Use Environment Variables for Secrets

```python
# Good: use env_vars for secrets
client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "env_vars": {
            "WANDB_API_KEY": os.environ["WANDB_API_KEY"],
            "AWS_ACCESS_KEY_ID": os.environ["AWS_ACCESS_KEY_ID"],
        },
    },
)

# Bad: hardcode secrets in entrypoint or code
client.submit_job(
    entrypoint="python train.py --api-key=sk-xxx",
)
```

### 9. Set Adequate Timeout for Large Environments

```python
from ray.runtime_env import RuntimeEnvConfig

client.submit_job(
    entrypoint="python train.py",
    runtime_env={"pip": ["torch", "transformers", "datasets"]},
    _runtime_env_config=RuntimeEnvConfig(
        setup_timeout_seconds=1800,  # 30 minutes for large installs
    ),
)
```

### 10. Use Remote Working Directory for Large Codebases

```python
# Good: use remote URI (avoid upload on each submission)
client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "working_dir": "s3://my-bucket/code/latest/",
        "pip": ["torch"],
    },
)

# Slow: upload local dir every time
client.submit_job(
    entrypoint="python train.py",
    runtime_env={
        "working_dir": "./large_codebase/",
        "pip": ["torch"],
    },
)
```

## Error Types

When a job fails, the `error_type` field in `JobInfo` indicates the category:

| Error Type | Description |
|-----------|-------------|
| `RuntimeEnvSetupError` | Runtime environment installation failed |
| `ApplicationError` | Application code raised an unhandled exception |
| `DriverError` | Job driver process crashed |
| `NodeDeathError` | Node running the driver died |
| `UnknownError` | Unrecognized error occurred |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RAY_ADDRESS` | Default dashboard address for CLI | None |
| `RAY_JOB_ID` | Job ID for the current job | Auto-generated |
| `RAY_RUNTIME_ENV` | Default runtime env as JSON | None |
| `RAY_JOB_SUBMISSION_MAX_RETRIES` | Max retries for submission | 3 |
| `RAY_JOB_SUBMISSION_RETRY_DELAY_S` | Delay between retries | 1 |
