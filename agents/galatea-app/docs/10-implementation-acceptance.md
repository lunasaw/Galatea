# 实施与验收记录

日期：2026-10-19（Asia/Shanghai）。当前交互设计基线 `galatea-app/design-v1.2`。
实现起点为 `56f324e31afdad16c9f9536c6cf97a9c1efc4439`；发布代码以包含本记录的 Git 提交为准。
下文分阶段保留验收历史，后续交互修订和主入口接入记录覆盖早期界面、部署描述。

## 实现范围

- `src/galatea_app`：SQLite WAL 事务、Task/Stage/Attempt/不可变计划、CAS、幂等收据、验证门槛、受限产物快照、Outbox/Inbox 与 fencing。
- Ed25519 Host 证明绑定真实主体、会话、工具、参数和确认；MCP App 与 Agent Provider 写权限分离；默认拒绝无证明调用。
- 项目目录初始化、原生 Host 会话激活、App 重挂载；原输入工作区与输出项目目录分别保存。
- 后台观察、接续、取消、旧备份执行隔离；暂停后使用新绑定；未知提交只对账原身份；失败投递最多五次后进入可查询 dead letter。
- 聊天驱动的 MCP App：自动展示动态阶段/过程卡、真实文件快照 diff、预算调整/延后/确认、指标、产物与日志、窄屏和暗色；早期发起页仅保留在历史验证说明中。
- 两条显式训练适配器：固定项目 Controller，以及现有 Campaign MCP。预授权匹配完整不可变输入与预算；次数/重试限制、外部 wall-time 截止触发治理停止并继续观察。
- `.codex/skills/galatea-task-workflow` 与外置 Provider。Host 未添加训练业务分支，也未新增模型循环。

## 分层验证

以下结果互不替代；测试日志位于工作区外 `../platform-data/galatea-app/`。

| 层次 | 本次结果 | 实际证明 |
| --- | --- | --- |
| App Python | 67 项通过，包含 1 项真实 Codex 协议测试 | 真实 SQLite、状态机、证明、恢复、预算、产物边界；模型响应受控 |
| Host Python | 229 项运行通过，18 项环境测试跳过 | 既有 Host 回归 + 通用证明、接续和导出；跳过项不计入真实联调 |
| Host frontend | 123 项通过；类型检查与构建通过 | 既有 UI 行为回归 |
| MCP App browser | 真实 Chromium + AppBridge，12 项通过 | diff、三个详情标签、产物预览、850/580/390 px、暗色、无横向溢出 |
| 签名启动 browser | 8 项通过 | 真实签名、Host 确认、拒绝、激活、SQLite、重挂载与重载；模型为 synthetic |
| 既有 Host MCP browser | 通过 | iframe 隔离、工具确认、上下文、发送消息、下载、全屏/固定、窄屏、关闭重开 |
| 七场景 fixture | 通过，七张截图 | 数据/代码/方案/训练/结果/两阶段/重规划；页面明确标注模拟 |
| 容量探针 | 100 Task、10 并发视图、2,000 次读取通过 | 本机空任务 SQLite 读取；约 15k reads/s，非训练吞吐或生产 SLA |
| 打包 | wheel 构建通过 | `--no-build-isolation` 使用现有构建依赖；默认镜像获取 setuptools 失败，不能宣称离线安装已覆盖 |
| systemd | 替换示例可执行路径后 verify 通过 | 部署模板语法；未将模板安装为生产服务 |

TDD 采用先写失败验收、实现、回归的纵向切片。新增失败用例验证了：过期租约、未知提交对账、取消竞态、暂停后新绑定、恢复备份禁止执行、预算超次数、wall-time 到期、外部产物跨操作引用、消息翻页边界及预览脱敏。真实模型联调进一步发现并修复数组元素缺类型和产物 `sha256` 字段缺失。

复跑（仓库根目录）：

```bash
export PYTHONPATH=agents/galatea-app/src:agents/codex-app-server/src:agents/galatea-app/tests
export CODEX_TEST_RUNTIME_PACKAGE="$PWD/agents/codex-app-server/.local/npm-0.163.0/node_modules/@openai/codex/vendor/x86_64-unknown-linux-musl"
python -m unittest discover -s agents/galatea-app/tests -p 'test_*.py'
PYTHONPATH=agents/codex-app-server/src python -m unittest discover -s agents/codex-app-server/tests -p 'test_*.py'
npm --prefix agents/galatea-app/frontend ci
npm --prefix agents/galatea-app/frontend run build
npm --prefix agents/codex-app-server/frontend test
npm --prefix agents/codex-app-server/frontend run build
python agents/galatea-app/scripts/generate_contracts.py
python agents/galatea-app/scripts/capacity_probe.py
```

