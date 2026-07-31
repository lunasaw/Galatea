# CLI Reference

## Overview

Ray provides a comprehensive command-line interface for cluster management, job submission, and system operations.

## Global Options
```bash
ray <command> [options]
--address           # Ray cluster address
--logging-level     # Logging level (debug, info, warning, error)
--logging-format    # Log format string
--verbose / -v      # Verbose output
```

## Cluster Management

### ray start
```bash
# Start head node
ray start --head [options]

# Options
--head                           # Start as head node
--address=<ip>:<port>            # Address of existing head (for worker)
--port=6379                      # GCS port
--node-ip-address=<ip>           # Node IP address
--dashboard-host=0.0.0.0        # Dashboard bind host
--dashboard-port=8265            # Dashboard port
--dashboard-agent-listen-port    # Agent listen port
--num-cpus=N                     # Number of CPUs
--num-gpus=N                     # Number of GPUs
--memory=<bytes>                 # System memory in bytes
--object-store-memory=<bytes>    # Object store memory
--resources='{"TPU": 4}'        # Custom resources (JSON)
--redis-password=<password>      # Redis/GCS password
--block                          # Block until Ray exits
--verbose                        # Verbose output
--temp-dir=/tmp/ray              # Temp directory
--storage=<uri>                  # Storage URI
--head-node-port-check-intensity # Port check strictness
--disable-usage-stats            # Disable usage stats
--system-config=<json>           # System configuration
--label=<json>                   # Node labels
--redis-shard-ports              # Redis shard ports
--redis-max-clients              # Max Redis clients
--autoscaling-config=<file>      # Autoscaler config
--metrics-export-port=<port>     # Metrics export port
--no-monitor                     # Disable autoscaler monitor
--tracing-startup-hook=<module>  # Tracing hook
--node-name=<name>               # Node name

# TLS options
--tls-cert-file=<path>
--tls-key-file=<path>
--tls-ca-file=<path>

# Runtime env
--runtime-env=<json>             # Runtime environment
--runtime-env-json=<json>        # Runtime env as JSON
--working-dir=<dir>              # Working directory
--py-module=<module>             # Python module
```

### ray stop
```bash
# Stop Ray on current node
ray stop

# Force stop (kill processes)
ray stop -f
ray stop --force

# Graceful stop with timeout
ray stop --grace-period=30
```

### ray status
```bash
# Cluster status overview
ray status

# Verbose status
ray status -v
ray status --verbose

# Address
ray status --address=http://head:8265
```

## Job Management

### ray job submit
```bash
ray job submit [options] -- <command>

# Options
--address=http://<head>:8265     # Dashboard address
--submission-id=<id>             # Job submission ID
--entrypoint=<cmd>               # Entrypoint command
--runtime-env=<file>             # Runtime env YAML/JSON file
--runtime-env-json=<json>        # Runtime env JSON
--entrypoint-num-cpus=N          # CPU requirement
--entrypoint-num-gpus=N          # GPU requirement
--entrypoint-memory=<bytes>      # Memory requirement
--metadata-json=<json>           # Job metadata
--no-wait                        # Don't wait for completion
--ray-logs-dir=<dir>             # Logs directory
--timeout=<seconds>              # Submission timeout
--verbose                        # Verbose output

# Examples
ray job submit --address=http://head:8265 \
    --runtime-env-json='{"pip": ["torch"]}' \
    -- python train.py --epochs 10

ray job submit --submission-id=my-job \
    --entrypoint="python -u train.py" \
    --no-wait
```

### ray job status
```bash
ray job status <job_id>
ray job status --address=http://head:8265 <job_id>
```

### ray job logs
```bash
# Get logs
ray job logs <job_id>

# Stream logs
ray job logs -f <job_id>
ray job logs --follow <job_id>

# Last N lines
ray job logs --tail 100 <job_id>

# Address
ray job logs --address=http://head:8265 <job_id>
```

### ray job stop
```bash
ray job stop <job_id>
ray job stop --address=http://head:8265 <job_id>
ray job stop --no-wait <job_id>
```

### ray job list
```bash
ray job list
ray job list --address=http://head:8265
ray job list --status RUNNING
```

### ray job delete
```bash
ray job delete <job_id>
ray job delete --address=http://head:8265 <job_id>
```

## Serve Commands

### ray serve start
```bash
# Start Serve on cluster
ray serve start [options]
--address=http://head:8265       # Dashboard address
--http-host=0.0.0.0              # HTTP proxy host
--http-port=8000                 # HTTP proxy port
--http-options=<json>            # HTTP options JSON
```

### ray serve shutdown
```bash
ray serve shutdown
ray serve shutdown --address=http://head:8265
```

### ray serve status
```bash
ray serve status
ray serve status --address=http://head:8265
```

### ray serve config
```bash
# Deploy from config file
ray serve deploy config.yaml
ray serve deploy --address=http://head:8265 config.yaml

# Get current config
ray serve config
ray serve config --address=http://head:8265
```

