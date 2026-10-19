# 08 部署与运维

## 1. 首版拓扑

同一受控主机或私有网络内运行：

1. 现有 Codex Host 与原生运行时，启用持久化 Console 存储。
2. Galatea App 服务：MCP Streamable HTTP、内部 API、Task Service、SQLite 与 Worker。
3. 外置 Task Provider，通过受认证的内部 API 访问服务。
4. 已有治理服务与 Ray/MLflow/Artifact 服务，使用显式 profile 和最小权限凭据。

MCP 与内部 API 可同进程不同 listener，内部接口绑定 loopback 或受保护网络，
跨主机时使用 TLS/mTLS。浏览器只能访问 Host，iframe 不直接访问内部 API。
若使用反向代理，认证头只能由可信代理设置，必须移除来自客户端的同名身份头。

具体地址、用户、端口在部署 profile 中设置。本方案不占用原型端口、不修改现有服务。
当前 Host 使用配置级固定 principal，首版必须是单 owner 的受认证部署；
多 owner 采用不同 Host 身份域及独立 Provider/MCP 凭据，不能在同一固定 principal 下伪装租户隔离。
单个固定 bearer 的共享 MCP 部署不能假装具备多用户身份隔离；
Task 权限依赖已验证 Host 主体，外部治理主体的能力范围也必须足够收敛。

## 2. 配置边界

配置分三层：管理员部署配置、任务级用户范围、治理系统授权。
模型只能在后两者已允许的范围内选择动作，不能改服务配置或密钥路径。

以下是**新增 Galatea 服务的设计示意**，不是当前 Host 接受的配置格式：

```yaml
schema_version: galatea-app-config/v1
storage:
  backend: sqlite
  database_path: ${GALATEA_APP_STATE_DIR}/tasks.sqlite3
host:
  profile_ref: codex-host-local
  trusted_keyset_ref: host-signing-keys
  continuation_credential_ref: host-continuation-client
mcp:
  profile_ref: galatea-app-mcp
governance:
  route: governance_mcp
  profile_ref: governed-development
workers:
  observation_interval_seconds: 10
  observation_max_interval_seconds: 60
  continuation_max_attempts: 5
limits:
  max_stages_per_task: 50
  max_active_stage_per_task: 1
  max_active_tasks_per_session: 1
  snapshot_max_bytes: 131072
  event_page_max_items: 100
  task_auto_continuations: 20
```

环境变量展开、profile/credential 解析和配置 Schema 都需要实现并做启动校验。
示例不包含真实密钥或可直接使用的内部 endpoint。
Host 继续使用现有 `codex-agent/v2` 配置，在其 Provider 目录登记精确工具和 Python factory；
不要把本段 `galatea-app-config/v1` 粘贴进 Host 配置。
Host 新增通用 proof、event exporter、continuation 配置时必须增加独立 Schema 验证与禁用默认值。

## 3. 持久化与容量

状态目录配置在 Git 忽略的运行路径，例如受管理的 `platform-data/galatea-app/`，
提交中只保存迁移、契约和无秘密配置模板。数据库、WAL、缓存、日志与凭据均不提交。

| 数据 | 持久化要求 | 建议默认保留 |
| --- | --- | --- |
| Task、Plan、Attempt、交互与命令收据 | 强持久化 | 终态后至少 90 天，治理审计按组织策略 |
| 业务事件与必要外部观察 | 强持久化 | 终态后至少 90 天 |
| 原生操作摘要与应用诊断日志 | 脱敏、可分页 | 30 天，可配置 |
| 完整训练 Artifact | 原制品服务拥有 | 遵循其生命周期，不随 Task 清理擅自删除 |
| 待投递 Outbox / unknown 收据 | 不得提前删除 | 直到明确完成、取消、过期或人工对账 |

清理前确认任务终态且不存在活跃/未知外部操作。删除操作保持审计 tombstone，
不得破坏仍在恢复窗口中的幂等记录。数据删除请求与审计保留冲突按组织策略显式处理。

初期容量假设：100 个活动任务、10 个活跃 App 视图、每任务最多 50 阶段。
这些是性能测试输入，不是已测吞吐量。超出单实例 SQLite 能力时才引入 PostgreSQL，
以同样的事务、唯一键和租约约束迁移，不在首版加入分布式调度。

