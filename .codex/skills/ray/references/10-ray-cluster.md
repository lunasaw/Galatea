# Ray Cluster - Architecture, Configuration, and Management

## Overview

A Ray cluster is a set of connected worker nodes that execute distributed Ray applications. The cluster consists of a head node that runs the Global Control Service (GCS) and worker nodes that execute tasks and actors. Ray provides automatic autoscaling to dynamically adjust cluster size based on workload demands.

## Cluster Architecture

### Head Node

The head node is the central coordinator of the Ray cluster. It runs the following critical services:

- **GCS (Global Control Service)**: Centralized metadata store managing cluster state
  - Job table: Tracks all running and completed jobs
  - Actor table: Tracks actor creation, state, and locations
  - Placement group table: Manages placement group state
  - Node table: Tracks cluster membership and node health
  - Pub/Sub: Event notification system for cluster changes
  - Worker table: Tracks worker process registrations

- **Dashboard**: Web UI at `http://<head-ip>:8265` for cluster monitoring
- **Raylet**: Local task scheduler and resource manager
- **Object Store**: Plasma shared-memory store
- **Autoscaler**: Monitors resource demand and scales the cluster (v1 and v2)

### Worker Nodes

Worker nodes join the cluster and execute tasks and actors:

- **Raylet**: Local task scheduler, resource manager, worker pool
- **Object Store**: Local Plasma store for data
- **Workers**: Task workers, actor workers, I/O workers
- **Dashboard Agent**: Reports metrics to head node dashboard

### Communication Flow

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
|   Dashboard      |                        |   - Actor Workers|
|   - Web UI       |                        |   - I/O Workers  |
+------------------+                        +------------------+
|   Autoscaler     |                        |   Dashboard Agent|
|   - V1 / V2      |                        +------------------+
+------------------+
```

### Cluster Lifecycle

1. **Head node starts**: GCS initializes, raylet starts, dashboard launches
2. **Worker nodes join**: Connect to GCS via `--address`, register resources
3. **Job submission**: Jobs registered with GCS, driver process starts
4. **Task scheduling**: Tasks distributed to workers based on resources and locality
5. **Autoscaling**: Cluster scales up/down based on resource demand
6. **Shutdown**: Workers disconnect, head node stops GCS

## CLI Commands

### ray start --head

Start a head node to create a new Ray cluster.

```bash
ray start --head \
    --port=6379 \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=8265 \
    --num-cpus=8 \
    --num-gpus=4 \
    --object-store-memory=1000000000 \
    --head-node-ip-address=192.168.1.100 \
    --node-ip-address=192.168.1.100 \
    --block \
    --verbose
```

**All Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--head` | flag | False | Start as head node |
| `--port` | int | 6379 | Port for GCS server |
| `--node-ip-address` | str | auto | IP address of this node |
| `--head-node-ip-address` | str | auto | Head node IP (same as node-ip for head) |
| `--address` | str | None | Address of existing head node (for worker) |
| `--dashboard-host` | str | 127.0.0.1 | Dashboard bind host (use 0.0.0.0 for external) |
| `--dashboard-port` | int | 8265 | Dashboard port |
| `--dashboard-agent-listen-port` | int | 52365 | Port for dashboard agent |
| `--num-cpus` | int | auto | Number of CPUs on this node |
| `--num-gpus` | int | auto | Number of GPUs on this node |
| `--memory` | int | auto | Memory in bytes available |
| `--object-store-memory` | int | auto | Object store memory in bytes |
| `--redis-password` | str | None | Password for GCS (deprecated, use --gcs-password) |
| `--storage` | str | None | Persistent storage URI |
| `--temp-dir` | str | /tmp/ray | Temporary directory |
| `--system-config` | str | None | JSON string of system config overrides |
| `--enable-dashboard` | flag | True | Enable dashboard |
| `--disable-usage-stats` | flag | False | Disable usage statistics |
| `--verbose` / `-v` | flag | False | Verbose output |
| `--block` | flag | False | Block until Ctrl+C |
| `--resources` | str | None | JSON dict of custom resources |
| `--labels` | str | None | JSON dict of node labels |
| `--autoscaling-config` | str | None | Path to autoscaling config YAML |
| `--no-monitor` | flag | False | Disable monitor process |
| `--plasma-directory` | str | None | Directory for plasma store |
| `--enable-object-reconstruction` | flag | False | Enable object reconstruction |
| `--metrics-export-port` | int | None | Port for metrics export |
| `--metric-export-interval` | int | 10 | Metrics export interval in seconds |
| `--runtime-env` | str | None | JSON runtime environment |
| `--ray-runtime-env` | str | None | JSON runtime env (alternative) |
| `--gcs-address` | str | None | GCS address for external worker |
| `--gcs-password` | str | None | GCS password |
| `--node-name` | str | None | Custom node name |
| `--ray-debugger-external` | flag | False | Make Ray debugger externally accessible |
| `--include-dashboard` | bool | True | Include dashboard (Python API) |
| `--dashboard-encoding` | str | utf-8 | Dashboard encoding |

