# 06 实施与验收

> **历史 Console 设计（2026-10-16 标注）**：下文“当前/本期/首版”指重构前版本，
> 不作为现行功能或部署依据。当前为通用 Host，无内置 Galatea Adapter；
> Skill/MCP、前端构建和 SQLite 观察存储以现行组件文档为准。
> 当前边界见 [总体架构](../../agent-galatea.md)、[Host 文档](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/docs/README.md)。

本期交付验收对象是“训练 Agent 的网页聊天与追踪台”：浏览器可以通过受信网络访问现有
Host，完成文字对话，并看到实际的 Codex 协议、Galatea 工具调用和作业状态。验收不包含
模型质量提升、训练算法、在线推理服务、Skill 启用或“优于原生 Codex”的结论。

## 1. 文件级改造范围

以下路径相对 `agents/codex-app-server/`。本次只是方案，表中的动作尚未执行。

| 文件/模块 | 拟实施修改 |
| --- | --- |
| `src/codex_agent/rpc.py` | 在实际收发位置接 observer；传递仅 Host 可见的调用 context；保持 reader、request 分发、ID 与超时合同 |
| `src/codex_agent/process.py` | initialize 前注入 observer/runtime_instance_id；进程失败和结束提供观察事实 |
| `src/codex_agent/host.py` | 在会话/消息操作传递关联；原 events.append 改到观察入口；持久化控制顺序保持原样 |
| `src/codex_agent/events.py` | 将旧 Console JSONL EventLog 迁移为新观察服务；清理不再使用的文件回放和只留少量字段的实现 |
| `src/codex_agent/console/models.py` | 新增事件、分页、快照、游标、完整性结构 |
| `src/codex_agent/console/ports.py` | 新增观察存储端口和领域异常 |
| `src/codex_agent/console/adapters/memory.py` | 新增进程生命周期内完整保留的内存实现 |
| `src/codex_agent/console/observer.py` | 捕获副本、接收序号及异常状态 |
| `src/codex_agent/console/correlation.py` | 双向 RPC ID、thread/turn/item/call/session 关联及晚绑定 |
| `src/codex_agent/console/projection.py` | 脱敏、对话和 Loop 增量投影 |
| `src/codex_agent/console/service.py` | 顺序 worker、原子提交、状态、查询与生命周期 |
| `src/codex_agent/app.py` | 统一构造 store/service/observer，注入 Host、Process、Console |
| `src/codex_agent/console/server.py` | 无鉴权访问；历史、快照、详情与 epoch SSE；保留持久化写操作幂等 |
| `src/codex_agent/console/static/*` | 删除 Token 交互；实现对话、Loop、原生/全局事件和重连 |
| `src/codex_agent/config.py`、`cli.py` | 新 Console 配置、内网监听、删除 token-file 必填与回环强制限制 |
| `src/codex_agent/deployment.py`、`release.py` | unit 参数、去除 Console event_dir/token 依赖；保留模型凭据和 Host/平台发布核验 |
| `config/agent.example.json`、`deploy/codex-agent.service` | 同步新配置与启动命令 |
| `pyproject.toml` | 更新不再适用的 Console 产品描述；维持现有运行时依赖清单 |
| `tests/*`、`README.md`、`docs/*` | 更新现行契约、回归与部署说明，删除已经失效的 JSONL/Token 使用步骤 |

不修改 `pyproject.toml`、`deploy/requirements.in` 中的运行时依赖清单；本期不新增 Node、Redis、
MCP transport、外部追踪服务或独立 Console 包。`galatea-mcp` 已有的传递依赖仍按现有发行包
存在，但不因本方案启动 MCP 服务。实现后的干净环境安装必须能证明 Console 没有导入或复制
`service-manager-agent` 的业务代码；平台和 Codex Runtime 依赖仍然属于完整运行链的一部分。

静态资源以 Python package 内的 `src/codex_agent/console/static/` 为运行事实源，检查并整理外层
`console/static/index.html`，避免维护两份不同页面。参考仓库只读借鉴，不引入其 Redis/task-manager/
upstream mirror/tunnel 实现，也不把业务专有字段复制进 Galatea。

## 2. 拟定配置与升级

Console 配置示例（待实施，不可直接给当前解析器使用）：

```json
{
  "console": {
    "host": "0.0.0.0",
    "port": 18880,
    "storage": {
      "backend": "memory",
      "retention": "process_lifetime"
    }
  }
}
```

`serve --host/--port` 可保留为显式覆盖，优先级为 CLI > 配置 > 上述默认值。
`storage.retention` 首版只接受 `process_lifetime`，不提供未实现的 TTL/max-events 开关。
backend 首版只接受 memory，后续 Redis 实现到位再开放。

由于 Q3 取消应用层鉴权，配置为非回环地址时的防火墙/网络 ACL 是部署前置条件；unit 或
启动脚本不应把“监听成功”解释为“可安全暴露公网”。没有可信内网边界的环境不能直接采用
无鉴权配置，应另行保留认证或只绑定回环地址。

