# Runtime Environments

## Overview

Runtime environments provide dependency isolation for Ray tasks and actors, allowing different code dependencies, environment variables, and working directories to coexist in the same cluster. Each task or actor can specify its own runtime environment, which is automatically set up before execution and cleaned up afterward.

Runtime environments solve key challenges in distributed computing:
- **Dependency management**: Different tasks need different library versions
- **Code distribution**: Ship code to remote workers automatically
- **Isolation**: Prevent dependency conflicts between tasks
- **Reproducibility**: Ensure consistent execution environments

## RuntimeEnv Class

The `RuntimeEnv` class provides a typed interface for constructing runtime environments.

```python
from ray.runtime_env import RuntimeEnv

# Construct via class
env = RuntimeEnv(
    pip=["numpy==1.24.0", "pandas>=2.0", "torch"],
    conda={"dependencies": ["scipy=1.10", "python=3.10"]},
    env_vars={"OMP_NUM_THREADS": "4", "CUDA_VISIBLE_DEVICES": "0,1"},
    working_dir="./my_project",
    py_modules=["./my_module.py", "./utils/"],
    container={"image": "rayproject/ray-ml:latest-gpu"},
)

# Convert to dict
env_dict = env.to_dict()

# Construct from dict
env = RuntimeEnv.from_dict(env_dict)
```

### pip

Specify Python packages to install via pip.

```python
# List of packages (simple)
runtime_env = {
    "pip": [
        "numpy==1.24.0",
        "pandas>=2.0",
        "torch",
        "transformers>=4.30",
    ]
}

# Dict format (with options)
runtime_env = {
    "pip": {
        "packages": [
            "numpy==1.24.0",
            "pandas>=2.0",
        ],
        "pip_check": True,              # Run pip check after install
        "pip_version": "==23.3.1",      # Specific pip version
    }
}

# From requirements file
runtime_env = {
    "pip": {
        "packages": "requirements.txt",
    }
}
```

**pip Field Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `packages` | list/str | required | Package names or requirements file path |
| `pip_check` | bool | False | Run `pip check` after installation |
| `pip_version` | str | None | Specific pip version to use |

### conda

Specify a Conda environment to install.

```python
runtime_env = {
    "conda": {
        "dependencies": [
            "python=3.10",
            "scipy=1.10",
            "numpy",
            "pip",
            {"pip": ["torch", "transformers"]},  # pip within conda
        ],
        "channels": ["conda-forge", "defaults"],
    }
}

# From environment.yml file
runtime_env = {
    "conda": "environment.yml"
}
```

**Note:** Using both `pip` and `conda` together is supported. Conda is installed first, then pip packages are installed into the conda environment.

### env_vars

Set environment variables for the task/actor.

```python
runtime_env = {
    "env_vars": {
        "OMP_NUM_THREADS": "4",
        "CUDA_VISIBLE_DEVICES": "0,1",
        "MY_CONFIG": "value",
        "TOKEN": os.environ.get("API_TOKEN", ""),
        "PYTHONPATH": "/custom/path",
        "TF_ENABLE_ONEDNN_OPTS": "0",
    }
}
```

Environment variables are set in the worker process before the task/actor code runs.

### working_dir

Set the working directory for the task/actor. This directory is added to `sys.path` and set as `os.getcwd()`.

```python
# Local directory (uploaded to cluster)
runtime_env = {"working_dir": "./my_project"}

# Remote URIs (downloaded to cluster)
runtime_env = {"working_dir": "s3://bucket/code/"}
runtime_env = {"working_dir": "gs://bucket/code/"}
runtime_env = {"working_dir": "hdfs://namenode:8020/code/"}
runtime_env = {"working_dir": "https://example.com/code.tar.gz"}
```

**Supported URI schemes:**