### ray start --address (Worker Node)

Start a worker node that connects to an existing head node.

```bash
ray start --address=<head-ip>:6379 \
    --num-cpus=8 \
    --num-gpus=4 \
    --object-store-memory=2000000000 \
    --node-ip-address=192.168.1.101 \
    --block \
    --resources='{"TPU": 4}' \
    --labels='{"region": "us-west", "zone": "1a"}'
```

All parameters from `--head` are available except `--head`-specific ones (`--dashboard-host`, `--dashboard-port`, `--port`). The `--address` parameter is required.

### ray stop

Stop a running Ray node.

```bash
# Stop Ray on this node
ray stop

# Force stop (kill all Ray processes)
ray stop --force

# Stop with grace period
ray stop --graceful-shutdown-timeout-s=30
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--force` / `-f` | flag | False | Force kill all Ray processes |
| `--graceful-shutdown-timeout-s` | int | 0 | Timeout for graceful shutdown |
| `-v` / `--verbose` | flag | False | Verbose output |

### ray status

Display cluster status information.

```bash
# Basic status
ray status

# With address
ray status --address=<head-ip>:6379

# Verbose status (detailed node info)
ray status --verbose
```

Output includes:
- Cluster status (running/stopped)
- Node count and resource summary
- Active jobs
- Resource utilization
- Autoscaler status (if enabled)

## Cluster YAML Configuration

Ray clusters can be configured using YAML files for the Ray autoscaler or KubeRay.

### Full YAML Reference

