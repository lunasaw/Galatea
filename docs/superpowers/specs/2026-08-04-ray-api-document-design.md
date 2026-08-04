# Ray 常用 API 文档设计

## 目标

在 `doc/ray-api.md` 编写一份面向日常运维与训练排错的 Ray API 操作手册。文档以仓库当前
使用的 Ray 2.53.0 为基准，以可直接改写和执行的 `curl` 示例为主，并把用户提供的
`/api/v0/logs/file` Actor stderr 查询作为核心示例。

## 读者与使用场景

- 需要通过 Ray Dashboard HTTP 地址提交、查询或停止训练作业的工程师。
- 需要查询 Actor、Task、Node、Worker、Object 等运行状态的运维人员。
- 需要按 Actor ID、Task ID、PID、文件名或节点定位 stdout/stderr 的排错人员。
- 需要判断应使用 REST、Ray CLI 还是 Python SDK 的平台维护者。

## 文档结构

1. **范围与稳定性**
   - 标注 Ray 2.53.0。
   - 区分 Ray Jobs REST API、Alpha State REST API 和版本相关 Dashboard 接口。
   - 提醒升级 Ray 后先检查 `/api/version` 并做兼容性验证。
2. **连接与认证**
   - 定义 Dashboard 基础地址，默认端口为 8265。
   - 说明 Ray 2.52.0 起可启用 Token Authentication，2.53.0 默认仍未启用。
   - 给出 `Authorization: Bearer <token>` 用法，同时强调 HTTPS、内网或认证代理。
3. **Ray Jobs REST API**
   - 覆盖版本检查、作业列表、提交、详情、日志、停止和删除。
   - 使用 `submission_id`，不推荐已废弃的 `job_id` 请求字段。
   - 说明 `PENDING`、`RUNNING`、`STOPPED`、`SUCCEEDED`、`FAILED` 状态。
   - 说明 `/logs/tail` 是 WebSocket，实时追踪优先使用 `ray job logs -f` 或 SDK。
4. **State REST API**
   - 覆盖 Actors、Jobs、Nodes、Placement Groups、Workers、Tasks、Objects、Runtime Envs。
   - 解释 `limit`、`timeout`、`detail`、`exclude_driver` 和可重复过滤参数。
   - 提供存活 Actor、失败 Task 和节点资源查询示例。
5. **日志 API**
   - 覆盖日志文件列表、Actor 日志、Task 日志、PID/文件日志和流式日志。
   - 解释 `actor_id`、`task_id`、`node_id`、`node_ip`、`filename`、`pid`、`suffix`、
     `lines`、`attempt_number`、`interval` 和 `filter_ansi_code`。
   - 明确 `/api/v0/logs/file` 返回纯文本，而 State 列表接口返回 JSON 包装结构。
6. **排错与安全**
   - 说明常见 400、401/403、404、429、500 错误方向。
   - 提供版本、节点、状态、日志、作业日志的最短排错顺序。
   - 强调能访问 Dashboard/Jobs API 的客户端可能执行任意代码，不能直接暴露公网。

## 内容边界

- 只创建 `doc/ray-api.md`，不修改 Ray 服务配置、训练代码或现有部署文档。
- 不调用用户给出的真实地址，不读取真实 Actor 日志，不执行提交、停止或删除作业。
- 示例使用 `http://xxxray.com`、占位 ID 和无敏感信息的请求体。
- 不收录缺少官方 OpenAPI 或 2.53.0 源码依据的 Dashboard UI 私有端点。
- 不触碰工作区中现有的 README、图片和 `outputs/` 改动。

## 验证

1. `doc/ray-api.md` 中所有代码围栏、表格和标题层级完整。
2. Jobs 端点与 Ray 2.53.0 OpenAPI/源码一致，集合端点保留尾部 `/`。
3. State 与日志查询参数和默认值与 Ray 2.53.0 源码一致。
4. 所有示例均使用占位地址和占位标识，不包含 Token 或内部真实数据。
5. `git diff --check` 通过，Git 变更不包含 README、图片、`outputs/` 等无关文件。
