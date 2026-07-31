# Ray Workflow

## Overview

Ray Workflow provides durable, fault-tolerant execution of directed acyclic graphs (DAGs) with automatic recovery from failures. Unlike regular Ray tasks, workflows persist their execution state and can survive cluster restarts.

## Key Concepts

- **Workflow Step**: A durable unit of execution (decorated function)
- **DAG**: Directed acyclic graph of workflow steps
- **Checkpointing**: Automatic state persistence after each step
- **Recovery**: Resume from last checkpointed step on failure

## Installation
```bash
pip install "ray[workflow]"
```

## Basic Usage

### Defining Workflow Steps
```python
import ray
from ray import workflow

@workflow.step
def add(a, b):
    return a + b

@workflow.step
def multiply(a, b):
    return a * b

@workflow.step
def process(data):
    return transform(data)
```

### Running a Workflow
```python
# Initialize workflow storage
workflow.init("/tmp/workflow_data")

# Simple workflow
result = add.step(1, 2).run()
print(result)  # 3

# Chained workflow
result = add.step(1, 2).continue_with(multiply, 10).run()
print(result)  # 30
```

## Step Decorator

### Parameters
```python
@workflow.step(
    max_retries=3,           # Max retry attempts
    catch_exceptions=False,  # Catch exceptions in step
    retry_exceptions=None,   # Exceptions to retry on
    num_cpus=None,           # CPU requirement
    num_gpus=None,           # GPU requirement
    memory=None,             # Memory requirement
    resources=None,          # Custom resources
)
def my_step(data):
    return result
```

### Step Options
```python
# Override options at runtime
result = (
    my_step.options(max_retries=5, num_cpus=4)
    .step(input_data)
    .run(workflow_id="my-workflow")
)
```

## DAG Construction

### Sequential Steps
```python
@workflow.step
def step1(x):
    return x + 1

@workflow.step
def step2(x):
    return x * 2

@workflow.step
def step3(x):
    return x ** 2

# Sequential composition
dag = step1.step(5)
dag = step2.step(dag)
dag = step3.step(dag)
result = dag.run()
# 5 + 1 = 6, 6 * 2 = 12, 12 ** 2 = 144
```

### continue_with
```python
@workflow.step
def double(x):
    return x * 2

@workflow.step
def add_one(x):
    return x + 1

# Chain steps with continue_with
result = double.step(5).continue_with(add_one).run()
# 5 * 2 = 10, 10 + 1 = 11
```

### Parallel Branches
```python
@workflow.step
def branch_a(data):
    return process_a(data)

@workflow.step
def branch_b(data):
    return process_b(data)

@workflow.step
def merge(result_a, result_b):
    return result_a + result_b

# Parallel branches
result = merge.step(
    branch_a.step(input_data),
    branch_b.step(input_data),
).run()
```

### Dynamic DAG (Nested Workflows)
```python
@workflow.step
def dynamic_step(items):
    # Dynamically create sub-steps
    if len(items) == 0:
        return 0
    return process_step.step(items[0]).continue_with(
        lambda result: result + dynamic_step.step(items[1:])
    )

@workflow.step
def process_step(item):
    return item * 2
```

### Loop Pattern
```python
@workflow.step
def loop_body(data, remaining):
    if remaining <= 0:
        return data
    new_data = data + 1
    return loop_body.step(new_data, remaining - 1)

result = loop_body.step(0, 10).run()
```

## Workflow Management

### Running with Workflow ID
```python
result = my_workflow.step(input_data).run(
    workflow_id="unique-workflow-id",
    # With existing workflow:
    # - If RUNNING: returns error
    # - If SUCCESSFUL: returns previous result
    # - If FAILED/RESUMABLE: resumes from checkpoint
)
```

### Resume Workflow
```python
# Resume a failed/interrupted workflow
result = workflow.resume(workflow_id="my-workflow")

# Resume all failed workflows
results = workflow.resume_all()
```

