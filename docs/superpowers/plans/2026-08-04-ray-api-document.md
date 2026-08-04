# Ray 常用 API 文档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建 `doc/ray-api.md`，提供基于 Ray 2.53.0、可复制使用的 Jobs、State 和日志 REST API 手册。

**Architecture:** 文档按“先连接与认证、再管理 Job、再观察 State、最后定位日志和排错”的使用路径组织。Jobs REST API 以官方 OpenAPI 为准，State 和 `/api/v0/logs/*` 明确标注稳定性，所有示例仅使用占位地址和占位标识。

**Tech Stack:** Markdown、curl、jq、Ray Dashboard HTTP API、Ray Jobs REST API 4.0.0、Ray State API、Ray 2.53.0 Token Authentication。

## Global Constraints

- 目标版本是仓库当前固定的 Ray 2.53.0。
- 只创建 `doc/ray-api.md`；不修改服务配置、训练代码、README 或现有部署文档。
- 不改动工作区中已有的 `README.md`、`images/`、`outputs/` 和其他用户文件。
- 示例地址统一使用 `http://xxxray.com` 或变量 `RAY_DASHBOARD_URL`，标识符统一使用 `<submission-id>`、`<actor-id>` 等占位内容。
- 不执行任何真实作业提交、停止、删除或日志读取请求。
- Jobs REST API 标记为主要 HTTP 接口；State API 标记为 Alpha；`/api/v0/logs/*` 标记为版本相关接口。
- 安全说明必须指出 Dashboard/Jobs API 可执行任意代码，生产访问需要内网隔离、HTTPS 和认证控制。

---

### Task 1: 创建连接、认证与 Ray Jobs API 章节

**Files:**
- Create: `doc/ray-api.md`

**Interfaces:**
- Consumes: Ray 2.53.0 Jobs OpenAPI 路由 `/api/version`、`/api/jobs/`、`/api/jobs/{submission_id}`、`/stop`、`/logs`、`/logs/tail`。
- Produces: 后续章节复用的 `RAY_DASHBOARD_URL` 约定、认证说明、`<submission-id>` 命名和返回值说明。

- [ ] **Step 1: 写入文档标题、适用范围和稳定性表**

  创建 `doc/ray-api.md`，明确以下三类接口：

  | 接口 | 文档定位 | 使用建议 |
  | --- | --- | --- |
  | Ray Jobs REST API | 官方 OpenAPI，API 版本 4.0.0 | 作业管理首选 |
  | State REST API | Alpha | 只读观测，升级后回归验证 |
  | `/api/v0/logs/*` | State/Dashboard 版本相关接口 | 排错使用，保留 CLI/SDK 兜底 |

- [ ] **Step 2: 写入基础地址、版本检查和 Token 认证示例**

  必须包含：

  ```bash
  export RAY_DASHBOARD_URL='http://xxxray.com'

  curl -fsS "${RAY_DASHBOARD_URL}/api/version" | jq .
  ```

  Token Authentication 说明必须写明：Ray 2.52.0 起支持，Ray 2.53.0 默认仍未启用；启用后
  `curl` 需要 `Authorization: Bearer <token>`，Token 不能写入仓库，HTTP 明文传输不能用于远程网络。

- [ ] **Step 3: 写入 Ray Jobs 端点速查表**

  端点表必须包含：

  ```text
  GET    /api/version
  GET    /api/jobs/
  POST   /api/jobs/
  GET    /api/jobs/<submission-id>
  GET    /api/jobs/<submission-id>/logs
  POST   /api/jobs/<submission-id>/stop
  DELETE /api/jobs/<submission-id>
  WS     /api/jobs/<submission-id>/logs/tail
  ```

  说明集合端点保留尾部 `/`，请求字段使用 `submission_id`，`job_id` 已废弃。