浏览器 fixture 启动方式见 [运行说明](../README.md)，签名 fixture 见 `tests/signed_browser_fixture.py`；分别运行 `workbench_browser.cjs`、`scenes_browser.cjs` 和 `signed_browser.cjs`。生产 Host、真实模型和训练需要各自已授权配置，不能用 fixture 结果代替。

## 猫狗真实 smoke 与展示

实际执行固定 `train-model/ray-cats-and-dogs/job/governed.mjs`，发布不可变 Release 后执行 1 Epoch smoke。未执行 baseline、champion、最终 test 或 Registry 推广。

| 身份/证据 | 本次事实 |
| --- | --- |
| Release | `a6fe1242405e188b9976` |
| 固定入口 attempt | `app-tdd-20261019-reconciled` |
| profile | `dev-app-acceptance-20261019` |
| Ray Job | `ray-cats-and-dogs-smoke-afa0e9825328`，`SUCCEEDED` |
| MLflow Run | `2132f17b556144599a0ccb08a7a0611a`，`FINISHED`，`run.outcome=succeeded` |
| 实际训练 | `training_started=true`，1 Epoch，22,497 train / 1,250 validation；test 未评估 |
| 验证指标 | `val_accuracy=0.54`，`val_loss=0.6855554707527161`；仅说明 smoke 链路，不是效果达标 |
| 检查点 | `checkpoints/best/best-model.pt`，3,650,483 bytes |
| 检查点 SHA-256 | `647eb549de2a03a50ffabeed065dae3e5f7d015989e357d3a5e4164ebf4647c9` |
| 独立验证 | MLflow Artifact API 下载，新进程加载 + 有限值前向通过；无参数更新 |
| App 展示 Task | `task_e2b67604e76047c6a5e98b5fa044c384`，三阶段证据核验完成 |

