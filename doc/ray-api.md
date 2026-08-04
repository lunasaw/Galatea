# Ray 常用 API 手册

本文整理 Ray Dashboard 上最常用的 HTTP API，适用于本仓库固定使用的 **Ray 2.53.0**。
示例以作业管理、集群状态查询和日志排错为主，可以直接替换地址与 ID 后使用。

> Ray Dashboard 默认监听 `8265` 端口。下文的 `<submission-id>`、`<actor-id>`、
> `<task-id>`、`<node-id>`、`<node-ip>`、`<pid>` 都是占位内容。

## 1. 接口范围与稳定性

Ray Dashboard 同时暴露多组 HTTP 接口，稳定性并不相同：

| 接口 | Ray 2.53.0 状态 | 使用建议 |
| --- | --- | --- |
| Ray Jobs REST API | 官方 OpenAPI，API 版本 `4.0.0` | 提交、查询、停止和删除 Job 的首选 HTTP 接口 |
| State REST API | Alpha | 用于 Actor、Task、Node 等只读观测；升级 Ray 后需要回归验证 |
| `/api/v0/logs/*` | State/Dashboard 版本相关接口 | 适合按 Actor、Task、进程或文件排错；保留 CLI/SDK 兜底 |
| Dashboard UI 私有接口 | 未承诺兼容 | 不应写入生产脚本 |

升级 Ray 后，先调用 `/api/version` 确认 Ray 和 Jobs API 版本，再验证 State 与日志接口。

## 2. 连接与认证

### 2.1 设置 Dashboard 地址

```bash
export RAY_DASHBOARD_URL='http://xxxray.com'
```

先检查地址是否可达以及服务端版本：

```bash
curl -fsS "${RAY_DASHBOARD_URL}/api/version" | jq .
```

典型响应包含：

```json
{
  "version": "4",
  "ray_version": "2.53.0",
  "ray_commit": "<commit>",
  "session_name": "<session-name>"
}
```

### 2.2 Token Authentication

Ray 从 2.52.0 起支持 Token Authentication；Ray 2.53.0 默认仍未启用。服务端启用
`RAY_AUTH_MODE=token` 后，原始 HTTP 请求需要携带：

```bash
curl -fsS \
  -H 'Authorization: Bearer <token>' \
  "${RAY_DASHBOARD_URL}/api/version" \
  | jq .
```

使用认证时要注意：

- Token 不能写入仓库、脚本、Notebook、日志或命令示例。
- HTTP 会明文传输认证头。远程访问必须使用 HTTPS、SSH 端口转发、VPN 或其他加密链路。
- Token Authentication 是纵深防御，不能代替内网隔离和外部认证/授权代理。
- Ray CLI 和 SDK 可以从 `RAY_AUTH_TOKEN`、`RAY_AUTH_TOKEN_PATH` 或 `~/.ray/auth_token`
  读取 Token，通常比把 Token 展开到 `curl` 命令更安全。

下文为简洁起见不重复认证头。启用了 Token Authentication 的集群需要给每个请求补上该
请求头。

## 3. Ray Jobs REST API

Ray Jobs REST API 由 Head 节点上的 Dashboard 提供。集合端点应保留尾部 `/`。

### 3.1 端点速查

| 方法 | 端点 | 用途 |
| --- | --- | --- |
| `GET` | `/api/version` | 查询 Jobs API、Ray 版本和 Commit |
| `GET` | `/api/jobs/` | 列出 Submission Job 和 Driver Job |
| `POST` | `/api/jobs/` | 提交 Job |
| `GET` | `/api/jobs/<submission-id>` | 查询 Job 详情与状态 |
| `GET` | `/api/jobs/<submission-id>/logs` | 获取 Submission Job 日志 |
| `POST` | `/api/jobs/<submission-id>/stop` | 停止 Submission Job |
| `DELETE` | `/api/jobs/<submission-id>` | 删除已经进入终态的 Submission Job 记录 |
| WebSocket | `/api/jobs/<submission-id>/logs/tail` | 实时追踪 Job 日志 |

请求与脚本应使用 `submission_id`。请求字段 `job_id` 已废弃；响应中可能仍包含它以兼容旧
客户端。

### 3.2 列出 Job

```bash
curl -fsS "${RAY_DASHBOARD_URL}/api/jobs/" \
  | jq '.[] | {
      submission_id,
      job_id,
      type,
      status,
      entrypoint,
      start_time,
      end_time
    }'
```