| Scheme | Description | Example |
|--------|-------------|---------|
| (local) | Local path, uploaded to cluster | `"./my_project"` |
| `s3://` | Amazon S3 | `"s3://bucket/path/"` |
| `gs://` | Google Cloud Storage | `"gs://bucket/path/"` |
| `hdfs://` | Hadoop HDFS | `"hdfs://namenode/path/"` |
| `https://` | HTTP/HTTPS URL | `"https://host/code.tar.gz"` |

**Local path behavior:**
- Directory contents are packaged into a `.zip` file
- Uploaded to the cluster's internal storage
- Extracted on each worker node
- Added to `sys.path`

**Remote URI behavior:**
- Downloaded directly from the URI
- Supports `.zip`, `.tar.gz`, `.whl`, and plain directories
- Cached on worker nodes by URI hash

### py_modules

Ship individual Python modules or packages to workers.

```python
# Single module file
runtime_env = {"py_modules": ["./my_utils.py"]}

# Package directory
runtime_env = {"py_modules": ["./my_package/"]}

# Multiple modules
runtime_env = {"py_modules": ["./utils.py", "./helpers.py", "./transforms/"]}

# Combined with working_dir
runtime_env = {
    "working_dir": "./main_project",
    "py_modules": ["../shared_lib/"],  # Relative to working_dir
}
```

### container

Run tasks/actors inside a Docker container.

```python
runtime_env = {
    "container": {
        "image": "rayproject/ray-ml:latest-gpu",
        "run_options": [
            "--network=host",
            "--gpus=all",
            "--shm-size=4g",
        ],
        "worker_path": "/home/ray/runtime_worker.py",
    }
}
```

**Container Field Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `image` | str | required | Docker image name and tag |
| `run_options` | list | [] | Extra `docker run` options |
| `worker_path` | str | auto | Path to runtime worker script in container |
| `center_image` | bool | True | Whether image exists on all nodes |

### images

Specify container images for different purposes (experimental).

```python
runtime_env = {
    "images": {
        "my_image": "docker.io/myimage:latest",
    }
}
```

## RuntimeEnvConfig

`RuntimeEnvConfig` controls how runtime environments are set up.

```python
from ray.runtime_env import RuntimeEnvConfig

config = RuntimeEnvConfig(
    setup_timeout_seconds=600,     # Timeout for env setup
    eager_install=True,            # Install at init time
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `setup_timeout_seconds` | int | 600 | Max seconds to wait for env setup |
| `eager_install` | bool | False | Install runtime env immediately at init |

### Usage with ray.init()

```python
import ray

ray.init(
    runtime_env={
        "pip": ["torch", "transformers"],
        "env_vars": {"TOKEN": "..."},
    },
    _runtime_env_config=RuntimeEnvConfig(
        setup_timeout_seconds=900,
        eager_install=True,
    )
)
```

### Usage with Job Submission

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://head:8265")
client.submit_job(
    entrypoint="python train.py",
    runtime_env={"pip": ["torch"]},
    _runtime_env_config=RuntimeEnvConfig(
        setup_timeout_seconds=1200,
        eager_install=True,
    ),
)
```

## Inheritance

Runtime environments inherit and merge from parent to child contexts. This allows common dependencies to be set at the job level and overridden or extended at the task/actor level.

### Inheritance Hierarchy

```
1. Job-level runtime env (from ray.init() or job submission)
   |
   +-- 2. Task/actor runtime env (from @ray.remote() or .options())
       |
       +-- 3. Child tasks inherit from their parent task
           |
           +-- 4. Further child tasks continue inheriting
```

### Inheritance Rules

1. **pip**: Child packages are merged with parent packages. If the same package appears in both, the child version takes precedence.
2. **conda**: Child conda config replaces parent config entirely (no merge).
3. **env_vars**: Child env vars are merged with parent. Child values override parent values for the same keys.
4. **working_dir**: Child working_dir replaces parent working_dir entirely.
5. **py_modules**: Child py_modules are merged with parent py_modules.
6. **container**: Child container config replaces parent config entirely.

### Inheritance Example