```yaml
# =============================================================================
# Cluster Identity
# =============================================================================
cluster_name: my-ray-cluster          # Unique cluster name
max_workers: 10                       # Maximum total workers
upscaling_speed: 2                    # Max new workers per minute (v1)
idle_timeout_minutes: 5               # Auto-downscale after idle

# =============================================================================
# Cloud Provider
# =============================================================================
provider:
  type: aws                           # aws, gcp, azure, aliyun, vsphere, external, local
  region: us-west-2                   # Cloud region
  availability_zone: us-west-2a       # Availability zone
  # (provider-specific options below)

# =============================================================================
# Authentication
# =============================================================================
auth:
  ssh_user: ubuntu                    # SSH username for node access
  ssh_private_key: ~/.ssh/id_rsa      # SSH private key path
  ssh_proxy_command: ""               # SSH proxy command (for bastion)
  ssh_control_master: auto            # SSH multiplexing

# =============================================================================
# Node Types
# =============================================================================
available_node_types:
  ray.head.default:
    resources: {"CPU": 4}             # Resources advertised by this node type
    node_config:
      # Provider-specific instance config
      InstanceType: m5.xlarge
      ImageId: ami-0abcdef1234567890
      # ...
    min_workers: 0                    # Minimum workers of this type
    max_workers: 0                    # Max workers (0 for head)

  ray.worker.cpu:
    resources: {"CPU": 8}
    node_config:
      InstanceType: m5.2xlarge
      # ...
    min_workers: 2                    # Always-running workers
    max_workers: 10

  ray.worker.gpu:
    resources: {"CPU": 8, "GPU": 1}
    node_config:
      InstanceType: p3.2xlarge
      # ...
    min_workers: 0
    max_workers: 5

head_node_type: ray.head.default      # Which node type is the head

# =============================================================================
# File Mounts
# =============================================================================
file_mounts:
  /home/ubuntu/my_code: ./local_code_dir     # Sync local dir to remote
  /home/ubuntu/config.yaml: ./config.yaml    # Sync single file
  /home/ubuntu/data: s3://bucket/data/       # S3 data (via awscurl)

file_mounts_sync_continuously: false   # Continuously sync file mounts

# =============================================================================
# Initialization and Setup Commands
# =============================================================================
initialization_commands:
  - sudo apt-get update
  - sudo apt-get install -y python3-pip
  - pip3 install ray

setup_commands:
  - pip install -r /home/ubuntu/my_code/requirements.txt
  - echo "Setup complete"

head_setup_commands:
  - echo "Head-specific setup"
  - pip install ray[default]

worker_setup_commands:
  - echo "Worker-specific setup"

head_start_ray_commands:
  - ray stop
  - ray start --head --port=6379 --dashboard-host=0.0.0.0
      --object-store-memory=1000000000
      --num-cpus=$RAY_HEAD_CPU
      --block

worker_start_ray_commands:
  - ray stop
  - ray start --address=$RAY_HEAD_IP:6379
      --object-store-memory=1000000000
      --num-cpus=$RAY_WORKER_CPU
      --block

# =============================================================================
# Docker Configuration
# =============================================================================
docker:
  image: "rayproject/ray:latest-cpu"         # Docker image
  container_name: "ray_container"             # Container name
  pull_before_run: true                       # Pull image before start
  run_options:
    - "--rm"
    - "--network=host"
    - "--privileged"
  head_image: "rayproject/ray:latest-cpu"     # Override for head
  worker_image: "rayproject/ray:latest-gpu"   # Override for workers
  head_run_options: []
  worker_run_options:
    - "--gpus=all"

# =============================================================================
# Autoscaler Configuration (v1)
# =============================================================================
autoscaling_config:
  upscaling_speed: 2                    # Workers added per scaling event
  idle_timeout_minutes: 5               # Minutes before idle removal
  max_concurrent_launches: 10           # Max concurrent node launches
  max_failures_skip_pernet: 5           # Failures before marking subnet bad
  target_utilization_fraction: 0.8      # Target resource utilization
  warmup_node_interval_s: 30            # Wait between node launches
  upscaling_queue_length: -1            # Queue length (-1 = auto)
  worker_frozen_timeout_s: 120          # Frozen worker timeout
  keep_alive_minutes: 0                 # Keep workers alive after job ends

# =============================================================================
# Runtime Environment
# =============================================================================
runtime_env:
  pip:
    - numpy==1.24.0
    - pandas
    - torch
  env_vars:
    OMP_NUM_THREADS: "4"
    RAY_BACKEND_LOG_LEVEL: "info"

# =============================================================================
# Advanced Configuration
# =============================================================================
cluster_synced_files: []                # Files to sync on update
rsync_options:
  rsync_exclude: []                     # Exclude patterns
  rsync_filter: []                      # Filter patterns
```

### Minimal Configuration Example

```yaml
cluster_name: minimal-cluster
max_workers: 5

provider:
  type: aws
  region: us-west-2

auth:
  ssh_user: ubuntu
  ssh_private_key: ~/.ssh/id_rsa

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      InstanceType: m5.xlarge
  ray.worker.default:
    min_workers: 1
    max_workers: 5
    resources: {"CPU": 8}
    node_config:
      InstanceType: m5.2xlarge

head_node_type: ray.head.default
```

## Cloud Provider Configurations

### AWS Configuration

```yaml
provider:
  type: aws
  region: us-west-2
  availability_zone: us-west-2a
  # AWS-specific settings
  aws_access_key_id: null               # Optional, uses IAM role if null
  aws_secret_access_key: null
  aws_session_token: null
  cache_stopped_nodes: true             # Cache stopped nodes for fast reuse
  ssh_proxy_command: ""

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      InstanceType: m5.xlarge
      ImageId: ami-0abcdef1234567890        # AMI ID
      KeyName: my-key                        # SSH key pair
      SubnetIds: ["subnet-xxx"]              # VPC subnet
      SecurityGroupIds: ["sg-xxx"]           # Security groups
      IAMInstanceProfile:
        Arn: "arn:aws:iam::xxx:instance-profile/ray-profile"
      TagSpecifications:
        - ResourceType: instance
          Tags:
            - Key: Name
              Value: ray-head
      BlockDeviceMappings:
        - DeviceName: /dev/sda1
          Ebs:
            VolumeSize: 100
            VolumeType: gp3
      Placement:
        AvailabilityZone: us-west-2a

  ray.worker.spot:
    min_workers: 0
    max_workers: 20
    resources: {"CPU": 8, "GPU": 1}
    node_config:
      InstanceType: p3.2xlarge
      InstanceMarketOptions:
        MarketType: spot
        SpotOptions:
          MaxPrice: "3.00"                   # Max spot price
          SpotInstanceType: persistent
          InstanceInterruptionBehavior: stop
      ImageId: ami-0abcdef1234567890
      KeyName: my-key
      SubnetIds: ["subnet-xxx"]
      SecurityGroupIds: ["sg-xxx"]
```

