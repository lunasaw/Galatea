# 独立训练 Agent TDD 实施记录

日期：2026-09-06；本地整体验证更新：2026-09-07。需求依据：[总览](../README.md)及工作包 01–08。

**Goal:** 实现首版已注册 workload、预建 Release/配置的独立 MCP、Codex Runner 和 Skill 包；
交付可重复本地测试与真实环境端到端联调步骤。

**Architecture:** 平台约束由 MCP 实施，原版 Codex 执行 Skill 和 MCP 工具，Runner 只调度一次
Turn 与外部等待。三个发布目录各自打包，无相互源码 import、无旧插件依赖。

**Tech Stack:** Python 3.11+、官方 MCP SDK、Ray Jobs SDK、MLflow/S3 API、openai_codex。

## 实施裁定

- 用户已经要求按照现有设计完整开发，直接执行已给定设计，不重复请求设计批准。
- 在当前非主分支工作区开发，保留现有未跟踪文档；不提交、切换或清理用户分支。
- `services/galatea-mcp`、`agents/training-agent-runtime`、`packages/galatea-training-skills`
  是分别发布的源码根。物理同仓库不构成运行时依赖。
- 真实 SDK 模型调用、远端训练、holdout 披露、部署均不由本地测试隐式触发。
- 所有扩展（上传、动态构建、LoRA 产品化、推广）按原文首版范围明确 unsupported。

## 接口冻结

- 协议 `galatea.tools/v1`，原文 17 个工具名不变。公共契约发布于 MCP `contracts/`。
- `get_campaign.data` 包含 `campaign_id/project_id/request_revision/stage/cancelled/budget/candidate/report`。
- `list_operations.data` 为 `{items: [...], next_cursor: null|string}`。
- Operation 包含 `operation_id/submission_id/run_id/execution/quality/integrity`。
  执行状态：pending/submitting/unknown/queued/running/stopping/succeeded/failed/stopped。
- `verify_candidate.data` 含 `outcome`、`report_ref`、`evidence_refs`、`final_test_status`、`integrity`。
  只有平台登记的报告允许 Runner 完成；Agent 输出只作建议。
- Runner 通过 MCP ClientSession.call_tool 消费 envelope，不 import MCP 服务。
- Skill 包从发布契约生成工具清单，固定 digest；Runner 恢复必须验证相同 Skill/runtime 锁。

## 任务与验证顺序

每项先写失败测试、运行并记录失败，再实现、运行窄测试与相邻回归；真实外部 API 使用边界 fake。

- [x] 1. MCP 状态与政策：原子落盘/锁、作用域、冻结输入、槽位/保守预算、幂等、取消、未知提交恢复。
  文件：`services/galatea-mcp/src/galatea_mcp/{state,contracts,projects,service}.py` 与包内测试。
  命令：`PYTHONPATH=src python -m unittest discover -s tests -v`。
- [x] 2. MCP 协议、官方后端及证据：真实 MCP 握手、分页、Artifact 路径/大小/摘要、兼容性、候选、test-once。
  文件：同包 `server.py`、`backends/`、`evidence.py`、`contracts/` 与包内测试。
- [x] 3. 独立 Runner：官方 SDK 参数/事件、持久 Thread/Turn、输出/usage、环境白名单、进程组、等待/对账/取消。
  文件：`agents/training-agent-runtime`；测试不调用模型。
- [x] 4. 自包含 Skill：六个技能、契约同步、校验/可重复归档、冻结行为场景。
  文件：`packages/galatea-training-skills`；验证实际安装包内容和故障输入。
- [x] 5. Workload 接入：固定训练/隔离评价契约、Release 工具、清洁重训和 Artifact 恢复。
  训练相关代码和 forward-only/mock 测试留在独立 `train-model/` 项目内。
- [x] 6. 总体验证与交付：三包安装独立性、Runner→真实 MCP transport→fake 平台的完整生命周期，
  无新增模型调用的等待、错误/重启/最终评价冲突，根测试；部署模板和 E2E runbook。

勾选表示代码及本地测试交付，不表示目标环境部署完成。最新结果与修复见
[本地验证记录](../09-local-validation.md)，操作步骤见[端到端手册](../08-e2e-integration-runbook.md)。

## 真实环境验收边界

本地 fake 的通过不证明 Ray 集群身份、数据隔离、GPU/CPU 调度、MLflow Artifact 代理、
真实 Codex Skill 发现/认证/进程清理或 Linux 服务配置已经验收。
逐项 expected/observed/evidence/status 记录于后续端到端联调文档，未执行项保持 pending。