## Data Commands

### ray data
```bash
# Inspect a dataset
ray data inspect <path>
ray data stats <path>

# Convert data formats
ray data copy --input-format parquet --output-format csv input/ output/
```

## Cluster Operations (ray up/down/exec)

### ray up
```bash
# Create or update cluster
ray up cluster.yaml

# Options
--no-restart              # Don't restart Ray on nodes
--restart-only            # Only restart Ray, don't update
--min-workers=N           # Minimum workers
--max-workers=N           # Maximum workers
--no-config-cache         # Don't cache config
--cluster-name=<name>     # Cluster name override
--verbose                 # Verbose output
```

### ray down
```bash
# Tear down cluster
ray down cluster.yaml

# Options
--workers-only            # Only terminate workers
--keep-head               # Keep head node
--cluster-name=<name>     # Cluster name override
```

### ray exec
```bash
# Execute command on head
ray exec cluster.yaml "python script.py"

# Execute on all nodes
ray exec cluster.yaml "command" --all-nodes

# Options
--stop                    # Stop Ray after command
--start                   # Start Ray before command
--screen                  # Run in screen session
--tmux                    # Run in tmux session
--cluster-name=<name>     # Cluster name
--port-forward=<ports>    # Port forwarding
```

### ray attach
```bash
# SSH to head node
ray attach cluster.yaml

# Options
--cluster-name=<name>
--tmux                   # Attach via tmux
--screen                 # Attach via screen
--new                    # Create new session
```

### ray rsync_up / ray rsync_down
```bash
# Upload files
ray rsync_up cluster.yaml <local> <remote>

# Download files
ray rsync_down cluster.yaml <remote> <local>

# Options
--cluster-name=<name>
--all-nodes              # Sync to all nodes
--no-config-cache
--verbose
```

### ray submit
```bash
# Submit script to cluster
ray submit cluster.yaml script.py [args]

# Options
--stop                   # Stop cluster after
--start                  # Start cluster before
--screen                 # Run in screen
--tmux                   # Run in tmux
--cluster-name=<name>
--remote-runner-kwargs=<json>
```

### ray dashboard
```bash
# Open dashboard in browser
ray dashboard cluster.yaml

# Options
--cluster-name=<name>
--port=<port>            # Local port
--no-open                # Don't open browser
```

## Utility Commands

### ray list
```bash
# List resources
ray list tasks            # List tasks
ray list actors           # List actors
ray list objects          # List objects
ray list nodes            # List nodes
ray list placement-groups # List placement groups
ray list workers          # List workers

# Options
--address=<address>
--format=<format>         # Output format (table, json, yaml)
--filter=<filter>         # Filter expression
--limit=<n>               # Max results
--detail                  # Detailed output
```

### ray debug
```bash
# Interactive debugging
ray debug
```

### ray memory
```bash
# Memory usage info
ray memory
ray memory --address=http://head:8265
```

### ray stack
```bash
# Dump worker stacks
ray stack
ray stack --address=http://head:8265
```

### ray timeline
```bash
# Generate timeline
ray timeline
```

### ray gc
```bash
# Trigger garbage collection
ray gc
```

### ray check
```bash
# Check Ray installation
ray check
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `RAY_ADDRESS` | Default cluster address |
| `RAY_NAMESPACE` | Default namespace |
| `RAY_JOB_ID` | Job ID |
| `RAY_RUNTIME_ENV` | Runtime env JSON |
| `RAY_DISABLE_MEMORY_MONITOR` | Disable memory monitor |
| `RAY_BACKEND_LOG_LEVEL` | Backend log level |
| `RAY_LOG_TO_STDERR` | Log to stderr |
| `RAY_DEDUP_LOGS` | Enable log deduplication |
| `RAY_DEBUG` | Enable debug mode |
| `RAY_COLOR_PREFIX` | Color log prefix |
| `RAY_USAGE_STATS_ENABLED` | Usage stats |
| `RAY_ENABLE_RECORD_ACTOR_HANDLER_TASK` | Record actor handler tasks |
| `RAY_SERVE_ENABLE_EXPERIMENTAL_STREAMING` | Enable Serve streaming |
| `RAY_DISABLE_DOCKER_CPU_WARNING` | Disable Docker CPU warning |
| `RAY_node_manager_forwarding_enabled` | Node manager forwarding |

## Exit Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1 | General error |
| 2 | Usage error |
| 128 | Signal exit |

## Best Practices

1. **Use `--address`** to specify cluster for remote operations
2. **Use `ray job submit`** for production job submission
3. **Use `ray status`** to verify cluster health
4. **Set `RAY_ADDRESS`** env var to avoid repeating `--address`
5. **Use `--verbose`** for debugging CLI issues
6. **Use `ray job logs -f`** for real-time log streaming
7. **Use `ray list`** for programmatic resource inspection