### GCP Configuration

```yaml
provider:
  type: gcp
  region: us-west1
  availability_zone: us-west1-a
  project_id: my-gcp-project               # Required
  gcp_credentials: null                     # Optional, uses service account

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      machineType: n1-standard-4             # GCP machine type
      disks:
        - boot: true
          autoDelete: true
          type: PERSISTENT
          initializeParams:
            sourceImage: "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
            diskSizeGb: 100
      networkInterfaces:
        - network: global/networks/default
          accessConfigs:
            - type: ONE_TO_ONE_NAT
      serviceAccounts:
        - email: ray-sa@my-project.iam.gserviceaccount.com
          scopes:
            - "https://www.googleapis.com/auth/cloud-platform"
      labels:
        ray-cluster: my-cluster
        ray-node-type: head

  ray.worker.preemptible:
    min_workers: 0
    max_workers: 20
    resources: {"CPU": 8, "GPU": 1}
    node_config:
      machineType: n1-standard-8
      scheduling:
        preemptible: true                    # Preemptible (spot) VMs
        automaticRestart: false
        onHostMaintenance: TERMINATE
      guestAccelerators:
        - acceleratorType: nvidia-tesla-v100
          acceleratorCount: 1
      disks:
        - boot: true
          autoDelete: true
          initializeParams:
            sourceImage: "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
      networkInterfaces:
        - network: global/networks/default
```

### Azure Configuration

```yaml
provider:
  type: azure
  location: westus2
  resource_group: my-resource-group
  # Azure-specific settings
  subscription_id: null                      # Optional, uses AZURE_SUBSCRIPTION_ID
  client_id: null
  client_secret: null
  tenant_id: null

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      vmSize: Standard_D4s_v3               # Azure VM size
      imageReference:
        publisher: canonical
        offer: ubuntu-24_04-lts
        sku: server
        version: latest
      osDisk:
        osType: Linux
        caching: ReadWrite
        managedDisk:
          storageAccountType: Premium_LRS
        diskSizeGB: 100
      networkInterfaces:
        - primary: true
      tags:
        ray-cluster: my-cluster

  ray.worker.lowpriority:
    min_workers: 0
    max_workers: 20
    resources: {"CPU": 8, "GPU": 1}
    node_config:
      vmSize: Standard_NC6s_v3
      priority: Low                          # Low priority (spot) VMs
      evictionPolicy: StopDeallocate
      billingProfile:
        maxPrice: -1                         # -1 = any price up to on-demand
      imageReference:
        publisher: canonical
        offer: ubuntu-24_04-lts
        sku: server
        version: latest
```

### Alibaba Cloud (Aliyun) Configuration

```yaml
provider:
  type: aliyun
  region: cn-beijing
  # Aliyun-specific settings

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      InstanceType: ecs.c6.xlarge
      ImageId: m-xxx
```

### vSphere Configuration

```yaml
provider:
  type: vsphere
  # vSphere-specific settings for on-premise deployments

available_node_types:
  ray.head.default:
    resources: {"CPU": 4}
    node_config:
      # vSphere VM configuration
```

## KubeRay

KubeRay is a Kubernetes operator for managing Ray clusters on Kubernetes.

### RayCluster CRD

