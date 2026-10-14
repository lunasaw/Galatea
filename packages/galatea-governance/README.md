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

Web Agent 统一使用 [`agents/codex-app-server`](../../agents/codex-app-server/README.md)，
其 Dynamic Tool Registry 直接调用 Python `galatea_mcp.Service`。
两者分别接入项目 YAML 契约和 Campaign 注册表；本库不是 app-server 的工具插件，
也不替代 Campaign 登记、预算或生产发布接纳。
