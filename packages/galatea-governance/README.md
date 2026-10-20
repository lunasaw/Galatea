# Galatea 项目治理库

供 Codex 仓库工作流调用项目固定入口时使用的独立平台库，负责不可变 Release、
Readiness、授权证据、提交幂等、Ray Jobs 和 MLflow API 校验。它不实现 Agent、
Session、模型调用、工具注册或权限交互，运行依赖只有 `yaml`。

当前调用方为
[`ray-cats-and-dogs/job/governed.mjs`](../../train-model/ray-cats-and-dogs/job/governed.mjs)。
该入口继续要求显式集群 profile、GCS/Jobs/MLflow 地址、Attempt 和任务授权，
先计划再提交；不能通过迁移路径绕过 Release、执行绑定或训练门禁。

```bash
cd packages/galatea-governance
npm ci
npm test
npm run typecheck
npm run build
```

测试使用本地 HTTP fixture、临时文件和 fake backend，不提交真实训练。
Node 版本要求见 `package.json`；项目入口直接使用 Node 原生 TypeScript 导入。

Web Agent 统一使用 [`agents/codex-app-server`](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/README.md)，
该 Host 通过配置连接外部 MCP 或可选 Python Tool Provider，不内置本库或
`galatea_mcp.Service`。本库用于项目 YAML 固定入口；独立
[`galatea-mcp`](../../services/galatea-mcp/README.md) 使用自己的项目与 Campaign 注册表。
两条治理路径的状态、Plan、授权和 Release 契约不能直接互换。

本库不是 app-server 的工具插件，也不替代 Campaign 登记、预算或 Host 发布接纳。
Codex 原生审批与平台业务授权分别校验，当前接入和迁移边界见
[架构说明](../../doc/agent-galatea.md)。
