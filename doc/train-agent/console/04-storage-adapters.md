# 04 存储适配器与 Redis 演进

> **历史 Console 设计（2026-10-16 标注）**：下文“当前/本期/首版”指重构前版本，
> 不作为现行功能或部署依据。当前为通用 Host，无内置 Galatea Adapter；
> Skill/MCP、前端构建和 SQLite 观察存储以现行组件文档为准。
> 当前边界见 [总体架构](../../agent-galatea.md)、[Host 文档](https://github.com/lunasaw/open-codex/blob/b5d9a0d26b14a8d8c0c308b6229bba866ac4d70a/docs/README.md)。

本期只为网页聊天与追踪台保存观察数据；存储端口不管理模型状态、训练作业或工具执行权限。
首版的唯一观察后端是本进程内存，Redis 仅作为后续可替换实现的设计边界。

## 1. 适配范围

定义一个面向观察语义的 `ConsoleObservationStore`，包含事件、关联索引和派生快照。
首版提供 `MemoryConsoleObservationStore`；未来提供 Redis 实现。
Host 的 `AtomicJsonStore`、`OperationJournal` 和进程控制不纳入这个端口。

HTTP、observer、projector 不能判断“当前是 Redis 还是内存”来改变业务行为。
`get/set/xadd/zrange` 不是这个端口的公共方法；仅包一层 Redis 客户端不能满足可切换要求。

## 2. 异步端口合同

以下接口是设计草案，相关类型定义在 `console/models.py`，不要求照抄参数名：

```python
class ConsoleObservationStore(Protocol):
    async def open(self, epoch: str) -> StoreInfo: ...

    async def commit(self, batch: ObservationBatch) -> CommitReceipt: ...

    async def read_events(self, query: EventQuery) -> EventPage: ...

    async def snapshot(self, query: SnapshotQuery) -> ViewSnapshot: ...

    async def wait_for_change(self, after: Cursor, timeout: float) -> Cursor: ...

    async def stats(self) -> StoreStats: ...

    async def close(self) -> None: ...
```

| 操作 | 必须满足的语义 |
| --- | --- |
| `open` | 建立当前观察 epoch；返回 backend、retention 和起始水位；不读取 Host 业务状态 |
| `commit` | 原子追加一批连续 seq 的事件，及其绑定/投影增量；事件与快照提交水位一致；重复同批同内容不重复追加，冲突失败 |
| `read_events` | 固定查询 scope/filter/上界水位，按 seq 升序返回分页；提供扫描游标，不只提供最后一条匹配记录的序号 |
| `snapshot` | 返回截至同一已提交水位的展示快照/分页、绑定版本和历史范围；无待提交事件混入 |
| `wait_for_change` | 检查当前水位并原子注册等待；超时正常返回；只能作为唤醒优化，数据仍从 read_events 获取 |
| `stats` | 当前 epoch、水位、事件数、编码字节数、视图数；不把编码体积冒充进程 RSS |
| `close` | 释放连接/等待器；内存适配器不落盘 |

`ObservationBatch` 包含稳定 batch_id、epoch、连续事件序号和可重放的投影变更。
只有写入 worker 构造 batch；store 不重新分配序号。相同 ID 不同内容返回 `CommitConflict`，
不进行 last-write-wins 覆盖。序号前跳返回 `SequenceGap`，避免把丢批次伪装成完整历史。

查询类错误至少包含：`InvalidCursor`、`EpochMismatch`、`CursorAhead`、`UnknownSession`；
基础设施异常包含 `StoreUnavailable`。Host 会话是否存在由查询服务验证，不能以观察存储没有
这个 session 的事件就返回“会话不存在”。

`EventQuery` 至少表达 scope、after、until、limit 和结构化 filters；单事件详情用同一查询端口
精确读取该 event_id。`SnapshotQuery` 表达 session、可选 turn、view 类型、until 及分页令牌。
`ViewSnapshot` 返回 messages/turns/steps 所请求的一类或首屏组合、watermark、next_page 和
binding_version。API 不直接读取内部视图容器。

## 3. 内存结构与并发

建议结构：

- 全局有序事件容器：事件只追加，信封和 payload 存不可变副本。
- session/thread/turn/item/RPC 关联索引：保存事件序号引用，不复制完整 payload。
- session 对话、turn Loop 等派生视图：保留完整当前视图，包含来源事件序号及关联版本。
- batch 去重与提交水位：一个顺序写入者控制提交，读取不看到半个 batch。
- 水位条件变量：唤醒 SSE 等待者，订阅之间相互独立。

端口方法使用 async，为未来 Redis I/O 保留调用方式；memory 实现在事件循环内短临界区完成
更新。Host/Registry 的线程执行若需要提交观察，应经 `loop.call_soon_threadsafe` 回到同一
事件循环，不能直接从线程修改容器。写入同一事件多次不重复，内容冲突可见。

入站队列无丢弃上限，单 worker 顺序构造 batch 并提交。Memory commit 不等待外部 I/O；
接口失败保留原 batch 与后续待提交事件，状态标为 pending/error，不跳过失败 batch 继续提交。
未来 Redis 超时重试同一 batch_id，防止“服务端已写入但客户端未收到确认”造成重复事件。

Queue、关联表、派生视图与快照的操作需要避免整份历史反复复制；API 的分页大小只限制一次
响应数量，不限制事件本身长度和历史总量。原生 RPC 已有的帧大小/输入限制继续生效，Console
不额外截断已被 Runtime/Host 接纳的有效内容。

## 4. 保留与内存成本

按用户 Q5：服务进程存活期间全部保留，重启才清空。

- 不设置 TTL、最大事件数或按容量淘汰。
- 不因长时间无人浏览而暂停采集，不因查询历史刷新/延长保留期。
- 不采用定长 deque、LRU、delta 抽样或历史压缩后删除原事件。
- 活跃 RPC 配对表可在响应后移除，因为完整请求/响应已经保存在事件记录；这不是删除历史。
- 允许释放可重新计算的查询临时缓存和浏览器 DOM，不能释放唯一的事件内容。

空间随运行时长与实际流量增长，近似为“事件数 × 平均事件大小 + 关联索引 + 展示投影 + 未提交队列”。
状态页显示增长情况，不给出未经测量的固定可运行天数。出现进程级资源故障后，历史按内存模式语义丢失，
不能承诺无限流量下的零丢失。

投用前应对目标训练对话场景记录事件产生速率、`event_count`、`encoded_bytes`、队列积压和进程
RSS，形成受控使用时段与运维监控阈值；这些阈值由实测和部署容量确定，不作为自动清理条件。
Q5 保证的是正常进程生命周期内不主动淘汰已提交事件，不是内存容量无限或进程故障后的持久保留。

## 5. 游标、快照与过滤

当前 epoch 的事件不被淘汰，因此正常查询不存在 retention gap。
`after` 为排他下界，`until` 为包含上界；分页可固定 until，以避免历史查询被不断新增的事件拖住。
第一次请求可省略 after，解释为当前 epoch 起点。显式携带旧 epoch 游标必须报错，不能静默从零重读。

`EventPage` 返回：

```json
{
  "epoch": "epoch-demo",
  "events": [],
  "next_cursor": "epoch-demo:42",
  "watermark": "epoch-demo:42",
  "has_more": false,
  "binding_version": 3,
  "coverage": {"status": "complete", "reason": null}
}
```

即使 scope/filter 没有匹配事件，扫描到的游标也前进，防止一再扫描无关记录。
从运行实例全局事件投影到 session 时不能把其他会话的序号间隔当作丢失。
绑定后补的旧记录按 [事件关联规则](03-events.md#4-关联规则与乱序) 触发快照重载。

查询水位也限定绑定版本：后来的绑定不能悄悄改变已经固定水位的历史页。
首版可使用不可变事件与带 seq 的绑定事实，在指定 session/turn 的索引范围内重放到 until，
得到固定水位的派生分页；当前视图只是最新水位的加速缓存。这样不需要保存每个 token 的整份
视图副本，也不依赖会被后续 delta 改写的可变对象实现历史分页。
分页令牌失效时明确返回错误并要求重取，不能转为当前水位继续上一页。

`snapshot` 必须保证对话/Loop 与其 `watermark` 一致。前端先读取快照水位 H，再订阅 `after=H`：
快照读取与订阅之间新增的事件仍在日志中，通过回放补齐。`wait_for_change` 必须复查水位，
避免提交发生在 read 与等待注册之间导致丢唤醒；有限等待超时后还会再次查询。
绑定变更、观察故障等控制信息独立于 method/kind 展示筛选，不能因为当前筛选无匹配消息而漏发。

当前代没有初始历史缺口不表示观察器一定健康。采集/投影异常需通过独立状态和可存储的
`observation_error` 事件报告；无法记录到日志时，状态接口仍需提供不完整标志。

## 6. 后续 Redis 实现

Redis 阶段只替换 `create_observation_store(config)` 的装配结果，API、事件信封、游标、关联和
projector 保持不变。首版配置指定 `backend=redis` 时明确报 unsupported，不悄悄退回内存。

后续适配器可以把日志、索引、快照和提交水位映射到 Redis 原语，但必须满足：

1. 以 agent/epoch 隔离键；同一 epoch 仍只有一个观察写入者。
2. 事件、索引、绑定版本、快照及 batch 去重采用一次原子提交；Redis Cluster 情况下需同槽或
   显式缩小原子单元，不能把跨槽多次写入声称为事务。
3. 公共 cursor 仍是 epoch/seq；原生 Redis Stream ID 不能泄露成 HTTP 依赖。
4. 通知只用于唤醒，不能仅依赖 Pub/Sub 当历史；断线之后按已提交日志补读。
5. 不能因切换 Redis 默认启用 TTL、MAXLEN 或允许缓存驱逐静默删除事件；保留策略改变需要另行定义。
6. Redis 断开时保留待提交批次，报告积压和错误；同一 batch 幂等重试，不采样和不宣称已提交。
7. Redis 持久化/AOF/备份与跨重启保留是其部署与产品合同，不能由“使用 Redis”自动推导。

一期不实现 Redis 客户端、不加 Redis 依赖、不接现有其他服务的 Redis，也不设计双写迁移。
因此首版不应出现为了 Console 观察层而新增的外部存储部署步骤。
后续切换方式为停止服务、切换已实现的 adapter、启动新 epoch；旧内存历史不承诺迁移。
如果要求 Redis 跨 Host 重启浏览旧 epoch，再增加明确历史索引与 API，但不恢复旧 socket/Future。

Redis 支持共享观察数据不等于 Host 支持多实例：Host 文件锁、Runtime 所有权、操作路由和业务
幂等仍是另一项改造。本次不能通过 `uvicorn --workers N` 来扩容内存模式。
