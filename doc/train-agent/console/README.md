# 训练 Agent 网页聊天与追踪台方案

更新：2026-10-15。状态：技术方案；关键需求已经逐项确认，尚未实施代码改造。

本期交付定位为“训练 Agent 的网页聊天与追踪台”：在现有 Codex Host 上提供浏览器聊天、完整协议事件追踪和训练工具调用过程展示。
它不改造模型或 Agent Loop，不新增训练算法、模型推理服务或工具能力，也不承诺比原生 Codex 更聪明或更通用。

## 已明确的需求

- 基于当前服务扩展 Console 后台。
- 参考同级仓库 `service-manager-agent` 的 Console 实现。
- 暴露 Codex 的所有事件，不能只显示部分生命周期和工具状态。
- 本阶段不打通隧道。
- Console 数据使用内存；Host 会话、请求幂等和工具执行记录继续持久化。
- 使用适配器模式，为后续切换 Redis 存储保留明确接口。
- 保持当前 Codex Runtime、模型、工具合同和 Skill 配置；本期不启用或发布 Skill 包。
- 本次先交付技术方案；有歧义时按一问一答确认，不自行推定。

## 当前源码入口

当前 Web Agent 位于 [agents/codex-app-server](../../../agents/codex-app-server/README.md)，
已包含 Host、原生 Codex app-server 子进程、工具 Registry 和 HTTP/SSE Console。
本目录针对该现有实现展开，不以同级历史 Python Runner 文档作为当前运行入口。
组件关系见 [Codex 与 Galatea 平台架构](../../agent-galatea.md)。

## 已确认的需求决定

| 编号 | 用户确认 | 方案中的落实 |
| --- | --- | --- |
| Q1 | 仅 Console 数据使用内存，保留 Host 现有持久化 | 事件、对话/流程投影和订阅使用内存；会话、HTTP/消息幂等、工具意图/回执、进程记录与单写锁继续使用现有文件存储 |
| Q2 | 保留现有交互，新增完整事件展示 | 保留创建、发送、中断、关闭、恢复；展示审批/权限/提问事件，但不新增回复这些请求的入口 |
| Q3 | 不需要鉴权 | 移除 Console Token 登录、Bearer 校验、Console token 文件及相关启动参数 |
| Q4 | 允许通过服务器 IP:端口直接访问 | 在现有 HTTP 服务上开放内网监听，地址/端口可配置；本阶段无隧道 |
| Q5 | 当前进程存活期间全部保留，重启才清空 | 无 TTL、无数量/容量淘汰、无静默采样；查询分页、浏览器按需渲染 |

首版使用一个服务进程、一个 Host 和一个进程内观察存储。所有访问者使用该 Host 已配置的同一个
principal；移除 Console 登录不改变平台工具的项目、Campaign、预算和权限校验。
Q3/Q4 组合意味着应用本身不验证访问者身份；通过非回环地址开放前，部署环境必须用防火墙或网络 ACL
将端口限制在可信内网来源。Host principal 的平台权限是共享执行身份，不能替代 Console 访问控制。

## 本期交付定位与能力边界

| 事项 | 本期承诺 |
| --- | --- |
| 网页聊天 | 复用现有 Host 和已配置 Codex Runtime/模型，支持文本会话、流式回复、中断、关闭和恢复 |
| 训练过程追踪 | 展示 Host 与 app-server 实际交换的协议事件、Agent Loop、现有 Galatea 工具调用及回执；作业状态以已观察到的工具回执为准，完整性和脱敏状态明确可见 |
| 工具能力 | 沿用当前版本化工具合同及 Host principal 的 action/project/Campaign 范围，不新增、放宽或重命名工具 |
| Skill | 保持当前 Runtime 配置。本仓库示例配置关闭 Skills，本期不安装、启用或注入 Skill，也不声称训练 Skill 已接入 |
| 模型训练/推理 | Console 可呈现现有受治理训练工具所触发的作业与结果；不实现训练算法、目标模型的在线推理入口或新工作流。已有 `evaluate` 作业是否执行批量推理由当前项目合同决定 |
| 与原生 Codex 的比较 | 本期只提供 Galatea 工具和执行轨迹的专用入口；不作能力或效果优于原生 Codex 的结论，也不覆盖原生 Codex 的通用交互能力 |

本期成功标准是用户能在浏览器完成现有 Host 支持的文字对话，并看清训练工具何时被调用、传入和返回了什么可公开数据、作业最近一次已观察到的状态。
模型质量、Skill 效果和训练结果不属于 Console 改造的验收结论。

## 依赖与代码独立性