快照目标小于 128 KiB；事件页不超过 100 条且限制编码字节数，
日志分页保持在 Host 工具结果上限以内。当前动态工具结果上限 256 KiB，
不能把全部历史或大产物塞进一次工具返回。

## 4. 启停与健康检查

启动顺序：载入配置与凭据引用 → 数据库迁移 → 校验可信 Host key 和服务身份 →
读取治理 capabilities → 启动只读入口 → 恢复租约与未决命令 → 启动 Worker → 放开写入口。

新增健康端点：

- `/health/live`：进程是否存活；不查询昂贵的外部依赖。
- `/health/ready`：迁移、存储、可信配置是否可用；缺少关键认证时不接受写入。
- `/health/dependencies`：受认证的详细依赖状态，区分功能降级与服务整体故障。

治理系统暂时不可达时，数据处理任务仍可使用不依赖它的能力；训练标记不可用，
不能使前端不断等待一个实际上不具备的操作。
Host 暂时离线可以接受有期限的排队启动，但必须向用户展示“等待 Host”，而非“执行中”。

关闭时停止领新租约，完成短事务，保存游标；不把外部 Job 的运行寿命绑到 Web 进程。
Worker 超过租期后可由同实例恢复流程重新领取，旧 fencing token 不可回写。

## 5. 可观测性

所有结构化日志使用 task_id、stage_id、attempt_id、operation_id、host_session_id、
thread_id、turn_id、call_id、command_id、receipt_id 进行关联，按需要脱敏。
文本日志不记录签名证明全文、execution_token、数据样本、认证头或对象存储凭据。

建议指标：

- Task 各状态数量、阶段耗时、计划修订次数、验收失败类别。
- 外部观察延迟、状态陈旧数、未知操作数、证据核验延迟。
- Outbox 最老待投递年龄、投递重试、租约冲突、Host 接续排队与启动延迟。
- 重复命令命中率、版本冲突、无效证明、越权拒绝、取消到确认停止耗时。
- 事件导出落后游标、缺口、重建次数、App 快照与事件查询延迟。

告警关注 unknown、长时间取消未确认、授权拒绝异常、Outbox 积压和证据不一致。
正常轮询无变化不发用户通知。任务完成、失败、需要用户决策才产生产品通知事件。

## 6. 服务目标与限制

以下为验收目标，必须通过测试后才能写成运行承诺：

| 指标 | 首版目标与条件 |
| --- | --- |
| 快照查询 P95 | 本地受控环境、10 并发视图下小于 500 ms，不含网络公网往返 |
| 活跃 App 看到状态变化 | 无故障且前台可见时，提交后 5 秒内 |
| 外部终态观察 | 默认观察配置与依赖正常时，60 秒内 |
| 外部事实到接续受理 | Host 在线、无退避时 10 秒内；排队启动单独计时 |
| 服务重启恢复未决记录 | 测试容量下 60 秒内完成扫描，不含外部对账等待 |
| 重复业务副作用 | 重放测试中不重复创建逻辑操作；未知执行必须阻塞 |

模型输出时间、外部排队和训练运行时间不计入 Task Service 查询 SLO。
单机 SQLite 不提供主机故障下零数据损失；实际 RPO/RTO 取决于持久化卷与备份策略。

## 7. 备份、升级与恢复

- 使用 SQLite backup API 或一致性快照；不能仅复制繁忙数据库主文件而遗漏 WAL。
- 备份 Task 数据库、迁移版本、Host 相关请求日志/绑定、签名 key 元数据及必要配置引用。
- Artifact 由所属平台一致备份；恢复后通过 API 验证引用，不复制数据库内路径冒充产物。
- 迁移前停止写入与 Worker，生成可恢复备份；迁移采用版本锁，禁止新旧进程并行写。
- 数据迁移回滚不默认支持。应用回退前验证 Schema 兼容，否则恢复备份并完成外部操作对账。
- 从备份恢复后外部操作可能已前进，先只读重建与对账，确认幂等键、租约和 Host 请求状态，
  再放开新写入，避免重放旧 Outbox 造成重复训练。

上线前演练：服务硬退出、Host 重启、Worker 重领、数据库备份恢复、签名 key 轮换、
凭据失效、授权过期与 Artifact 不可达。演练不需要实际训练，可以使用协议一致的模拟后端。
