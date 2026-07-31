# Security

## Overview

Ray provides security features for authentication, encryption, network isolation, and access control in production deployments.

## Security Architecture

```
Client → Dashboard (HTTPS) → Head Node (GCS)
                              ├── Raylet (gRPC + TLS)
                              ├── Workers (gRPC + TLS)
                              └── Redis (Auth)
```

## Authentication

### Redis Authentication
```bash
# Start head with Redis password
ray start --head \
    --redis-password=my_secure_password \
    --dashboard-host=0.0.0.0
```

```python
# Connect with password
ray.init(
    address="ray://head:10001",
    _redis_password="my_secure_password",
)
```

### Dashboard Authentication
```bash
# Basic auth for dashboard
ray start --head \
    --dashboard-login-username=admin \
    --dashboard-login-password=secret
```

## TLS Encryption

### Enabling TLS
```python
import ray

ray.init(
    address="ray://head:10001",
    _tls_config={
        "cert_chain": "/path/to/cert.pem",
        "private_key": "/path/to/key.pem",
        "ca_cert": "/path/to/ca.pem",
    },
)
```

### CLI with TLS
```bash
ray start --head \
    --tls-cert-file=/path/to/cert.pem \
    --tls-key-file=/path/to/key.pem \
    --tls-ca-file=/path/to/ca.pem
```

### Worker Node with TLS
```bash
ray start --address=head:6379 \
    --tls-cert-file=/path/to/cert.pem \
    --tls-key-file=/path/to/key.pem \
    --tls-ca-file=/path/to/ca.pem
```

### Generating Certificates
```bash
# CA certificate
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt

# Server certificate
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
    -subj "/CN=ray-head"
openssl x509 -req -days 365 -in server.csr \
    -CA ca.crt -CAkey ca.key -set_serial 01 \
    -out server.crt
```

## Network Security

### Dashboard Host Binding
```bash
# Bind to specific interface only
ray start --head --dashboard-host=127.0.0.1

# Bind to all interfaces (default, use with caution)
ray start --head --dashboard-host=0.0.0.0
```

### Port Configuration
```bash
ray start --head \
    --port=6379 \                    # GCS server port
    --dashboard-port=8265 \          # Dashboard port
    --dashboard-agent-listen-port=52365 \  # Agent port
    --node-manager-port=8096 \       # Node manager port
    --object-manager-port=8097 \     # Object manager port
    --min-worker-port=10001 \        # Worker port range
    --max-worker-port=10050          # Worker port range
```

### Firewall Rules
```bash
# Required ports
# 6379    - GCS (Redis)
# 8265    - Dashboard
# 52365   - Dashboard agent
# 8096    - Node manager
# 8097    - Object manager
# 10001-10050 - Workers

# Example iptables
iptables -A INPUT -p tcp --dport 6379 -j ACCEPT
iptables -A INPUT -p tcp --dport 8265 -j ACCEPT
iptables -A INPUT -p tcp --dport 52365 -j ACCEPT
```

## Access Control

### Namespaces
```python
# Job-level namespace
ray.init(namespace="team-a")

# Actors in namespace
@ray.remote
class SecureActor:
    pass

actor = SecureActor.options(
    name="my-actor",
    namespace="team-a",
).remote()

# Get actor from specific namespace
handle = ray.get_actor("my-actor", namespace="team-a")
```

### Runtime Environment Isolation
```python
# Isolated runtime environments
ray.init(runtime_env={
    "working_dir": "./team_a_code/",
    "pip": ["team-a-deps"],
    "env_vars": {"TEAM": "A"},
})
```

### Detached Actors Access Control
```python
# Only accessible within same namespace
@ray.remote(lifetime="detached")
class ProtectedService:
    pass

service = ProtectedService.options(
    name="protected-service",
    namespace="secure-namespace",
).remote()
```

## Ray Serve Security

### HTTPS with Serve
```python
from ray import serve

# Configure HTTPS
serve.start(httproxy_options={
    "host": "0.0.0.0",
    "port": 443,
    "location": "EveryNode",
    "tls_certfile": "/path/to/cert.pem",
    "tls_keyfile": "/path/to/key.pem",
})
```

### API Key Authentication
```python
from ray import serve
from fastapi import Request, HTTPException

@serve.deployment
class AuthenticatedModel:
    async def __call__(self, request: Request):
        api_key = request.headers.get("X-API-Key")
        if api_key != os.environ.get("API_KEY"):
            raise HTTPException(status_code=401, detail="Invalid API key")
        data = await request.json()
        return self.predict(data)
```

### Rate Limiting
```python
import time
from ray import serve

@serve.deployment
class RateLimitedService:
    def __init__(self):
        self.request_times = []
        self.max_rpm = 100  # requests per minute

    async def __call__(self, request):
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.max_rpm:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        self.request_times.append(now)
        return self.process(request)
```

## Secrets Management

### Environment Variables
```python
# Pass secrets via runtime env
ray.init(runtime_env={
    "env_vars": {
        "DATABASE_URL": "postgres://...",
        "API_KEY": "...",
    },
})
```

### Secret Scoping
```python
@ray.remote
def task_with_secret():
    import os
    # Secret available only in this task's runtime env
    db_url = os.environ["DATABASE_URL"]
    return query_database(db_url)
```

### Mounting Secret Files
```python
# Use working_dir with secret files
ray.init(runtime_env={
    "working_dir": "./app",
    "env_vars": {
        "SECRET_PATH": "/app/secrets/config.json",
    },
})
```

## Kubernetes Security

### KubeRay Security
```yaml
apiVersion: ray.io/v1
kind: RayCluster
metadata:
  name: secure-cluster
spec:
  rayVersion: '2.47.0'
  headGroupSpec:
    template:
      spec:
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          fsGroup: 1000
        containers:
          - name: ray-head
            image: rayproject/ray:2.47.0
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: false
              capabilities:
                drop:
                  - ALL
            resources:
              limits:
                memory: 8Gi
```

### Network Policies
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ray-network-policy
spec:
  podSelector:
    matchLabels:
      app: ray
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: ray
      ports:
        - port: 6379
        - port: 8265
        - port: 8096
        - port: 8097
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: ray
```

### Service Account & RBAC
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ray-service-account
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ray-role
rules:
  - apiGroups: [""]
    resources: ["pods", "services"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ray-role-binding
subjects:
  - kind: ServiceAccount
    name: ray-service-account
roleRef:
  kind: Role
  name: ray-role
  apiGroup: rbac.authorization.k8s.io
```

## Security Best Practices

1. **Enable TLS** for all inter-node communication in production
2. **Use Redis authentication** (`--redis-password`) for GCS
3. **Restrict dashboard access** - bind to 127.0.0.1 or use auth
4. **Use namespaces** to isolate workloads
5. **Limit firewall rules** to only required ports
6. **Run as non-root** in containers (`securityContext`)
7. **Use network policies** in Kubernetes
8. **Don't log secrets** - use environment variables or secret mounts
9. **Rotate certificates** regularly
10. **Audit access** via dashboard and logging
11. **Use Runtime Environments** for dependency isolation
12. **Set resource limits** to prevent denial of service
13. **Use RBAC** in Kubernetes deployments
14. **Monitor for suspicious activity** via Ray dashboard and metrics