### Cancel Workflow
```python
workflow.cancel(workflow_id="my-workflow")
```

### Delete Workflow
```python
workflow.delete(workflow_id="my-workflow")
```

### List Workflows
```python
# List all workflows
workflows = workflow.list_workflows()
for wf in workflows:
    print(f"ID: {wf['workflow_id']}")
    print(f"Status: {wf['status']}")
    print(f"Start time: {wf['start_time']}")
```

### Get Workflow Status
```python
status = workflow.get_status(workflow_id="my-workflow")
# RUNNING, SUCCESSFUL, FAILED, CANCELED, RESUMABLE
```

### Get Workflow Output
```python
# Get output of completed workflow
output = workflow.get_output(workflow_id="my-workflow")

# Get output with timeout
try:
    output = workflow.get_output(
        workflow_id="my-workflow",
        timeout=30,
    )
except TimeoutError:
    print("Workflow not yet complete")
```

## Workflow Storage

### Local Storage
```python
workflow.init("/tmp/workflow_storage")
```

### S3 Storage
```python
workflow.init("s3://my-bucket/workflows")
```

### GCS Storage
```python
workflow.init("gs://my-bucket/workflows")
```

### Storage Configuration
```python
# The storage path determines where checkpoints are persisted
# Supported schemes: file://, s3://, gs://
workflow.init(
    storage="/path/to/storage",
    # Checkpointing is automatic after each step
)
```

## Workflow Status Lifecycle

```
                    ┌── SUCCESSFUL
RUNNING ────────────┤
   ↑                ├── FAILED ──── RESUMABLE
   │                │
   │                └── CANCELED
   │
   └── RESUME ──────┘
```

## Advanced Patterns

### Virtual Actor (Durable State Machine)
```python
@workflow.step
class CounterActor:
    def __init__(self, count=0):
        self.count = count

    def incr(self):
        self.count += 1
        return self.count

    def get(self):
        return self.count

# Create virtual actor
counter = CounterActor.run(count=0)

# Call methods (each call is durable)
counter.incr.run()
counter.incr.run()
result = counter.get.run()  # 2
```

### Side Effects
```python
@workflow.step
def write_to_db(data):
    # Side effects are executed exactly once
    db.write(data)
    return data

# Even with retries, write_to_db executes only once
# due to checkpoint-based deduplication
```

### Large Input Handling
```python
@workflow.step
def process_large_data(data_ref):
    # Pass ObjectRefs through workflows
    data = ray.get(data_ref)
    return process(data)

# Large data via ObjectRef
large_data = ray.put(huge_array)
result = process_large_data.step(large_data).run()
```

### Exception Handling
```python
@workflow.step(catch_exceptions=True)
def may_fail():
    if random.random() < 0.5:
        raise ValueError("Random failure")
    return "success"

result = may_fail.step().run()
# Returns either "success" or the exception object
if isinstance(result, Exception):
    print(f"Failed: {result}")
```

### Workflow Composition
```python
@workflow.step
def stage_one(data):
    return preprocess(data)

@workflow.step
def stage_two(data):
    return transform(data)

@workflow.step
def stage_three(data):
    return postprocess(data)

# Compose as a pipeline
pipeline = stage_one.step(input_data)
pipeline = pipeline.continue_with(stage_two)
pipeline = pipeline.continue_with(stage_three)
result = pipeline.run(workflow_id="pipeline-001")
```

## Best Practices

1. **Use workflow IDs** for idempotent execution and recovery
2. **Keep steps idempotent** for safe retries
3. **Use persistent storage** (S3/GCS) for production
4. **Minimize data passed between steps** - use ObjectRefs for large data
5. **Design for resumption** - assume any step can fail
6. **Use `catch_exceptions=True`** when steps may fail but you want to continue
7. **Set `max_retries`** for flaky operations
8. **Monitor workflow status** for long-running pipelines
9. **Use `resume_all()`** to recover from cluster-wide failures
10. **Clean up old workflows** to avoid storage bloat