Console UI 作为当前 Python ASGI Host 的静态资源和路由实现；本期不增加独立 Console 服务、Node 构建链、Redis 或隧道。
完整运行链仍依赖受控的 Codex Runtime 包和模型服务、Galatea Python 包、Ray、MLflow、对象存储、Python/系统运行环境及其配置凭据。
当前 `galatea-mcp` 发行包的依赖图包含 MCP Python SDK，但新 Host 通过进程内 Registry 适配业务，不启动 MCP Server transport。
Codex Runtime 沿用当前受控的原版二进制/资源复制与发布核验，Console 不复制或改写它的 Agent Loop；
“独立”不表示不依赖这份 Runtime。

`service-manager-agent` 只作为交互和旁路观察的设计参考，不作为运行时依赖；实现应在 Galatea 包内独立编写，不导入或复制其业务模块。
代码独立性最终由干净构建/安装验证确认，不能仅凭技术方案宣称已验证。

## 审核结论

| 审核问题 | 本期结论 |
| --- | --- |
| 代码是否可独立实现 | Console 页面、HTTP/SSE、协议观察和内存投影可以在当前 Galatea Agent 包内独立实现；不复制 `service-manager-agent` 的业务代码，也不新增独立 Console 服务。当前方案尚未完成构建/安装证明。 |
| 方案是否可行 | 网页聊天、全量协议追踪和现有工具调用展示在现有 Host 边界内可行；前提是按目标 Runtime 的真实 schema 完成 observer 接线、脱敏、内存容量测量和浏览器验收。当前文档不是已上线证据。 |
| 是否只依赖 `codex-app-server` | Console 观察层不新增 Codex 之外的运行时组件；完整训练 Agent 仍必须依赖现有 Galatea ToolRegistry/业务包、配置的模型服务及训练工具所需的 Ray、MLflow、对象存储和系统凭据。若要求“只依赖 app-server”，就必须同时取消现有训练工具和作业追踪，不能保持本期目标。 |
| 是否完全不依赖外部组件 | “不新增外部组件”可作为本期目标；“完整系统不依赖任何外部组件”不可行，也不是当前方案的承诺。已有依赖继续由现有部署管理。 |
| 最终效果 | 在打开的 Console 中完成文字聊天，查看 assistant 回复、协议事件、工具调用和训练作业状态；相对原生 Codex 的可验证改善是 Galatea 工具适配和执行可追踪性，不推出模型质量或通用 Agent 能力更好的结论。 |

后续若要让 Skill 参与训练 Agent、提供新的模型推理入口，或比较原生 Codex 与 Galatea Agent
的任务效果，应分别立项并冻结 Runtime/Skill 版本、工具权限、任务集、成本和成功指标；这些工作
不以本期聊天与追踪台上线为完成证据。

## 方案概览

在现有 `JsonRpcPeer` 收发边界增加观察入口，完整接收双向 request、response、notification 和
error。统一关联 thread/turn/item/call，再写入 `ConsoleObservationStore` 端口的内存适配器。
Console 提供对话、Agent Loop、原生事件详情和运行实例事件，支持历史分页与 SSE 断线续读。

“全部事件”指当前 Host 与其托管 Codex app-server 之间实际收发的协议消息，不以 method 白名单
丢弃事件；未知方法和无 session 的启动消息也可查看。字段按既有凭据与私有内容边界脱敏，
脱敏项有明确标记；完整事件类型覆盖不等于暴露 Runtime 未发送的内部状态。

现有 `EventLog` JSONL 只承担 Console 投影/回放，不参与 Host 的恢复判定，属于本次需要迁移到
内存的观察数据。`AtomicJsonStore`、`OperationJournal` 和 Codex 自身的会话存储继续保留。
内存历史不从旧 JSONL 或 Codex 内部文件自动补造；进程重启后显示历史已重置。

后续 Redis 仅替换观察存储适配器，保留事件和 HTTP 契约。Redis 不自动带来 Host 多实例接管，
也不承载 socket、Future 或 Runtime 控制权。

## 文档导航

| 文档 | 状态与内容 |
| --- | --- |
| [01 当前实现与本期边界](01-current-state.md) | 已核对源码；记录工具/Skill 现状、依赖和代码独立性边界 |
| [02 架构与职责](02-architecture.md) | 当前服务扩展方式、状态归属、组件接线、生命周期和访问方式 |
| [03 事件协议与展示](03-events.md) | 全量采集、消息关联、完整性、脱敏、对话与 Loop 投影 |
| [04 存储适配器与 Redis 演进](04-storage-adapters.md) | 内存实现、接口合同、无淘汰保留、游标、快照和 Redis 切换 |
| [05 网页聊天、HTTP、SSE 与追踪台](05-http-and-ui.md) | API、断线续读、重启提示、现有操作、无鉴权访问 |
| [06 实施与验收](06-implementation-and-acceptance.md) | 文件级改造、配置迁移、实施次序、测试矩阵及交付标准 |

各文件中的新增模块、接口和配置均为拟实施设计，不代表已有能力。当前只交付本文档集，
没有修改运行代码、启动服务、配置网络或提交训练任务。