- [ ] **Step 4: 写入提交、列表、详情、日志、停止和删除示例**

  提交示例至少包含以下请求体，元数据值必须是字符串：

  ```bash
  curl -fsS -X POST "${RAY_DASHBOARD_URL}/api/jobs/" \
    -H 'Content-Type: application/json' \
    --data '{
      "entrypoint": "python scripts/train.py --config configs/baseline.yaml",
      "submission_id": "project-train-001",
      "runtime_env": {},
      "metadata": {"project": "example", "purpose": "training"},
      "entrypoint_num_cpus": 1,
      "entrypoint_num_gpus": 0
    }' | jq .
  ```

  同章说明原始 REST 请求不会自动上传客户端本地目录；本地代码打包应使用
  `ray job submit --working-dir` 或 Python SDK，REST 的 `working_dir` 应使用集群可访问的远程 URI。
  `submission_id` 重复提交会被拒绝；安全重试应先按该 ID 查询已有 Job。说明状态
  `PENDING`、`RUNNING`、`STOPPED`、`SUCCEEDED`、`FAILED`；停止返回
  `{"stopped": true|false}`，删除返回 `{"deleted": true|false}`，Job 日志返回
  `{"logs": "..."}`。实时 Job 日志优先推荐 `ray job logs -f` 或
  `JobSubmissionClient.tail_job_logs()`，因为 `/logs/tail` 是 WebSocket。

- [ ] **Step 5: 检查 Task 1 的结构和端点**

  Run:

  ```bash
  rg -n '^#|/api/version|/api/jobs/|submission_id|PENDING|SUCCEEDED|Authorization' doc/ray-api.md
  git diff --check -- doc/ray-api.md
  ```

  Expected: 所有 Jobs 端点、状态和认证说明均命中；`git diff --check` 无输出并返回 0。

---

### Task 2: 添加 State API 与日志 API 操作手册

**Files:**
- Modify: `doc/ray-api.md`

**Interfaces:**
- Consumes: Task 1 定义的 `RAY_DASHBOARD_URL`、认证约定和占位标识格式。
- Produces: State 查询参数表、JSON 返回路径、日志选择规则和可复制的排错命令。

- [ ] **Step 1: 写入 State API 资源端点表**

  覆盖以下只读端点：

  ```text
  /api/v0/actors
  /api/v0/jobs
  /api/v0/nodes
  /api/v0/placement_groups
  /api/v0/workers
  /api/v0/tasks
  /api/v0/objects
  /api/v0/runtime_envs
  ```

- [ ] **Step 2: 写入 State 公共查询参数和返回结构**

  参数表必须包括：`limit` 默认 100、最大 10000；`timeout` 默认 30 秒；`detail` 默认
  `false`；`exclude_driver` 默认 `true` 且主要用于 Tasks；可重复的 `filter_keys`、
  `filter_predicates`、`filter_values` 必须成组出现，谓词仅支持 `=` 与 `!=`，多组过滤按 AND 组合。

  说明资源数组位于 `.data.result.result`，并提醒检查 `.data.result.partial_failure_warning`、
  `total`、`num_after_truncation` 和 `num_filtered`，不能把受限或部分失败的结果当成完整快照。

- [ ] **Step 3: 写入存活 Actor、失败 Task 和节点查询示例**

  Actor 示例必须使用成组过滤：

  ```bash
  curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/actors" \
    --data-urlencode 'detail=true' \
    --data-urlencode 'filter_keys=state' \
    --data-urlencode 'filter_predicates==' \
    --data-urlencode 'filter_values=ALIVE' \
    | jq '.data.result.result'
  ```

  Task 示例使用 `state=FAILED` 和 `exclude_driver=false`；Node 示例输出节点 ID、IP、状态和资源字段，
  并注明具体字段会随 Ray 版本演进，首次接入应先查看完整 JSON。

- [ ] **Step 4: 写入日志文件列表和日志读取规则**

  日志列表使用 `GET /api/v0/logs`，要求提供 `node_id` 或 `node_ip`，可选 `glob` 与 `timeout`。
  日志读取使用 `/api/v0/logs/file` 或 `/api/v0/logs/stream`，并记录以下规则：

  - `actor_id` 或 `task_id` 可以让 Ray 解析日志所在节点。
  - 通过 `pid`、`filename` 或 `submission_id` 查询时还要给 `node_id` 或 `node_ip`。
  - `node_id` 与 `node_ip` 不应同时提供。
  - `suffix` 默认 `out`，常用值为 `out`、`err`。
  - `lines` 默认 1000，`attempt_number` 默认 0，`timeout` 默认 30 秒。
  - `interval` 仅用于 `stream`，`filter_ansi_code=true` 可去除 ANSI 控制码。
  - `/api/v0/logs/file` 和 `/stream` 返回 `text/plain`，不是 JSON。

