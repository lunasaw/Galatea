# 文档导航

当前架构为通用 Codex Host，配置版本 `codex-agent/v2`。本目录描述完整重构后的实现，旧业务适配模式已删除。Codex 是唯一 Agent Runtime；整仓集成边界见 [Codex 与 Galatea 架构](../../../doc/agent-galatea.md)。

- [架构与两阶段边界](01-architecture.md)
- [原生协议](02-app-server-protocol.md)
- [Tool Provider 契约](03-tool-registry.md)
- [会话和恢复](04-agent-loop-state.md)
- [网页工作台](05-console.md)
- [配置与部署](06-config-deployment.md)
- [权限和扩展信任](07-security-boundaries.md)
- [后续业务接入](08-implementation-plan.md)
- [测试验收](09-test-acceptance.md)
- [运行时证据](10-source-evidence.md)
- [接纳门禁](11-stage0-implementation.md)
- [生产状态](12-production-readiness.md)
- [重构验收范围](13-console-acceptance.md)
- [前端实现](14-assistant-ui-console.md)
- [原生能力覆盖](15-native-workspace.md)