```yaml
apiVersion: ray.io/v1
kind: RayCluster
metadata:
  name: my-ray-cluster
  labels:
    ray.io/cluster: my-ray-cluster
spec:
  # Ray version
  rayVersion: "2.47.0"

  # Autoscaling (optional)
  enableInTreeAutoscaling: true
  autoscalerOptions:
    upscalingMode: Default                  # Default, Aggressive, Conservative
    idleTimeoutSeconds: 60                  # Idle timeout for workers
    imagePullPolicy: IfNotPresent
    resources:
      limits:
        cpu: "1"
        memory: "1Gi"
      requests:
        cpu: "500m"
        memory: "512Mi"
    env:
      - name: RAY_LOG_TO_STDERR
        value: "1"
    envFrom:
      - configMapRef:
          name: ray-config

  # Head group
  headGroupSpec:
    serviceType: ClusterIP                  # ClusterIP, NodePort, LoadBalancer
    rayStartParams:
      port: "6379"
      dashboard-host: "0.0.0.0"
      dashboard-port: "8265"
      num-cpus: "4"
      object-store-memory: "1000000000"
      block: "true"
    template:
      spec:
        containers:
          - name: ray-head
            image: rayproject/ray:2.47.0
            ports:
              - containerPort: 6379
                name: gcs
              - containerPort: 8265
                name: dashboard
              - containerPort: 10001
                name: client
            resources:
              limits:
                cpu: "4"
                memory: "8Gi"
              requests:
                cpu: "4"
                memory: "8Gi"
            env:
              - name: RAY_LOG_TO_STDERR
                value: "1"
            volumeMounts:
              - mountPath: /tmp/ray
                name: ray-logs
        volumes:
          - name: ray-logs
            emptyDir: {}

  # Worker groups
  workerGroupSpecs:
    - groupName: cpu-group
      replicas: 3
      minReplicas: 1
      maxReplicas: 10
      numOfHosts: 1                         # For multi-host (e.g., GPU training)
      rayStartParams:
        num-cpus: "8"
        object-store-memory: "2000000000"
        block: "true"
      template:
        spec:
          containers:
            - name: ray-worker
              image: rayproject/ray:2.47.0
              resources:
                limits:
                  cpu: "8"
                  memory: "16Gi"
                requests:
                  cpu: "8"
                  memory: "16Gi"

    - groupName: gpu-group
      replicas: 2
      minReplicas: 0
      maxReplicas: 5
      rayStartParams:
        num-cpus: "8"
        num-gpus: "1"
        block: "true"
      template:
        spec:
          containers:
            - name: ray-worker
              image: rayproject/ray:2.47.0-gpu
              resources:
                limits:
                  cpu: "8"
                  memory: "32Gi"
                  nvidia.com/gpu: "1"
                requests:
                  cpu: "8"
                  memory: "32Gi"
                  nvidia.com/gpu: "1"
```

### RayCluster Spec Fields

| Field | Type | Description |
|-------|------|-------------|
| `rayVersion` | str | Ray version for image selection |
| `enableInTreeAutoscaling` | bool | Enable built-in autoscaler |
| `autoscalerOptions` | object | Autoscaler pod configuration |
| `headGroupSpec` | object | Head node pod specification |
| `workerGroupSpecs` | list | Worker group specifications |
| `headServiceAnnotations` | object | Annotations for head service |
| `suspend` | bool | Suspend the cluster |

### headGroupSpec Fields

| Field | Type | Description |
|-------|------|-------------|
| `serviceType` | str | Kubernetes service type |
| `rayStartParams` | map | Parameters for `ray start` |
| `template` | PodTemplateSpec | Kubernetes pod template |
| `enableIngress` | bool | Create Ingress for dashboard |

### workerGroupSpec Fields

| Field | Type | Description |
|-------|------|-------------|
| `groupName` | str | Unique name for this worker group |
| `replicas` | int | Current number of replicas |
| `minReplicas` | int | Minimum replicas (autoscaling floor) |
| `maxReplicas` | int | Maximum replicas (autoscaling ceiling) |
| `numOfHosts` | int | Number of hosts per replica (multi-host) |
| `rayStartParams` | map | Parameters for `ray start` |
| `template` | PodTemplateSpec | Kubernetes pod template |
| `scaleStrategy` | object | Scaling strategy |

### RayJob CRD

```yaml
apiVersion: ray.io/v1
kind: RayJob
metadata:
  name: my-training-job
spec:
  # Ray cluster specification (creates an ephemeral cluster)
  rayClusterSpec:
    rayVersion: "2.47.0"
    headGroupSpec:
      rayStartParams:
        port: "6379"
        dashboard-host: "0.0.0.0"
      template:
        spec:
          containers:
            - name: ray-head
              image: rayproject/ray:2.47.0
              resources:
                limits:
                  cpu: "4"
                  memory: "8Gi"
    workerGroupSpecs:
      - groupName: worker
        replicas: 4
        minReplicas: 4
        maxReplicas: 4
        rayStartParams: {}
        template:
          spec:
            containers:
              - name: ray-worker
                image: rayproject/ray:2.47.0
                resources:
                  limits:
                    cpu: "4"
                    memory: "16Gi"

  # Job specification
  entrypoint: "python /home/ray/train.py --epochs 10"
  runtimeEnv: |
    pip:
      - torch==2.0.0
      - transformers
    env_vars:
      TRAINING_MODE: distributed
  shutdownAfterJobFinishes: true
  ttlSecondsAfterFinished: 3600             # Cleanup after 1 hour
  backoffLimit: 3                           # Retry limit
  activeDeadlineSeconds: 3600               # Max runtime
  submissionMode: "InteractiveMode"          # InteractiveMode or K8sJobMode
```