```python
import ray

# Job-level runtime env
ray.init(runtime_env={
    "pip": ["numpy==1.24.0", "pandas"],
    "env_vars": {"LOG_LEVEL": "INFO", "REGION": "us-west"},
})

@ray.remote(runtime_env={
    "pip": ["torch"],                     # Merged: numpy, pandas, torch
    "env_vars": {"LOG_LEVEL": "DEBUG"},   # Merged: LOG_LEVEL=DEBUG, REGION=us-west
})
def parent_task():
    # Available: numpy, pandas, torch
    # LOG_LEVEL=DEBUG, REGION=us-west
    pass

@ray.remote
def child_task():
    # Inherits full parent env:
    # numpy, pandas, torch
    # LOG_LEVEL=DEBUG, REGION=us-west
    pass
```

### Job-Level Runtime Environment

```python
import ray

# Set at initialization
ray.init(runtime_env={
    "pip": ["numpy==1.24.0", "pandas"],
    "working_dir": "./project",
    "env_vars": {"OMP_NUM_THREADS": "4"},
})
```

### Task-Level Runtime Environment

```python
import ray

# Via decorator
@ray.remote(runtime_env={"pip": ["scipy"]})
def compute():
    import scipy  # Available here
    pass

# Via .options() override
result = my_task.options(
    runtime_env={"pip": ["numpy==1.24"]}
).remote()
```

### Actor-Level Runtime Environment

```python
import ray

# Via decorator
@ray.remote(runtime_env={"pip": ["tensorflow"]})
class TFModel:
    def __init__(self):
        import tensorflow as tf
        self.model = tf.keras.Model(...)

    def predict(self, x):
        return self.model.predict(x)

# Via .options() override
model = TFModel.options(
    runtime_env={
        "pip": ["tensorflow==2.15"],
        "env_vars": {"TF_CPP_MIN_LOG_LEVEL": "2"},
    }
).remote()
```

## working_dir from Remote URIs

### S3

```python
runtime_env = {"working_dir": "s3://my-bucket/code/"}

# S3 requires AWS credentials configured via:
# - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# - IAM role (EC2)
# - ~/.aws/credentials file
# - AWS profile

# With explicit credentials
import boto3
s3 = boto3.client("s3", region_name="us-west-2")
```

### Google Cloud Storage (GCS)

```python
runtime_env = {"working_dir": "gs://my-bucket/code/"}

# GCS requires Google Cloud credentials via:
# - Environment variable: GOOGLE_APPLICATION_CREDENTIALS
# - Service account (GCE/GKE)
# - gcloud auth application-default login
```

### HDFS

```python
runtime_env = {"working_dir": "hdfs://namenode:8020/path/to/code/"}

# HDFS requires:
# - Hadoop configuration in classpath
# - Kerberos ticket (if security enabled)
# - pyarrow with HDFS support
```

### Packaging Format

Remote URIs should point to one of:
- A `.zip` file containing the project directory
- A `.tar.gz` file containing the project directory
- A directory (for supported storage backends that list directories)

## pip Install and Caching

### Install Process

1. Ray computes a hash of the pip package list
2. Checks if a cached environment exists with that hash
3. If cached: symlink or copy the cached environment
4. If not cached: create a new virtualenv and install packages
5. Cache the installed environment for future use

### Caching Behavior

```python
# These two runtime envs will share the same cache entry
env1 = {"pip": ["numpy==1.24.0", "pandas"]}
env2 = {"pip": ["numpy==1.24.0", "pandas"]}

# Adding a new package creates a new cache entry
env3 = {"pip": ["numpy==1.24.0", "pandas", "torch"]}
```

### Cache Location

```
/tmp/ray/session_<id>/runtime_resources/
    pip/
        <hash>/
            virtualenv/         # Python virtualenv
            installed_packages.txt
    conda/
        <hash>/
            conda_env/
    working_dir/
        <hash>/
            package.zip         # Downloaded working dir
```

### Cache Management