升级涉及以下已有字段和入口：

- 删除 Console `token_file`、配置化 `allowed_origins`、`--token-file` 及前端 Token 代码；
  不删除模型 API key 文件和平台凭据。若实现保留同源 CSRF/Fetch Metadata 检查，它是无身份
  的请求来源保护，不恢复用户鉴权，也不依赖旧的可配置 Origin allowlist。
- `paths.event_dir` 不再是必填项；新配置移除该字段，删除 AgentConfig、release 路径检查、
  unit 写目录白名单和测试中对它的依赖。旧配置字段给出明确迁移错误，不能保留一个悄悄落盘的旧通道。
- `state_dir`、workspace、CODEX_HOME、平台 state_root 的原有路径和校验继续存在。
- unit 生成器去除 token 参数并纳入 host/port 配置；不在本方案阶段安装 unit、开放端口或重启服务。
- 配置和应用代码改变后，既有 release 的代码/配置摘要会变化；按现有发布流程更新接纳证据，
  不能用移除 Console 鉴权顺带绕过 `verify_release()`。

新 Console 首次启动生成新 epoch，不导入旧事件 JSONL，也不删除旧文件。
第一次恢复持久化 session 仍走现有 Host 核对路径。已有幂等记录返回以前的结果时，
只标明该结果来自持久化控制记录，不能补造上次进程中的用户正文或全部 Codex 事件。

## 3. 实施顺序

| 阶段 | 工作 | 退出条件 |
| --- | --- | --- |
| A：范围与契约 | 固定网页聊天/追踪台边界、依赖清单、能力声明和事件/游标/快照合同 | 文档、`/api/capabilities` 和实现没有宣称 Skill/新推理/模型优越性 |
| B：内存存储 | 定义 store 端口和进程内实现 | 合同测试覆盖完整保留、顺序提交、分页、幂等和 epoch |
| C：RPC 观察接线 | observer 在 initialize 前接入；双向消息镜像和关联；Host 状态观察 | 不用真实模型也能完整采集请求、回执、通知及未知事件 |
| D：展示投影 | 脱敏、对话 delta、工具/Loop 聚合、晚绑定和完整性 | 重复回放不重复文本；长内容不被静默截断 |
| E：HTTP/SSE | 新查询接口、快照/水位、重连、重启、慢客户端和无鉴权 | 快照到流之间不漏；进程重启正确提示；可信内网客户端可查询与操作 |
| F：页面与启动配置 | 对话/流程/事件视图，删除 Token，内网监听、unit/config 迁移 | 浏览器可通过服务器 IP:端口打开，现有操作可用，并显示能力边界 |
| G：回归与交付 | Host 持久化回归、模拟协议联调、性能测量、文档和可选目标 Runtime 验证 | 完成下面验收矩阵，列明真实环境、Skill 和训练结果未验证项 |

每一步都使用当前已经确认的范围；不在一期实现 Redis、不增加审批回复、不升级 Codex Runtime。
目标 Runtime 的完整协议 schema 尚未在本次读取，应在阶段 C 固定版本后导出/核验，并建立
server request/notification 覆盖夹具。未知方法通用采集不能依赖这个清单才工作。

## 4. 必须验证的场景