### RayJob Spec Fields

| Field | Type | Description |
|-------|------|-------------|
| `entrypoint` | str | Command to execute |
| `rayClusterSpec` | object | Cluster spec (creates ephemeral cluster) |
| `clusterSelector` | object | Select existing cluster (alternative to rayClusterSpec) |
| `runtimeEnv` | str | YAML runtime environment |
| `shutdownAfterJobFinishes` | bool | Delete cluster after job completes |
| `ttlSecondsAfterFinished` | int | Cleanup delay after completion |
| `backoffLimit` | int | Number of retries on failure |
| `activeDeadlineSeconds` | int | Maximum runtime in seconds |
| `submissionMode` | str | "InteractiveMode" or "K8sJobMode" |
| `jobId` | str | Custom job ID |

### RayService CRD

```yaml
apiVersion: ray.io/v1
kind: RayService
metadata:
  name: my-serve-service
spec:
  # Serve deployment configuration
  serveConfigV2: |
    applications:
      - name: my_app
        route_prefix: "/"
        import_path: my_module:app
        runtime_env:
          working_dir: "s3://bucket/code/"
          pip:
            - torch
            - transformers
        deployments:
          - name: Model
            num_replicas: 4
            ray_actor_options:
              num_gpus: 1
            autoscaling_config:
              min_replicas: 1
              max_replicas: 10
              target_num_ongoing_requests_per_replica: 5

  # Ray cluster for serving
  rayClusterConfig:
    rayVersion: "2.47.0"
    headGroupSpec:
      rayStartParams:
        port: "6379"
        dashboard-host: "0.0.0.0"
      template:
        spec:
          containers:
            - name: ray-head
              image: rayproject/ray:2.47.0
              resources:
                limits:
                  cpu: "4"
                  memory: "8Gi"
    workerGroupSpecs:
      - groupName: gpu-worker
        replicas: 4
        minReplicas: 2
        maxReplicas: 10
        rayStartParams:
          num-gpus: "1"
        template:
          spec:
            containers:
              - name: ray-worker
                image: rayproject/ray:2.47.0-gpu
                resources:
                  limits:
                    nvidia.com/gpu: "1"
                    memory: "32Gi"

  # Service health configuration
  serviceUnhealthySecondThreshold: 300
  deploymentUnhealthySecondThreshold: 300
```

### RayService Spec Fields

| Field | Type | Description |
|-------|------|-------------|
| `serveConfigV2` | str | Serve deployment config (multi-app V2 format) |
| `rayClusterConfig` | object | Underlying RayCluster spec |
| `serviceUnhealthySecondThreshold` | int | Restart after N seconds unhealthy |
| `deploymentUnhealthySecondThreshold` | int | Deployment-level health threshold |
| `serveService` | object | Kubernetes service for serve endpoints |

### KubeRay kubectl Commands

```bash
# Apply cluster configuration
kubectl apply -f ray-cluster.yaml

# Check cluster status
kubectl get raycluster
kubectl describe raycluster my-ray-cluster

# Check pods
kubectl get pods -l ray.io/cluster=my-ray-cluster

# Submit a RayJob
kubectl apply -f ray-job.yaml
kubectl get rayjob
kubectl logs -f rayjob-my-training-job-xxxxx

# Deploy a RayService
kubectl apply -f ray-service.yaml
kubectl get rayservice
kubectl get svc

# Port-forward dashboard
kubectl port-forward svc/my-ray-cluster-head-svc 8265:8265

# Scale worker group
kubectl patch raycluster my-ray-cluster --type=json \
  -p='[{"op":"replace","path":"/spec/workerGroupSpecs/0/replicas","value":5}]'

# Delete resources
kubectl delete raycluster my-ray-cluster
kubectl delete rayjob my-training-job
kubectl delete rayservice my-serve-service
```

## Autoscaler

### Autoscaler V1

The V1 autoscaler is the default, stable autoscaler.

```yaml
autoscaling_config:
  # Upscaling
  upscaling_speed: 2                    # Max workers added per scaling event
  max_concurrent_launches: 10           # Max concurrent node launches
  target_utilization_fraction: 0.8      # Target resource utilization before scale up
  warmup_node_interval_s: 30            # Wait between launches

  # Downscaling
  idle_timeout_minutes: 5               # Minutes before idle node removal
  keep_alive_minutes: 0                 # Keep alive after job ends (0 = don't)

  # Failure handling
  max_failures_skip_pernet: 5           # Failures before marking subnet bad
  worker_frozen_timeout_s: 120          # Frozen worker timeout
```