```bash
# View cache usage
du -sh /tmp/ray/session_*/runtime_resources/

# Clear cache (stops Ray first)
ray stop
rm -rf /tmp/ray/session_*/runtime_resources/
```

### pip Replay

When a runtime environment with the same pip specification is requested on a worker that already has a cached version, Ray "replays" the cached environment instead of re-installing. This dramatically speeds up task startup for repeated jobs.

## Plugin System

The runtime environment system supports plugins for extending dependency management.

### Built-in Plugins

Ray ships with these built-in runtime env plugins:
- **PipPlugin**: Manages pip package installation
- **CondaPlugin**: Manages conda environment creation
- **WorkingDirPlugin**: Manages working directory setup
- **PyModulesPlugin**: Manages Python module distribution
- **EnvVarPlugin**: Manages environment variable setup
- **ContainerPlugin**: Manages Docker container setup

### Custom Plugin API

Create custom runtime environment plugins by extending the plugin interface.

```python
from ray.runtime_env.plugin import RuntimeEnvPlugin

class MyCustomPlugin(RuntimeEnvPlugin):
    name = "my_custom"

    @staticmethod
    def validate(runtime_env_dict):
        """Validate the runtime env configuration."""
        config = runtime_env_dict.get("my_custom")
        if config and "required_field" not in config:
            raise ValueError("required_field is required")

    @staticmethod
    def modify_worker_cmd(original_cmd, runtime_env_dict, ctx):
        """Modify the worker startup command."""
        config = runtime_env_dict.get("my_custom", {})
        if config.get("debug"):
            original_cmd.extend(["--debug"])
        return original_cmd

    @staticmethod
    def create(uri, runtime_env_dict, ctx, logger):
        """Create resources for the runtime environment."""
        config = runtime_env_dict.get("my_custom", {})
        # Download, setup, or configure resources
        return "my_custom_resource_path"

    @staticmethod
    def modify_context(context, runtime_env_dict):
        """Modify the worker context before task execution."""
        config = runtime_env_dict.get("my_custom", {})
        if "env_prefix" in config:
            context.env_vars["MY_PREFIX"] = config["env_prefix"]

    @staticmethod
    def delete(uri, runtime_env_dict, ctx, logger):
        """Clean up resources when no longer needed."""
        pass
```

### Registering a Plugin

```python
# Via ray.init system config
ray.init(_system_config={
    "runtime_env_plugins": {
        "my_custom": {
            "class": "my_module.MyCustomPlugin",
        }
    }
})

# Usage
ray.init(runtime_env={
    "pip": ["numpy"],
    "my_custom": {
        "required_field": "value",
        "debug": True,
    }
})
```

## Best Practices

### 1. Set Common Dependencies at Job Level

```python
# Good: common deps at job level
ray.init(runtime_env={
    "pip": ["numpy", "pandas", "scikit-learn"],
    "env_vars": {"LOG_LEVEL": "INFO"},
})

@ray.remote
def task1():
    # numpy, pandas, scikit-learn available
    pass

@ray.remote(runtime_env={"pip": ["torch"]})
def task2():
    # numpy, pandas, scikit-learn, torch available
    pass
```

### 2. Override Per Task/Actor Only When Needed

```python
# Good: minimal overrides
@ray.remote(runtime_env={"pip": ["torch"]})
class Model:
    pass

# Bad: repeating common deps
@ray.remote(runtime_env={"pip": ["numpy", "pandas", "torch"]})
class Model:
    pass
```

### 3. Pin Package Versions for Reproducibility

```python
# Good: pinned versions
runtime_env = {
    "pip": [
        "numpy==1.24.0",
        "pandas==2.0.3",
        "torch==2.1.0",
    ]
}

# Bad: unpinned versions (may break on different nodes)
runtime_env = {
    "pip": ["numpy", "pandas", "torch"]
}
```

### 4. Use working_dir for Frequently Changing Code