| 编号 | 验收场景 | 预期结果 |
| --- | --- | --- |
| E01 | initialize 到 turn 结束的双向混合序列，含通知/请求/成功和错误回执 | 每条有效 wire 消息都有唯一事件；方向与类别准确 |
| E02 | 未知 method、未知 item、无 session、错误 turn 的 request | 原生事件可查；Host 按现有协议处理，不静默丢弃观察 |
| E03 | 两个 session 交错、双向相同 RPC ID、数字/字符串 ID、ID=0 | 正确关联，不串会话、不丢 0 |
| E04 | turn 通知先于 turn/start response；thread 通知在绑定前到达 | 立即捕获；绑定后 session 快照包含旧事件并通知订阅重载 |
| E05 | write 成功后 drain 失败、EOF、RPC 超时、重复/异常回执 | 发送观察不等于执行成功；故障可见；不自动重执行 |
| E06 | 无浏览器期间持续产生事件，关闭 session 后再查看 | 全部历史仍可查询，不以订阅/关闭动作清理 |
| C01 | 可信内网浏览器创建 session，发送普通文字并等待 turn 结束 | 页面显示用户消息、完整 assistant 文本、实际终态；刷新/重连不重复追加回复 |
| C02 | 用确定性协议夹具驱动现有 Galatea 只读或受控训练工具 | 页面显示工具名、call/operation/job 身份、参数/回执脱敏结果及最近已观察到的作业状态/时间；不重复执行或直接轮询后端 |
| C03 | 查询 capabilities 和状态 | 明确返回 `training_agent_chat_trace` 角色、内存/进程生命周期保留、本期未集成 Skill、无独立推理入口及观察健康 |
| C04 | 受信与非受信网络来源访问非回环端口 | ACL/防火墙阻断非受信来源；受信来源无需 Token 仍按 Host principal 使用既有控制合同 |
| M01 | 超过参考项目 TTL、8192 队列量和旧 1000 步展示上限的合成事件 | 服务端全部保留，无 TTL、LRU、采样或静默 drop |
| M02 | 多页历史、过滤后无匹配记录、长字段/长列表 | 游标正确推进；翻页可获取全部内容；前端折叠不截断存储 |
| M03 | 重试同一 commit 与相同 ID 不同 payload | 同内容幂等；不同内容报冲突；顺序水位无半提交 |
| M04 | snapshot 与 commit 并发，read 和 wait 注册间插入新事件 | 快照自洽，SSE 回放补齐，不丢唤醒 |
| M05 | 首版配置 backend=redis | 明确不支持，不连接 Redis、不回退假成功 |
| S01 | 可信内网新客户端无需 Token 访问页面、历史、SSE 与现有写操作 | 无 401/Token 前置要求；写幂等保持 |
| S02 | 服务器绑定非回环地址，浏览器从另一内网客户端访问 | 页面、API、SSE 均可用；无隧道依赖 |
| S03 | SSE 断开/慢读/取消、两客户端同时订阅 | 仅该连接受影响；重连去重回放；采集不中断 |
| S04 | UTF-8 拆包、多 data 行、半帧、注释、连续多帧 | parser 不丢字、不丢事件，游标在处理后更新 |
| S05 | Host 服务重启，客户端带旧 epoch | 返回重置错误并显示历史清空；持久化 session 仍存在 |
| S06 | 跨站页面向 Console 写接口发起浏览器请求 | 同源来源检查拒绝该请求；可信网络内的同源页面仍可正常操作 |
| P01 | delta + item/completed、重复 SSE、工具 request/response + 原生 item | 消息/工具聚合不重复，原始事件均保留 |
| P02 | 累计 Token 快照、failed/interrupted、无终态 | 用量不重复累加；终态及未知如实显示 |
| P03 | 嵌套 JSON 伪凭据、私有 reasoning、公开摘要、HTML、超长普通正文 | 脱敏有路径与原因；公开正文完整；不执行 HTML |
| P04 | 审批/权限/提问请求 | 请求及实际回执可见，无回复按钮，不改变 Runtime 权限 |
| R01 | Host 重启恢复与未完成工具 intent | 仍使用原文件记录核对，不能因 Console 清空而重执行 |
| R02 | 重复 HTTP 请求/消息，已知回执与未知结果 | 持久化幂等行为保留，观察投影不重复用户消息 |
| R03 | 文件状态写入失败和观察失败分别注入 | 权威状态失败仍按原规则处理；观察故障明确上报，不能互相冒充 |
| R04 | 配置、CLI、unit、release 更新 | 无 Console token/event_dir 残留依赖；模型/平台凭据和发布校验仍有效 |
| R05 | 干净环境安装并启动 Console 包 | 不需要 service-manager-agent、Redis、Node 或 MCP transport；现有 Codex/Galatea/Ray/MLflow 等运行依赖按清单可解析 |

M01 使用合成协议数据验证保留，不调用模型，不需要真实训练。
性能验收记录输入速率、event_count、队列积压、RPC 延迟、历史查询延迟和进程 RSS；
完成测量前不虚构吞吐量或固定内存占用目标。

## 5. 验证与交付

实施时先运行受影响的 Console、RPC、Host 生命周期和部署配置测试，再运行 Agent 包回归。
当前机器可用 Python 路径为 `/data/miniconda3/envs/attend-ray-py312/bin/python`；
仓库旧说明中的 `/data/conda/...` 此次未找到，不应未经检查直接复制。

示例回归命令（从 Galatea 仓库根目录运行，实施后再执行）：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agents/codex-app-server/tests -p 'test_*.py'
```

浏览器测试覆盖无需登录、发送/流式显示、中断/恢复、历史筛选、完整详情和重启提示。
平台未改动时不要求以真实训练验证 Console。真实 binary 测试使用既有 mock Responses
fixture 路径；未提供可用且匹配的 Runtime 包时，明确记录该部分未验证。
不将 mock 协议通过表述为真实模型/生产服务上线通过。

交付应包含：内存适配器与协议合同、可直接访问的 Console、与用户确认一致的配置/部署说明、
依赖与代码独立性证据、浏览器聊天和工具追踪验收记录，以及后续 Redis 仅需替换的端口说明。
验收记录应单独列出模型质量、训练结果、Skill 效果和与原生 Codex 的比较均未由本期 Console
改造证明。当前本轮交付只包含这套技术方案文档，文档本身只做路径/链接、结构和一致性检查，
不运行未来实现的验收测试。