`type` 可能是 `SUBMISSION` 或 `DRIVER`。停止、删除和读取 Job Submission 日志只适用于
`SUBMISSION` 类型。

### 3.3 提交 Job

```bash
curl -fsS -X POST "${RAY_DASHBOARD_URL}/api/jobs/" \
  -H 'Content-Type: application/json' \
  --data '{
    "entrypoint": "python scripts/train.py --config configs/baseline.yaml",
    "submission_id": "project-train-001",
    "runtime_env": {},
    "metadata": {
      "project": "example",
      "purpose": "training"
    },
    "entrypoint_num_cpus": 1,
    "entrypoint_num_gpus": 0
  }' \
  | jq .
```

典型响应：

```json
{
  "job_id": "<deprecated-job-id>",
  "submission_id": "project-train-001"
}
```

关键约束：

- `entrypoint` 是必填的 Shell 命令。
- `submission_id` 应由调用方生成并保持唯一。重复 ID 会被拒绝；网络重试前先按该 ID 查询
  是否已经创建 Job，避免重复训练。
- `metadata` 的键和值都是字符串，可记录项目、用途、代码版本或外部实验标识，但不能放
  Token。
- `entrypoint_num_cpus`、`entrypoint_num_gpus`、`entrypoint_memory` 和
  `entrypoint_resources` 只预留 Driver/Entrypoint 的资源，不代表训练 Worker 的资源。
- 原始 REST 请求不会自动上传客户端本地目录。需要上传本地代码时，使用
  `ray job submit --working-dir` 或 `JobSubmissionClient`；REST 请求中的 `working_dir` 应使用
  集群可访问的远程 URI。

Job 状态包括：

| 状态 | 含义 |
| --- | --- |
| `PENDING` | 已提交，正在等待 Driver 或准备 Runtime Environment |
| `RUNNING` | Entrypoint 正在执行 |
| `SUCCEEDED` | Entrypoint 以成功状态退出 |
| `FAILED` | Entrypoint、Runtime Environment 或系统执行失败 |
| `STOPPED` | Job 被停止 |

### 3.4 查询 Job 详情

```bash
curl -fsS \
  "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>" \
  | jq '{
      submission_id,
      job_id,
      type,
      status,
      message,
      error_type,
      start_time,
      end_time,
      driver_info,
      runtime_env,
      metadata
    }'
```

`start_time` 和 `end_time` 是 Unix 毫秒时间戳。失败时优先查看 `message`、`error_type` 和
Job 日志，不要只根据 HTTP 状态判断训练是否成功。

### 3.5 获取 Job 日志

```bash
curl -fsS \
  "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>/logs" \
  | jq -r '.logs'
```

该接口返回 JSON：

```json
{
  "logs": "<stdout-and-stderr>"
}
```

`/api/jobs/<submission-id>/logs/tail` 是 WebSocket，不是普通的 HTTP 文本流。实时追踪 Job
日志时，优先使用：

```bash
ray job logs \
  --address="${RAY_DASHBOARD_URL}" \
  -f \
  '<submission-id>'
```

Python 程序可以使用 `JobSubmissionClient.tail_job_logs()`。

### 3.6 停止 Job

停止会中断正在运行的 Entrypoint。先确认 `submission_id` 和当前状态：

```bash
curl -fsS \
  "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>" \
  | jq '{submission_id, status, entrypoint}'
```

确认后再停止：

```bash
curl -fsS -X POST \
  "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>/stop" \
  | jq .
```

响应格式：

```json
{
  "stopped": true
}
```

### 3.7 删除 Job 记录

只删除已经处于 `SUCCEEDED`、`FAILED` 或 `STOPPED` 的 Submission Job 记录：

```bash
curl -fsS -X DELETE \
  "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>" \
  | jq .
```

响应格式：

```json
{
  "deleted": true
}
```

删除 Ray Job 记录不等于删除 MLflow Run、Checkpoint 或 MinIO Artifact。各系统的生命周期
应分别治理。

## 4. State REST API

State REST API 用于读取集群当前快照。在 Ray 2.53.0 中它仍是 Alpha API，不保证跨版本兼容。
生产自动化优先考虑稳定的 State CLI；直接调用 HTTP 时要检查返回的截断和部分失败信息。

### 4.1 资源端点