### Autoscaler V2

The V2 autoscaler provides improved scheduling and scaling decisions.

```bash
# Enable V2 autoscaler via system config
ray start --head --port=6379 \
    --system-config='{"autoscaler_v2": true}'
```

**V2 Features:**
- Improved bin-packing for resource requests
- Better multi-node-type scheduling
- Faster convergence to target cluster state
- Enhanced debuggability with autoscaler state API
- Instance storage persistence

```python
# Query autoscaler state (V2)
from ray.autoscaler.v2.sdk import get_autoscaler_state
state = get_autoscaler_state()
print(state.cluster_resource_constraints)
print(state.pending_gang_resource_requests)
```

### Autoscaler Behavior

1. **Resource Demand Detection**: Autoscaler monitors pending resource requests
2. **Scale-Up Decision**: If pending tasks exceed target utilization, launch new nodes
3. **Node Type Selection**: Choose node type that best fits resource requirements
4. **Scale-Down Decision**: Remove idle workers after `idle_timeout_minutes`
5. **Failure Handling**: Mark failed subnets/AZs and avoid them

```python
# Programmatically request cluster resources
from ray.autoscaler.sdk import request_cluster_resources

# Request specific resources
request_cluster_resources({
    "CPU": 16,
    "GPU": 4,
    "memory": 64 * 1024 * 1024 * 1024,
})
```

### Autoscaler Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RAY_AUTOSCALER_V2_ENABLED` | Enable V2 autoscaler | "0" |
| `RAY_UPSCALING_SPEED` | Upscaling speed | 2 |
| `RAY_MAX_CONCURRENT_LAUNCHES` | Max concurrent launches | 10 |
| `RAY_IDLE_TIMEOUT_MINUTES` | Idle timeout | 5 |
| `RAY_TARGET_UTILIZATION` | Target utilization fraction | 0.8 |

## Node Types Configuration

### Resource Specification

```yaml
available_node_types:
  # CPU-optimized workers
  cpu-worker:
    resources: {"CPU": 16}
    min_workers: 2
    max_workers: 20
    node_config:
      InstanceType: m5.4xlarge

  # Memory-optimized workers
  memory-worker:
    resources: {"CPU": 8, "memory": 64}
    min_workers: 0
    max_workers: 5
    node_config:
      InstanceType: r5.2xlarge

  # GPU workers
  gpu-worker:
    resources: {"CPU": 8, "GPU": 1}
    min_workers: 0
    max_workers: 10
    node_config:
      InstanceType: p3.2xlarge

  # Multi-GPU workers
  multi-gpu-worker:
    resources: {"CPU": 32, "GPU": 4}
    min_workers: 0
    max_workers: 4
    node_config:
      InstanceType: p3.8xlarge

  # Custom resource workers
  tpu-worker:
    resources: {"CPU": 8, "TPU": 4}
    min_workers: 0
    max_workers: 4
    node_config:
      machineType: n1-standard-8
```

### Node Type Selection Logic

The autoscaler selects node types based on:
1. Resource requirements of pending tasks/actors
2. Cost optimization (prefer cheaper node types)
3. Available capacity of each node type
4. Min/max worker constraints

## Docker Support

### Docker Configuration in YAML

```yaml
docker:
  # Basic settings
  image: "rayproject/ray:latest-cpu"
  container_name: "ray_container"
  pull_before_run: true

  # Run options (passed to `docker run`)
  run_options:
    - "--network=host"
    - "--privileged"
    - "-v /tmp:/tmp"
    - "--shm-size=4g"
    - "--ulimit=nofile=65536:65536"

  # Per-node-type overrides
  head_image: "rayproject/ray:latest-cpu"
  head_run_options:
    - "-p 8265:8265"

  worker_image: "rayproject/ray:latest-gpu"
  worker_run_options:
    - "--gpus=all"
```

### Official Docker Images

```bash
# Official Ray Docker images
rayproject/ray:latest                    # Latest CPU image
rayproject/ray:latest-cpu                # CPU only
rayproject/ray:latest-gpu                # GPU with CUDA
rayproject/ray:2.47.0                    # Specific version
rayproject/ray:2.47.0-cpu
rayproject/ray:2.47.0-gpu
rayproject/ray:2.47.0-cu121             # Specific CUDA version
```