- [ ] **Step 5: 写入用户给出的 Actor stderr 示例及其他常用示例**

  Actor 示例保留用户的参数组合，但改为占位 ID：

  ```bash
  curl -fsS -G "${RAY_DASHBOARD_URL}/api/v0/logs/file" \
    --data-urlencode 'actor_id=<actor-id>' \
    --data-urlencode 'suffix=err' \
    --data-urlencode 'lines=50' \
    --data-urlencode 'filter_ansi_code=true'
  ```

  另外增加 Task 指定 `attempt_number`、节点 + PID、节点 + `filename`、`curl -N` 实时 Actor
  日志四个示例。Job Submission 日志优先指向 `/api/jobs/<submission-id>/logs`，避免把 Job 日志
  查询建立在版本相关的 State 日志解析上。

- [ ] **Step 6: 检查 Task 2 的参数覆盖**

  Run:

  ```bash
  for value in actors tasks nodes workers objects runtime_envs \
    filter_keys filter_predicates filter_values actor_id task_id node_id node_ip \
    filename pid suffix lines attempt_number interval filter_ansi_code; do
    rg -q "$value" doc/ray-api.md || exit 1
  done
  git diff --check -- doc/ray-api.md
  ```

  Expected: 循环和 `git diff --check` 都返回 0。

---

### Task 3: 添加排错、安全与最终验证

**Files:**
- Modify: `doc/ray-api.md`

**Interfaces:**
- Consumes: Task 1 的 Jobs 操作和 Task 2 的 State/日志操作。
- Produces: 可交付的完整文档、故障定位顺序和验证证据。

- [ ] **Step 1: 写入常见 HTTP 错误表**

  覆盖：400 参数或 Job 类型错误；401/403 Token 缺失或错误；404 标识不存在；429 State API
  并发请求达到限制；500 Dashboard Agent、数据源、Runtime Env 或内部错误。每项给出下一步检查，
  不把 500 简化为应用代码错误。

- [ ] **Step 2: 写入最短排错顺序**

  顺序固定为：

  1. `/api/version` 验证地址、Ray 版本与 Jobs API 版本。
  2. `/api/v0/nodes` 检查节点状态。
  3. `/api/jobs/<submission-id>` 检查作业阶段和 `message`。
  4. `/api/jobs/<submission-id>/logs` 检查 Driver 日志。
  5. `/api/v0/tasks` 或 `/api/v0/actors` 定位失败实体。
  6. `/api/v0/logs/file` 读取对应 stderr。

- [ ] **Step 3: 写入安全边界和官方参考**

  明确 Dashboard、Jobs 和 Ray Client 具有集群任意代码执行能力；Token Authentication 是纵深防御，
  不能代替隔离网络与 TLS；Token 不能进入命令历史、日志或 Git。链接到 Ray 2.53 对应的 Jobs REST、
  State API 和 Token Authentication 官方文档。

- [ ] **Step 4: 验证 Markdown 结构、占位数据和变更范围**

  Run:

  ```bash
  test -f doc/ray-api.md
  fence_count=$(rg -c '^```' doc/ray-api.md)
  test $((fence_count % 2)) -eq 0
  ! rg -n 'Authorization: Bearer [A-Za-z0-9_-]{16,}' doc/ray-api.md
  git diff --check -- doc/ray-api.md
  git status --short
  ```

  Expected: 文件存在，代码围栏为偶数，不含用户 Actor ID 或真实 Token，差异检查返回 0；状态输出
  允许出现任务开始前就存在的 README、`docs/superpowers/plans/`、`outputs/` 改动，但本任务新增文件
  只能是 `doc/ray-api.md` 和本计划文件。

- [ ] **Step 5: 逐项核对接口事实**

  确认：Jobs 集合端点带尾部 `/`；Stop/Delete/Logs 返回结构正确；State 过滤为三组重复参数；
  `/api/v0/logs/file` 返回纯文本；Actor 示例不要求 node 参数；PID 和 filename 示例要求节点参数；
  认证说明准确反映 Ray 2.53.0。

- [ ] **Step 6: 提交文档文件**

  ```bash
  git add -- doc/ray-api.md
  git commit -m 'docs: add Ray API guide'
  ```

  Expected: 提交只包含 `doc/ray-api.md`，不包含 README、计划目录、图片或 `outputs/`。