| 端点 | 资源 |
| --- | --- |
| `/api/v0/actors` | Actor |
| `/api/v0/jobs` | Job 状态快照 |
| `/api/v0/nodes` | Node |
| `/api/v0/placement_groups` | Placement Group |
| `/api/v0/workers` | Worker |
| `/api/v0/tasks` | Task |
| `/api/v0/objects` | Object Store 对象引用 |
| `/api/v0/runtime_envs` | Runtime Environment |

### 4.2 公共查询参数

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `limit` | `100` | 最大返回条数；Ray 2.53.0 服务端默认上限为 `10000` |
| `timeout` | `30` | HTTP 查询超时，单位秒 |
| `detail` | `false` | 是否返回更多详细字段；详细查询可能访问更多数据源 |
| `exclude_driver` | `true` | 是否排除 Driver Task，主要用于 Task 查询 |
| `filter_keys` | 无 | 可重复的过滤字段 |
| `filter_predicates` | 无 | 与过滤字段一一对应，只支持 `=` 或 `!=` |
| `filter_values` | 无 | 与过滤字段一一对应的值 |

`filter_keys`、`filter_predicates`、`filter_values` 必须按组重复出现。多组过滤条件按 AND 组合。
示例中 `'filter_predicates=='` 的第一个 `=` 分隔参数名和值，第二个 `=` 才是谓词值。

State 列表响应使用以下包装结构：

```json
{
  "result": true,
  "msg": "",
  "data": {
    "result": {
      "total": 10,
      "num_after_truncation": 10,
      "num_filtered": 3,
      "result": [],
      "partial_failure_warning": ""
    }
  }
}
```

实际资源数组位于 `.data.result.result`。还要检查：

- `total`：集群可用资源总数。
- `num_after_truncation`：数据源截断后剩余的数量。
- `num_filtered`：应用过滤条件后的数量。
- `partial_failure_warning`：部分数据源不可用时的告警。

不要把受 `limit` 限制、数据源截断或部分失败的结果当成完整集群快照。

### 4.3 查询存活 Actor

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/actors" \
  --data-urlencode 'detail=true' \
  --data-urlencode 'filter_keys=state' \
  --data-urlencode 'filter_predicates==' \
  --data-urlencode 'filter_values=ALIVE' \
  | jq '.data.result.result'
```

按 Actor ID 查询：

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/actors" \
  --data-urlencode 'detail=true' \
  --data-urlencode 'filter_keys=actor_id' \
  --data-urlencode 'filter_predicates==' \
  --data-urlencode 'filter_values=<actor-id>' \
  | jq '.data.result.result'
```

### 4.4 查询失败 Task

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/tasks" \
  --data-urlencode 'detail=true' \
  --data-urlencode 'exclude_driver=false' \
  --data-urlencode 'filter_keys=state' \
  --data-urlencode 'filter_predicates==' \
  --data-urlencode 'filter_values=FAILED' \
  | jq '.data.result.result'
```

`detail=true` 时可以获得错误、日志位置和更多调度信息，但查询成本更高。

### 4.5 查询 Node

首次接入某个 Ray 版本时先查看完整 JSON：

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/nodes" \
  --data-urlencode 'detail=true' \
  --data-urlencode 'limit=100' \
  | jq '.data.result'
```

只查看常用字段：

```bash
curl -fsS "${RAY_DASHBOARD_URL}/api/v0/nodes" \
  | jq '.data.result.result[] | {
      node_id,
      node_ip,
      node_name,
      is_head_node,
      state,
      state_message,
      resources_total
    }'
```

字段会随 Ray 版本演进；生产脚本不要假设所有版本都返回完全相同的详细字段。

## 5. 日志 API

Ray 2.53.0 的日志接口分为两步：先列出某个节点的日志文件，再按 Actor、Task、进程或文件
读取。

### 5.1 日志端点

| 端点 | 返回类型 | 用途 |
| --- | --- | --- |
| `GET /api/v0/logs` | JSON | 列出指定节点上的日志文件 |
| `GET /api/v0/logs/file` | `text/plain` | 返回匹配日志的有限行数 |
| `GET /api/v0/logs/stream` | `text/plain` 流 | 持续返回新增日志 |

### 5.2 选择日志的规则