### Custom Dockerfile

```dockerfile
FROM rayproject/ray:2.47.0-cpu
RUN pip install my-package torch
COPY . /app
WORKDIR /app
```

### Running Ray in Docker

```bash
# Start head in Docker
docker run --rm --network=host rayproject/ray:latest \
    ray start --head --port=6379 --dashboard-host=0.0.0.0

# Start worker in Docker
docker run --rm --network=host rayproject/ray:latest \
    ray start --address=<head-ip>:6379

# With GPU support
docker run --rm --network=host --gpus=all rayproject/ray:latest-gpu \
    ray start --address=<head-ip>:6379 --num-gpus=1
```

## Multi-Cluster Management

### Connecting to Remote Clusters

```python
import ray

# Connect via Ray Client
ray.init("ray://cluster-head:10001")

# Connect to existing cluster
ray.init(address="ray://cluster-head:10001")

# Connect with runtime env
ray.init(
    address="ray://cluster-head:10001",
    runtime_env={"pip": ["torch"]},
)

# Connect to auto-discovered cluster
ray.init(address="auto")
```

### Cluster Management CLI

```bash
# Start cluster from YAML
ray up cluster.yaml

# Update cluster configuration
ray up cluster.yaml --restart-only

# Tear down cluster
ray down cluster.yaml

# Monitor cluster
ray monitor cluster.yaml

# Execute command on cluster
ray exec cluster.yaml "python my_script.py"

# rsync files to cluster
ray rsync_up cluster.yaml ./local_file /home/ubuntu/remote_file

# rsync files from cluster
ray rsync_down cluster.yaml /home/ubuntu/remote_file ./local_file

# Attach to head node (SSH)
ray attach cluster.yaml

# Get dashboard URL
ray dashboard cluster.yaml
```

### Multi-Cluster Patterns

```python
# Pattern 1: Job submission to remote cluster
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://cluster-head:8265")
job_id = client.submit_job(
    entrypoint="python train.py",
    runtime_env={"pip": ["torch"]},
)

# Pattern 2: Multi-cluster workload
clusters = [
    "http://cluster-a:8265",
    "http://cluster-b:8265",
    "http://cluster-c:8265",
]

for cluster_url in clusters:
    client = JobSubmissionClient(cluster_url)
    client.submit_job(
        entrypoint=f"python train.py --cluster={cluster_url}",
    )
```

## Cluster Security

### Network Security

```bash
# Restrict dashboard access to localhost
ray start --head --port=6379 \
    --dashboard-host=127.0.0.1
```

### Authentication

```bash
# Start with GCS password
ray start --head --port=6379 --gcs-password=my-secret-password

# Worker connects with password
ray start --address=<head>:6379 --gcs-password=my-secret-password
```

### TLS Configuration

```python
import ray

ray.init(
    address="ray://cluster:10001",
    _system_config={
        "tls_server_cert": "/path/to/server.crt",
        "tls_server_key": "/path/to/server.key",
        "tls_ca_cert": "/path/to/ca.crt",
    }
)
```

## Environment Variables for Cluster Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `RAY_address` | Cluster address to connect to | None |
| `RAY_namespace` | Default namespace | Job ID |
| `RAY_RUNTIME_ENV` | Runtime env as JSON string | None |
| `RAY_RECORD_REF_CREATION_SITES` | Track ObjectRef creation sites | False |
| `RAY_BACKEND_LOG_LEVEL` | C++ backend log level | info |
| `RAY_LOG_TO_STDERR` | Log to stderr | False |
| `RAY_graceful_shutdown_timeout_s` | Graceful shutdown timeout | 60 |
| `RAY_max_lineage_bytes` | Max lineage for reconstruction | 1GB |
| `RAY_object_spilling_config` | Object spilling configuration | None |
| `RAY_plasma_directory` | Plasma store directory | /tmp |
| `RAY_disable_memory_monitor` | Disable memory monitor | False |
| `RAY_memory_monitor_refresh_ms` | Memory monitor refresh interval | 1000 |
| `RAY_AUTOSCALER_V2_ENABLED` | Enable V2 autoscaler | "0" |
| `RAY_DEBUG` | Enable debug mode | False |
| `RAY_JOB_ID` | Job ID override | Auto-generated |
| `RAY_NODE_IP` | Node IP address | Auto-detected |
| `RAY_GCS_SERVICE_NAME` | GCS service name | None |