真实证据展示：[本机只读工作台](http://127.0.0.1:18888)。状态目录是
`/data/ai/chenzhangyue/galatea-model/platform-data/galatea-app/real-evidence/`，含 `acceptance.json`、`tasks.sqlite3`、`viewer.json` 和检查点回读记录。
截图位于同级 `screenshots-real/`；七场景截图位于 `screenshots-scenes/`。

**这个训练由固定项目入口执行，随后由 `scripts/reproduce.py` 只读导入工作台。它证明真实 Ray/MLflow/Artifact 适配和展示，不能冒充由 App 预算按钮自主发起并完成训练。** 源码 diff 明确标记为来源快照，不是 Agent 本次编写的改动。

平台故障记录：原 dev 的 Ray runtime-env-agent 启动超时，raylet 退出而 API 留有 RUNNING 记录；重启对应 dev unit 后故障复现。故障材料保存在 `incident-20261019/`。随后建立独立验收 Ray profile（GCS `127.0.0.1:19540`，API `http://127.0.0.1:19541`，GPU 7），仍经固定治理入口运行。
首次隔离提交虽然 Job 成功，但 `training_started=false`（旧 Run 锁），没有算作训练成功；只对本次失效 Run 经 MLflow API 做失败对账后，最终 smoke 才实际训练成功。共享 dev 故障没有被宣称已修复；没有清空共享 Redis 或执行全局 Ray stop。

## 真实模型链路与当前边界

独立验收 Host 使用原有 `gpt-6-astra` 配置与 Codex CLI `0.163.0`，地址 `http://127.0.0.1:18891`，App 地址 `127.0.0.1:18886`。工作台的可信确认实际创建项目与原生会话，并在浏览器关闭后通过 continuation 触发真实 Codex。
真实会话：`app-activation_b120c1224e07668f9f5a765d71e2d0cf`；Task：`task_30bd0fbea28b4e048e8a8240b94743dd`。

**真实任务最终 completed**：真实 Codex 使用本地工具检查 6 行 CSV，去除 2 行完全重复记录，输出 4 行 `outputs/clean.csv`、`outputs/quality.json` 和 `outputs/inspection.json`。两个阶段和三个真实产物均经服务验证，最终由真实 Codex 调用 `complete_task` 封存。没有训练占位阶段、Campaign 或训练 Run。证据是 `live/acceptance.json` 和 `live-completed.png`。
验证过程中人工尝试一次同名最终封存时，Codex 已先行完成；该请求以幂等冲突拒绝，没有改写结果。
[打开真实任务](http://127.0.0.1:18891/?session=app-activation_b120c1224e07668f9f5a765d71e2d0cf)。

当前验收必须保留以下界限，不能将 M0–M5 全部标成生产验收完成：

- 模型工具发现受部署影响：本次模型的 `tool_search` 调用不兼容固定 runtime；使用聊天 `/mcp` 的既有入口打开 App，并在独立配置关闭 defer/search 功能后，Task Provider 可调用。
- 本次真实任务经历了人工诊断、同 command_id 的计划对账恢复以及对模型重复查询原生 Goal 的纠偏，不能描述成从未干预的一键闭环。恢复没有重建 Task 或提交训练。
- 默认只提供最近 100 条消息快照并标识更早记录，更新接口可分页；跨重新挂载的历史选择恢复、所有断网/滚动交互尚未全部自动验收。
- 原生事件缺口可见；跨 epoch 完整自动补拉与长期保留清理、告警外送、组织认证代理/密钥轮换还需要部署验收。当前不自动清理审计记录，避免破坏恢复窗口。
- Campaign MCP 路线有契约/传输测试，未在本次创建真实 Campaign；生产归属与恢复权限仍由管理员配置。
- 本次只运行受治理 1 Epoch smoke，并读取已有证据；完整猫狗效果复现实验、最终 test 和推广不在本次执行结果中。

生产发布前，应按 09 章矩阵继续完成这些尚未覆盖的验收并登记正式部署 profile。上述限制不影响只读复现页面与已验证的领域/Host 能力使用。

## 2026-10-19 右侧 Apps 标签与入口修正

用户报告的 `session-5a1a7bbfd92a44134e83a6e59245c37ccb20f71f1dd9a1488430eff382a5f538`
属于 18891。原始消息只发送了清理 CSV 的普通聊天指令；没有 `open_workbench` 调用，也没有
project activation。模型读取了既有项目产物，且关联任务访问被拒，这不是本次 App 任务验收。

新增右上角「打开 Apps」及右侧 Apps 标签；`/apps` 使用同一入口。无会话时自动创建并选中会话。
打开应用后隐藏目录、突出工作台；桌面切换标签或收起面板保留同一 iframe 与表单。
项目 activation 在独立项目会话右侧自动展开，刷新后恢复，不再插入聊天正文上方。
App-only 内部工具不再被列成启动卡片；示例按钮使用中性的「填入示例」，避免把数据清理误称为猫狗训练。

先新增浏览器测试，观察旧版本缺少「打开 Apps」按钮失败，再实现并验证：

- 前端 124 项通过，TypeScript 与 Host/App 构建通过。
- `test_app_sidebar_browser.cjs`：直接入口、自动选中会话、标签/收起保留 iframe、激活跳转、刷新与窄屏通过。
- `test_apps_launch_browser.cjs`、`test_conversation_panels.cjs` 与既有 MCP App 桥浏览器回归通过。
- 签名浏览器 8 项通过：真实 Host/AppBridge/签名/确认/SQLite，模型仍为受控响应。
- 真实 18891 中打开上述用户会话并读取 Galatea 发起页、填入示例、切换与收起恢复通过；未提交新任务。
- 已完成项目会话刷新后右侧工作台恢复通过。截图在运行数据目录 `screenshots-sidebar/`。

前端资源已更新，保留旧哈希资源供未刷新客户端使用；18880 的 Galatea 后端配置没有改动。
本次修正不增加新的训练验收，也不追认普通聊天中的旧产物为本次任务结果。


## 2026-10-19 会话驱动的 Agent 自主展示

本节替代上一节“先手动打开 App、再发起项目”的新任务操作流程。用户新建普通会话并描述
需求，Agent 自主选择任务 Skill，调用 `get_current_task` / `start_task`。任务绑定原 Host
会话与线程；Provider 成功回执携带受限 `app_view`，Host 自动显示右侧 MCP App。
这是一项通用 Provider 展示扩展，同时保留标准 MCP UI 元数据触发机制。没有关键词触发器
或第二个模型循环。原生运行时本次无法可靠向模型暴露 `open_workbench`，成功回执机制
消除了额外发现打开工具的依赖。

App 删除发起表单、项目列表、重复对话与输入框，保留目标、阶段、进度、指标、产物、
日志和必要的训练确认。准备工作由当前原生回合执行；训练仍须精确方案确认与治理授权。
当前会话查询不再误用其他会话的历史任务；异常约束返回明确失败并事务回滚。
快照独立保留最后有效 App 调用，超出历史分页后仍可恢复，并遵循水位、回滚和会话隔离。

验证：

- Host 前端 128 项通过，TypeScript 与 Host/App 构建通过。
- App 服务 74 项中 73 通过、1 项原生运行时测试默认跳过；含当前回合执行、会话隔离、
  幂等、约束拒绝、Provider 展示与训练精确确认边界。
- Host 观察存储/投影 31 项及会话回放 5 项通过，覆盖分页后 App 恢复、回滚与隔离。
- 通用自主展示浏览器用例通过：自动展开、同会话、无重复 iframe、切换/收起保留、刷新、窄屏。
- 签名链路浏览器 12 项通过：真实 Host/AppBridge/签名/Task Service，受控模型输出，
  同会话自动展示、实时完成、真实文件验证与预览、越过历史分页后刷新恢复。
- App 浏览器 12 项及七种场景通过，包括无发起表单/项目导航/第二个聊天框。

18891 真实模型使用新建会话
`session-9177b18cca7180e4241bd4e9d9d17b0f6b5d2d0f015fcfd885d78b6fb6fb6a5d`，
只发送检查 `conversation-check.csv` 的普通需求，未提 App、Skill 或工具名。
真实 Codex 自主建立 `task_ad12003a1f384582a927ab4bc4dda585`，原会话右侧自动显示，
随后检查四行 CSV 并交付 `quality_metrics.json` 与 `quality_report.md`，两个阶段均已验证，
真实 Agent 自主封存为 `completed` 并在原聊天返回最终报告。
输入有一条缺失值和一条多余的整行重复记录；源文件未修改，没有运行训练。
验证证据位于运行数据目录 `live/conversation-*.json` 和 `screenshots-conversation/`。
最终 Host 更新后，按原协议对本次空闲验收会话执行 `resume`，没有新增模型回合；
真实浏览器验证分页后自动恢复 App、显示 completed、预览报告与标签切换保留 iframe 均通过。
此前调试使用的失败/中断会话保留审计，不计入成功链路；它们没有执行训练。

18880 的 Galatea 后端配置没有改动。历史训练验收不属于本次交互修订的新增验证。


## 主入口 18880 接入验收（2026-10-19）

本节更新前述“18880 后端配置没有改动”的历史描述。主入口已接入聊天驱动任务 App，
Host 使用 18880，独立 App 后端使用回环端口 18885；18891/18886 验收环境保持独立。
主入口原有 10 个会话已保留，未把验收环境的任务库和凭据混入主入口。

真实模型在主入口的新聊天中完成普通 CSV 检查：自动建立任务并展开 App、更新进度、
交付经校验的报告，全程未手动打开 App、未切换会话。结果为 4 行、2 列、1 个缺失值、
1 行多余重复；源 CSV 摘要保持一致，本次未执行训练。刷新后可恢复同一任务。
同时修复并验证可信网关对子工作目录的匹配：解析真实路径、选择最具体的已授权根，
拒绝通过符号链接逃逸工作区。

运行备份、凭据、部署审计、真实浏览器结果和完整截图存于 Git 外
`platform-data/galatea-app/production-18880/`；不作为源码分发。
当前服务沿用独立后台进程启动方式。主机 systemd manager 连接失败，
未完成开机自动启动配置，不能把单元模板验证当作该能力已部署。

提交前复核（无训练）：

| 检查 | 结果 |
| --- | --- |
| App Python | 76 项，75 通过、1 项可选原生运行时测试跳过 |
| Host frontend | 128 项通过；TypeScript 类型检查通过 |
| Host 定向 Python 回归 | 23 项通过，覆盖观察存储、后台接续、事件导出、MCP 上下文和进程清理 |
| App TypeScript | 类型检查通过 |
| 同聊天签名浏览器检查 | 12 项通过，含自动展开、实时进度、核验产物和分页后刷新恢复；模型响应受控 |
| 新项目配置与治理边界 | 14 项只读测试通过：配置、错误集群输入拒绝和 Notebook 无执行状态 |

[产品介绍](00-product-overview.md) 中的面板截图来自上述受控浏览器检查，
与 Git 外保存的真实模型主入口证据分别标识。