| 选择方式 | 是否还需要节点 | 常用辅助参数 |
| --- | --- | --- |
| `actor_id` | 不需要，Ray 会解析所在节点 | `suffix`、`lines` |
| `task_id` | 不需要，Ray 会解析所在节点 | `attempt_number`、`suffix`、`lines` |
| `pid` | 需要 `node_id` 或 `node_ip` | `suffix`、`lines` |
| `filename` | 需要 `node_id` 或 `node_ip` | `lines` |
| `submission_id` | 需要 `node_id` 或 `node_ip` | Job 日志优先使用 Jobs API |

其他规则：

- `node_id` 与 `node_ip` 只能选择一个。
- `suffix` 默认是 `out`，常用值为 `out` 和 `err`。
- `lines` 默认是 `1000`。
- `attempt_number` 默认是 `0`，Task 重试时要选对 Attempt。
- `timeout` 默认是 `30` 秒。
- `interval` 只用于 `/stream`。
- `filter_ansi_code=true` 会移除终端颜色等 ANSI 控制码。
- `/file` 和 `/stream` 返回纯文本，不能再通过 `jq` 解析。

### 5.3 列出节点日志文件

按节点 IP 查找 Worker stderr 文件：

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs" \
  --data-urlencode 'node_ip=<node-ip>' \
  --data-urlencode 'glob=worker-*.err' \
  --data-urlencode 'timeout=30' \
  | jq '.data.result'
```

也可以把 `node_ip` 换成 `node_id`。`glob` 默认是 `*`。

### 5.4 读取 Actor stderr

这是最常用的 Actor 错误日志查询：

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs/file" \
  --data-urlencode 'actor_id=<actor-id>' \
  --data-urlencode 'suffix=err' \
  --data-urlencode 'lines=50' \
  --data-urlencode 'filter_ansi_code=true'
```

读取 Actor stdout 时把 `suffix=err` 改为 `suffix=out`。

### 5.5 读取 Task 指定 Attempt 的 stderr

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs/file" \
  --data-urlencode 'task_id=<task-id>' \
  --data-urlencode 'attempt_number=1' \
  --data-urlencode 'suffix=err' \
  --data-urlencode 'lines=200' \
  --data-urlencode 'filter_ansi_code=true'
```

Task 第一次执行的 `attempt_number` 是 `0`。只有发生重试时才使用更大的值。

### 5.6 按节点和 PID 读取日志

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs/file" \
  --data-urlencode 'node_id=<node-id>' \
  --data-urlencode 'pid=<pid>' \
  --data-urlencode 'suffix=err' \
  --data-urlencode 'lines=100' \
  --data-urlencode 'filter_ansi_code=true'
```

`pid` 只在指定节点内有意义，因此必须同时给出 `node_id` 或 `node_ip`。

### 5.7 按节点和文件名读取日志

读取 Head 节点 GCS stderr：

```bash
curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs/file" \
  --data-urlencode 'node_id=<head-node-id>' \
  --data-urlencode 'filename=gcs_server.err' \
  --data-urlencode 'lines=200' \
  --data-urlencode 'filter_ansi_code=true'
```

`filename` 可以是 Ray 日志目录下的相对嵌套路径。先调用 `/api/v0/logs` 确认真实文件名，
不要猜测 Worker 文件名。

### 5.8 实时追踪 Actor 日志

`curl -N` 关闭输出缓冲，连接会持续保持：

```bash
curl -fsS -N -G "${RAY_DASHBOARD_URL}/api/v0/logs/stream" \
  --data-urlencode 'actor_id=<actor-id>' \
  --data-urlencode 'suffix=err' \
  --data-urlencode 'lines=50' \
  --data-urlencode 'interval=1' \
  --data-urlencode 'filter_ansi_code=true'
```

结束追踪时按 `Ctrl-C`。自动化系统要同时设置客户端超时和断线重连策略，避免永久悬挂。

## 6. 常见错误与排查

### 6.1 HTTP 错误

| 状态码 | 常见原因 | 下一步 |
| ---: | --- | --- |
| `400` | 参数组合错误、日志选择器不足、操作的不是 Submission Job | 检查响应正文；确认三组过滤参数长度一致，日志选择器和节点参数满足规则 |
| `401` / `403` | 缺少 Token 或 Token 不匹配 | 确认集群和客户端使用同一 Token；不要在聊天或日志中输出 Token |
| `404` | Job、Actor、Task、Node 或日志文件不存在 | 先用列表接口确认 ID、节点和生命周期；短生命周期实体可能已经退出 |
| `429` | State API 并发请求达到服务端限制 | 降低轮询频率、增加退避，不要对每个对象同时发起详细查询 |
| `500` | Dashboard Agent 不可用、State 数据源超时、Runtime Environment 或内部错误 | 查看响应正文、`dashboard.log`、`dashboard_agent.log` 和 Job 日志；不要直接归因于训练代码 |

