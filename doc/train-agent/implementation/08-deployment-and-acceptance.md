# 方案 08：独立部署与验收实施计划

> 后续实现使用 superpowers:executing-plans；部署和算力执行限定在已有授权范围。

**目标：**验证四个独立组件协作，不改变既有 plugin 能力和原版 Codex。
**架构：**现有平台 + 一个 Python MCP 进程；独立 Python Runner 监督原版 Codex；Skill 独立发布。
**技术栈：**专用 Python 环境、原版 Runtime、systemd/容器、受保护本地文件卷、HTTPS；无新增数据库。
**共同约束：**遵守 [实施索引](README.md)，不以旧插件切换作为上线步骤。

2026-09-07：本地实现和独立安装结果见[验证记录](../09-local-validation.md)，
实际部署及故障恢复命令见[端到端手册](../08-e2e-integration-runbook.md)。
以下 P0–P4 含真实环境步骤，不能因本地替身测试通过而全部勾选。

## 1. 拟交付文件

| 位置 | 内容 |
| --- | --- |
| MCP 包 deploy/inventory.example.yaml | 非秘密版本、路径、端口、账号/密钥引用 |
| Galatea/systemd/galatea-mcp.service | 独立 MCP 与后台对账模板，无 Agent 依赖 |
| Runner 仓库 deploy/training-agent.service | 单 Runner、worker 监督和文件状态 |
| Runner 仓库 deploy/runtime-lock.json | Python/SDK/匹配 Runtime/Skill/Schema 的版本与摘要 |
| 各组件 tests/ | Python 契约、进程和故障测试 |
| 私有 acceptance/<release-id>/ | expected/observed/pass、版本、时间和脱敏引用 |

不新增 dispatcher/watchdog 微服务、不创建 SQL migrations；超时检查属于 MCP 后台模块，
训练自身 deadline 独立执行。未来有规模需求再拆部署，不先为一个串行流程设计集群控制平台。

## 2. 版本与身份准入

记录 Python、openai-codex、匹配 CLI Runtime、MCP SDK、Ray/MLflow/S3 客户端版本与依赖锁，
原版二进制摘要、Skill/协议/config 摘要、workload/evaluator Release。
本地 Codex 源码 `459a79e` 只用于设计，不替代实际安装包的版本/接口检查。

先 fake 验证 SDK 的 start/turn/resume/SkillInput/output_schema，再验证真实 MCP 握手和只读 API。
生产模型调用与训练 smoke 单独明确预算；不为文档审核安装依赖或变更远端服务。
工具 capability 必须反映真正支持的配置、恢复、评价隔离和预算模式。

专用账号分别拥有 MCP 状态卷和 Runner 工作区/会话卷；实际密钥用受保护配置/secret mount。
SDK 的 env 合并行为要求 supervisor 先清理继承环境；不把运行账号的整个个人配置当服务默认值。
跨主机恢复必须搬迁受保护会话和状态并确认旧执行组停止，首版不做自动多实例接管。

## 3. 验收矩阵

| ID | 场景 | 预期 |
| --- | --- | --- |
| A01 | 无 Codex/Harness/plugins 的 MCP 环境 | Python 服务和 fake 后端契约正常 |
| A02 | Agent 主机无 Galatea checkout | 仅发布 Skill、协议和 MCP 即可决策 |
| A03 | 普通 MCP client 替代 Codex | 同权限、幂等和证据结果 |
| A04 | 相同 Plan 换请求键、相同 step 换 Thread | 只有一个 operation/Job |
| A05 | 接受后丢响应、MCP/Runner 重启 | 复用原 ID 对账，不明时停止自动重发 |
| A06 | 第二写进程/Runner | 文件锁拒绝，不能接管仍活跃执行组 |
| A07 | 状态写入中断/损坏 | 保留旧快照或显式失败，不能重置空任务 |
| A08 | 等待多次 tick | 无模型调用增加 |
| A09 | SDK failed/interrupted/空输出 | 不判成功，平台副作用仍被找回 |
| A10 | 环境白名单与孤儿 worker | 后端秘密不可见，未清理不启动新 Turn |
| A11 | 取消/超时 | 先阻止新提交，再停止/对账；关闭 Codex 不假装停止 Ray |
| A12 | 预算不足/未知用量 | 不扩大范围；保持观察能力 |
| A13 | Trial 读取测试、重复最终测试 | 真实权限拒绝，marker 保留 |
| A14 | Ray success、质量失败或 Artifact 缺失 | 不 accepted，完整性失败不可交付 |
| A15 | Artifact 干净环境加载 | 摘要、接口和合成推理符合合同 |
| A16 | 旧插件目录与行为 | 内容/配置/依赖/Release 不变，无切换步骤 |
| A17 | 回退新 MCP/Runtime | 原任务引用与测试使用标记保留，不重发全部 Job |

首次真实测试：一个固定 workload、一个 Trial、明确 CPU/GPU/时长，关闭自动搜索。
正常训练不会因为 Runner 关闭而停止；测试故障只作用于专用测试账号与任务。

## 4. 任务和发布顺序

- [ ] P0：锁定版本，建立临时目录/fake 后端，验证 A01–A03/A09/A10。
- [ ] P1：部署单 Python MCP 与受保护文件卷，验证提交/文件故障 A04–A07。
- [ ] P2：安装独立 Skill/Runner，验证等待、预算、取消 A08/A11/A12。
- [ ] P3：接独立评价 Release，验证 A13–A15；正式运行必须有明确预算。
- [ ] P4：核对 plugin 树和旧配置/Release，无迁移操作，验证 A16；演练 A17。

各 Python 包执行自己的 `python -m unittest discover -s tests -p 'test_*.py' -v`。
Linux unit 修改后执行 `systemd-analyze verify`；workload 在项目声明环境做最小检查。
不把旧 TS 单测作为新架构唯一验收，不用昂贵真实训练替代可在 fake 完成的恢复测试。

## 5. 备份、升级和回退

备份 MCP JSON、evaluation-uses、Runner JSON、Codex 自有会话卷；MLflow/MinIO 沿用平台备份。
恢复先核对 Ray 集群身份和真实操作，不从历史缺失推导未提交，不读取 MLflow 后端数据库。
文件 Schema 改动显式转换并保留旧快照，禁止清空状态或 marker 作为回退手段。

暂停新 Turn/submit，处理旧 worker 和未知提交后切换兼容包；原 Job 继续观察。
新旧 Skill/Runtime 固定到具体 Campaign，缺旧版本就先报告恢复条件。
旧 DeepSeek 插件保持独立运行，不自动代发新架构的未确认作业，不变更其权限或入口。
