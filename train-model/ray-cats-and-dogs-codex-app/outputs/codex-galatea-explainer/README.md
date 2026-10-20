# 当前 Codex 与 Galatea 的关系

核对日期：2026-10-19。依据当前工作区源码与验收记录；未重新运行部署健康检查或训练。

打开 [浅色交互讲解页](index-light.html) 或 [深色交互讲解页](index.html)。
HTML 自包含，无需联网或安装依赖。若工作台按源码显示 HTML，可下载后用浏览器打开。
用六个按钮或左右方向键逐步阅读，切换普通数据检查与训练场景。
“方案确认”“治理条件”开关仅演示逻辑，不调用实际工具、不批准真实预算。

## 各自负责什么

| 组件 | 职责 |
| --- | --- |
| 原生 `codex app-server` | 唯一 Agent Runtime：模型调用、Agent Loop、Thread/Turn、Skill、内建工具、沙箱与原生审批 |
| `agents/codex-app-server/` | 仓库自己的通用 Python Host 和网页工作台：托管原生进程、JSON-RPC、HTTP/SSE、事件、会话与通用恢复 |
| `agents/galatea-app/` | Task Service 保存任务、阶段和证据；App 展示；Worker 验证、观察外部操作并请求原会话接续 |
| `packages/galatea-governance/` | 项目 YAML 固定入口使用的独立训练治理库 |
| `services/galatea-mcp/` | 另一条独立的 Campaign 治理入口，维护自己的注册、预算、授权与证据 |
| Ray / MLflow / MinIO | 执行计算 / 记录实验和模型证据 / 持久化对象 |

“Galatea”在这里是平台能力的集合，并非单个 Host 内置插件。
Host 按通用契约加载外置 Provider、连接 MCP、展示 App；Codex 始终负责模型决策。

## 一次请求怎样流转

1. 用户在网页的原聊天中提出需求，Host 将请求交给原生 Codex。
2. Codex 选择 Skill、原生工具、MCP 或 Provider，并决定下一步行动。
3. 对需要结构化任务展示的需求，`galatea_task.start_task` 创建当前会话绑定的任务。
   Provider 返回 `app_view`，Host 自动打开任务 App。
4. Codex 按实际需要安排动态阶段。普通数据检查可使用本地工具；训练须确认具体方案，
   并满足对应治理入口的 Release、数据、readiness、授权与预算要求。
5. 当前猫狗项目通过 `job/governed.mjs` 和治理库进入固定 Ray 训练入口。
   Worker 对账和观察外部操作，必要时经 Host 请求原会话接续。
6. Codex 上报产物候选，服务核验产物与验收规则后才能封存任务；
   App 展示结构化事实，Codex 在聊天中解释结果。

以上是讲解顺序，不是产品强制的六阶段 DAG。
普通数据处理、代码准备、训练与验证可以具有不同的阶段数量。

## 三个关键边界

- **权限分层**：Codex 完全访问权限不授予数据使用、GPU 预算、最终测试或模型推广权限。
  App 中的具体方案确认与治理授权各有校验；授权操作只能使用已有委派范围。
- **状态分层**：Thread/Turn、Task/Stage/Attempt、Ray Job、MLflow Run 是不同对象。
  聊天结束、Job 成功和业务验收通过不是同一个事实。
- **恢复分层**：Host 恢复聊天与展示；外部 Worker 观察业务任务并请求接续。
  Worker 不调用模型，Host 也不把历史 `wait_external` 自动当作 Campaign 调度事件。

项目 YAML 固定入口和独立 Campaign MCP 不能互换状态、Plan、Release 或授权。
当前项目声明 `executionBackend: ray`，主目标是最大化 `val_accuracy`；
本地短时、smoke 或 non-promotable 标签都不会产生训练入口例外。

## 当前验证边界

2026-10-19 的验收记录包含：主入口 18880 普通 CSV 请求的真实模型、同会话自动展开、
阶段更新、报告交付、刷新恢复和源文件未被修改。
真实猫狗 smoke 的执行与导入展示有另一组记录；这不证明 App 发起的完整训练闭环已全部验收。

## 源码依据

- [整体架构](../../../../doc/agent-galatea.md)
- [Host 装配](../../../../agents/codex-app-server/src/codex_agent/app.py)
- [原生进程托管](../../../../agents/codex-app-server/src/codex_agent/process.py)
- [Task Provider 与 app_view](../../../../agents/galatea-app/src/galatea_app/integrations/codex_provider.py)
- [Task Service 与结果核验](../../../../agents/galatea-app/src/galatea_app/service.py)
- [外部 Worker](../../../../agents/galatea-app/src/galatea_app/workers.py)
- [当前项目契约](../../galatea.project.yaml)
- [验收记录](../../../../agents/galatea-app/docs/10-implementation-acceptance.md)

## 图形与检查

Anatomy Figure，70 个实体，705 条路径。场景状态与文案共用 [model.mjs](model.mjs)。
生成脚本是 [build.mjs](build.mjs)，默认读取仓库中已安装的 Anatomy；
也可通过 `ANATOMY_SKILL` 指定 Skill 根目录。

```sh
node build.mjs --audit
node build.mjs --light --audit
```

几何审计覆盖真实实体形状、支撑、遮挡顺序和滑台行程采样；明暗主题均通过。
浏览器检查包含训练演示门禁、撤销条件、普通数据路线、键盘、主题、离线资源，
以及两主题各六个停靠位置的线端检查。检查日志与截图保存在本目录。
这些检查验证讲解页，不构成生产训练或治理服务验收。