### 6.2 最短排错顺序

1. 确认 Dashboard 和版本：

   ```bash
   curl -fsS "${RAY_DASHBOARD_URL}/api/version" | jq .
   ```

2. 检查节点状态：

   ```bash
   curl -fsS "${RAY_DASHBOARD_URL}/api/v0/nodes" \
     | jq '.data.result.result[] | {node_id, node_ip, state, state_message}'
   ```

3. 检查 Job 阶段和错误消息：

   ```bash
   curl -fsS "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>" \
     | jq '{status, message, error_type, driver_info}'
   ```

4. 查看 Driver/Entrypoint 日志：

   ```bash
   curl -fsS "${RAY_DASHBOARD_URL}/api/jobs/<submission-id>/logs" \
     | jq -r '.logs'
   ```

5. 使用 `/api/v0/tasks` 或 `/api/v0/actors` 找到失败实体，再用 `/api/v0/logs/file`
   获取它的 stderr。

### 6.3 日志为空时

- 确认 `suffix` 是 `out` 还是 `err`。
- Task 重试后确认 `attempt_number`。
- 确认 Actor 或 Task 还存在于 State API 中。
- 按文件读取前先用 `/api/v0/logs` 获取准确文件名。
- Submission Job 的 Driver 日志优先使用 `/api/jobs/<submission-id>/logs`。
- State API 返回成功但资源不全时检查 `partial_failure_warning` 和数量字段。

## 7. CLI 与 Python SDK 何时更合适

直接调用 HTTP 适合接入非 Python 系统、临时排错和受控平台封装；以下情况优先使用官方
客户端：

- 上传本地 `working_dir` 或 `py_modules`。
- 持续追踪 Job WebSocket 日志。
- 需要自动读取 Ray Token 配置。
- 希望减少对 Alpha State REST 返回结构的直接依赖。

常用 CLI：

```bash
ray job list --address="${RAY_DASHBOARD_URL}"
ray job status --address="${RAY_DASHBOARD_URL}" '<submission-id>'
ray job logs --address="${RAY_DASHBOARD_URL}" '<submission-id>'
ray job logs --address="${RAY_DASHBOARD_URL}" -f '<submission-id>'
ray job stop --address="${RAY_DASHBOARD_URL}" '<submission-id>'
```

Python Jobs SDK：

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://xxxray.com")

jobs = client.list_jobs()
status = client.get_job_status("<submission-id>")
logs = client.get_job_logs("<submission-id>")
```

## 8. 安全边界

Ray Dashboard、Ray Jobs 和 Ray Client 能让客户端在集群上执行任意代码。任何能访问这些
端口的主体都应被视为拥有集群计算资源和工作负载数据的高权限访问能力。

- 默认绑定回环地址或受控内网，不把 `8265` 直接暴露到公网。
- 使用防火墙、VPN、SSH 端口转发或带认证授权的 HTTPS 代理限制访问来源。
- Ray 2.53.0 建议启用 Token Authentication，但仍需网络隔离与传输加密。
- 不在 Job `entrypoint`、`metadata`、Runtime Environment 或日志中写入 Token、对象存储密钥
  和其他长期凭据。
- 不同信任边界的工作负载使用独立 Ray Cluster；Namespace 不是强隔离机制。
- 停止或删除 Ray Job 前先确认 `submission_id`、状态和影响范围。

## 9. 官方参考

- [Ray Jobs REST API](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/rest.html)
- [Ray Jobs OpenAPI](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/api.html)
- [Ray State API](https://docs.ray.io/en/latest/ray-observability/reference/api.html)
- [Ray Token Authentication](https://docs.ray.io/en/latest/ray-security/token-auth.html)
- [Ray Security](https://docs.ray.io/en/latest/ray-security/index.html)

本手册以 Ray 2.53.0 官方 OpenAPI 与源码为核对基准。升级后如果示例与实际响应不一致，
以目标集群 `/api/version`、对应 Ray 版本官方文档和源码为准。