```python
# Good: working_dir for local code
ray.init(runtime_env={"working_dir": "./my_project"})

# This automatically:
# - Packages the directory
# - Uploads to cluster storage
# - Extracts on workers
# - Adds to sys.path
```

### 5. Use py_modules for Shared Utility Code

```python
# Good: py_modules for reusable modules
ray.init(runtime_env={
    "py_modules": ["./shared_utils/", "./preprocessing.py"],
    "working_dir": "./main_app/",
})
```

### 6. Keep Runtime Environments Small

```python
# Bad: large runtime env with everything
runtime_env = {
    "pip": [
        "torch",           # ~2GB
        "tensorflow",      # ~500MB
        "jax",             # ~500MB
        "transformers",    # ~200MB
        # ... 50 more packages
    ]
}

# Good: split into focused environments
@ray.remote(runtime_env={"pip": ["torch", "transformers"]})
class PyTorchModel:
    pass

@ray.remote(runtime_env={"pip": ["tensorflow"]})
class TFModel:
    pass
```

### 7. Use Containers for Complex System Dependencies

```python
# When you need system-level libraries (CUDA, cuDNN, etc.)
runtime_env = {
    "container": {
        "image": "rayproject/ray-ml:latest-gpu",
        "run_options": ["--gpus=all", "--shm-size=4g"],
    }
}
```

### 8. Use eager_install for Critical Dependencies

```python
from ray.runtime_env import RuntimeEnvConfig

ray.init(
    runtime_env={"pip": ["critical-package"]},
    _runtime_env_config=RuntimeEnvConfig(
        eager_install=True,  # Install immediately, fail fast
    )
)
```

### 9. Use env_vars for Configuration

```python
# Good: configure via env_vars
runtime_env = {
    "env_vars": {
        "MODEL_PATH": "/models/latest",
        "BATCH_SIZE": "32",
        "DEBUG": "false",
    }
}

# Avoid embedding config in code that gets shipped
```

### 10. Leverage Remote URIs for Large Code Bases

```python
# Good: use remote URI for large code
runtime_env = {
    "working_dir": "s3://my-bucket/code/v2.1.0/",
}

# This avoids uploading code on every job submission
# Workers download directly from S3 (with caching)
```

## Troubleshooting

### Common Issues

**Runtime env setup timeout:**
```python
# Increase timeout
ray.init(
    runtime_env={"pip": ["large-package"]},
    _runtime_env_config=RuntimeEnvConfig(
        setup_timeout_seconds=1800,  # 30 minutes
    )
)
```

**Package not found:**
```python
# Check if package was installed correctly
@ray.remote(runtime_env={"pip": ["my-package"]})
def check():
    import pip
    print(pip.get_installed_distributions())
```

**Working dir not found on worker:**
```python
# Ensure working_dir path is correct relative to driver
import os
working_dir = os.path.abspath("./my_project")
ray.init(runtime_env={"working_dir": working_dir})
```

**Conda environment conflicts:**
```python
# Use separate envs, don't mix pip and conda for same packages
runtime_env = {
    "conda": {"dependencies": ["python=3.10", "numpy"]},
    # Don't also specify "pip": ["numpy"] with different version
}
```

## Runtime Environment Internals

### Setup Flow

1. User specifies runtime env in `ray.init()`, `@ray.remote()`, or `.options()`
2. Ray serializes the runtime env spec and attaches it to the task/actor
3. When a worker is assigned to execute the task:
   a. Worker receives runtime env spec
   b. Runtime env manager checks local cache
   c. If not cached, downloads/installs dependencies
   d. Sets up working directory, env vars, etc.
   e. Worker process starts with the configured environment
4. Task/actor executes in the prepared environment
5. After completion, environment is retained in cache for reuse

### Resource Cleanup

- Cached environments are cleaned up when the Ray session ends
- Working directories and packages are shared between tasks with the same env
- Container instances are stopped when no longer needed
- Environment variables are scoped to the worker process
