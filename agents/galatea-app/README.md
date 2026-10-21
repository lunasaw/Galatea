# Galatea 任务 App

**聊天作为入口，Agent 主导执行，App 按需展示阶段、进度、指标与结果。**
在原聊天描述需求，Agent 建立任务后自动展开面板；补充要求和调整方向仍在原聊天完成。
App 内没有发起表单、项目列表或第二个聊天框。

使用流程和职责边界见 **[产品介绍](docs/00-product-overview.md)**。
Codex 是唯一 Agent Runtime；Python 服务负责事务、校验、外部观察和幂等投递，不调用模型。
技术设计见 [docs/README.md](docs/README.md)，验证范围见 [验收记录](docs/10-implementation-acceptance.md)。

## 运行与测试

Python 3.11+，Node 24，Host 固定 Codex 0.163.0，MCP Apps SDK 2.0.3。
在仓库根目录执行：

```bash
git submodule update --init --recursive
python -m pip install -e agents/galatea-app -e agents/codex-app-server
npm --prefix agents/galatea-app/frontend ci
npm --prefix agents/galatea-app/frontend run build
node agents/galatea-app/scripts/build_preview.mjs
PYTHONPATH=agents/galatea-app/src:agents/codex-app-server/src:agents/galatea-app/tests \
  python -m unittest discover -s agents/galatea-app/tests -p 'test_*.py'
```

原生协议测试需设置 `CODEX_TEST_RUNTIME_PACKAGE` 为包含 `bin/codex` 的已验证 runtime 目录；
不设置时该项跳过。该测试使用真实 runtime、受控模型响应、真实 SQLite 与产物，不证明模型决策质量。

无训练的七场景（含两阶段、重规划）展示：

```bash
PYTHONPATH=agents/galatea-app/src python agents/galatea-app/scripts/fixtures.py --help
# 使用全新的目录，避免覆盖历史证据。
PYTHONPATH=agents/galatea-app/src python agents/galatea-app/scripts/fixtures.py --state-dir /tmp/galatea-fixtures
PYTHONPATH=agents/galatea-app/src python -m galatea_app.preview --state-dir /tmp/galatea-fixtures --port 18887
node agents/galatea-app/tests/workbench_browser.cjs
```

页面明确标识模拟数据，所有执行操作禁用。独立预览只读，真实操作必须经过 Host。

## 可信 Host 部署

1. 用 `scripts/generate_keys.py CREDENTIAL_DIR` 创建 Ed25519 密钥及四类服务凭据，文件 0600。
   Host 私钥和配置目录必须位于 Agent 无写权限的范围；MCP、Provider、接续分别使用凭据。
2. 按 `deploy/config.example.json` 填写 App 配置：owner/host identity、显式工作区、数据库、
   Host URL、公钥及凭据路径。`galatea-app --config CONFIG --host 127.0.0.1 --port 18886` 启动。
3. `scripts/configure_host.py --help` 为原 Host 配置生成新副本，登记外置 Provider、签名网关、
   Skill 和持久化 Console。已有 Provider 时先显式合并 catalog，脚本不会覆盖。
4. 在该 Host 独立 `codex_home/config.toml` 中登记：

   ```toml
   [features]
   goals = false

   [mcp_servers.galatea_app]
   url = "http://127.0.0.1:18886/mcp"
   bearer_token_env_var = "GALATEA_APP_MCP_TOKEN"
   ```

   通过保护的服务环境文件提供该变量，并加入 Host `codex.inherit_env`。不要把 token 写入仓库。
   Galatea 专用 Host 使用 Task 作为唯一任务状态源，关闭原生 Goal 工具，防止把 `get_goal`
   当作 Task 等待接口。已有 `[features]` 时修改原表，不能重复添加。配置变更后重启专用 Host；
   已加载线程上的 `thread/resume` 不保证更新 features，须重新加载线程后检查实际工具目录。
5. 重新构建 Host frontend 和 App 资源，以新配置重启 Host 与 App 服务，加载更新后的工具目录。
   在新会话直接描述训练或数据处理需求。Agent 通过 `get_current_task` / `start_task` 绑定当前会话，
   成功回执携带当前任务的 `app_view`，Host 自动展开右侧面板；无需再调用打开工具。
   `/apps` 与 `/mcp` 保留为可选诊断入口。

App 只展示当前任务的动态阶段、进度、指标、产物、日志和训练确认，不提供项目创建表单、
项目/会话导航或第二个聊天框。用户在原聊天补充要求；不会自动创建或跳转另一会话。
桌面切换标签或收起面板保留视图；刷新从原会话工具记录和服务端快照恢复。
`start_task` 允许在当前原生回合做已请求的准备工作，不排队第二个 Agent，也不授予训练权限。
真实训练仍需精确方案确认和治理授权。具备管理员委派权限的用户可先点击
“授权本次方案”，通过 Host 确认后留下持久化授权收据；这一步不会启动训练。
再点击“确认本次执行并训练”并通过 Host 确认，才启用后台观察与接续机制。
新会话只读取自身绑定的任务，不能将旧项目产物当作本次交付。
旧 `launch_project` / activation 接口保留兼容历史任务，不作为主要交互入口。
单个 Host 是单 owner 身份域；外网访问须经过认证代理。

## cats-and-dogs 真实证据

训练继续使用 `train-model/ray-cats-and-dogs/job/governed.mjs`。管理员配置
`project_governance` 的 `project_id/profile/ray_api/tracking_uri/receipts_root/project_root/grants`。
每个 grant 绑定完整 `immutable_inputs`、`hard_limits` 和 `expires_at`。
也可配置 `project_governance.user_authorization`，把有限的授权权限委派给指定
Host principal。字段为 `principal_ids`、`roles`、`allow_final_test`、
`resources`（CPU/GPU 上限）、`hard_limits`（次数/重试/秒数/内存字节上限）、
`expires_at`；参见 [委派配置示例](deploy/user-authorization.example.json)。
该配置不授予任何训练方案。用户的签名点击才产生 Task 内持久化授权，绑定精确
proposal digest、计划版本、任务/会话、用户与过期时间；修改方案必须重新授权。
Agent Provider 不暴露授权命令，普通聊天回答不伪装为 Host 签名收据。
授权和执行继续通过既有 App-only `submit_input`，分别使用 `authorize` 和 `confirm`，
不需要向通用 Host 添加训练专用逻辑。
未配置适配器、用户无权、预算越界或方案失效时，面板显示具体原因与聊天处理入口。
部署配置补齐后，既有待确认方案会重新计算按钮可用性，无须修改任务数据库状态。
移除委派权限、缩小上限或过期后，已保存授权也不能继续启动新 Attempt。

授权预算不等于资源隔离能力：配置中的内存值不能把 Ray 调度预留变成 RSS 硬限制。
执行器仍须验证可执行预算，方案必须如实展示未完成的就绪条件。
后端只有调度预留时，可以明确提交 `hard_limits.memory_bytes: null`，
同时设置 `resources.memory.enforcement: "scheduling_only"`，并在
`estimate.memory_limit_enforcement` 说明没有每个作业的内存硬上限。
这类方案仅匹配同时配置 `hard_limits.memory_bytes: null` 和
`allow_unbounded_memory: true` 的管理员权限；任何有限内存权限均拒绝，
不能静默把旧的有限预算变成无限制预算。用户仍须授权并确认新的精确方案。
App 不能创建 Release 或越过管理员配置扩充预算。
另一条路线配置 `governance_mcp`：`url` 为现有 stateless JSON MCP `/mcp`，
`credential_file` 指向 0600 bearer，另填 `project_id`、`campaign_id`、`grants`。
默认 `recovery_allowed=false` 只读取 operation；启用恢复式观察需要管理员显式授权。
取消整个 Campaign 还要求 `exclusive_campaign=true`，不能停止其他 Task 的工作。
`governance_mcp` 仍使用既有 Campaign 授权；App 不在线扩展该路线的外部权限。

`prepare_external.idempotency_key` 必须作为固定入口的 attempt 参数；Worker 从精确回执对账。

已有成功受治理运行可导入只读证据展示：

```bash
PYTHONPATH=agents/galatea-app/src python agents/galatea-app/scripts/reproduce.py \
  --project train-model/ray-cats-and-dogs \
  --submission-receipt /absolute/smoke-ATTEMPT-submission.json \
  --plan-receipt /absolute/smoke-ATTEMPT-plan.json \
  --state-dir /absolute/new-evidence-directory \
  --python /absolute/project-environment/bin/python
PYTHONPATH=agents/galatea-app/src python -m galatea_app.preview \
  --state-dir /absolute/new-evidence-directory --port 18888
```

该脚本通过 Ray、MLflow Tracking/Artifact API 核验唯一 Run、训练/验证指标、报告和检查点。
检查点重新下载并在新进程进行无参数更新的前向检查。导入快照明确标记为源码快照，
不能冒充本次 Agent 编写的 diff；该展示也不能冒充由 App 发起了训练。
项目适配器需要 `.[training]` 依赖和项目自己的 PyTorch 环境。

## 恢复与运维

SQLite WAL、FULL 同步；commands、events、projection、outbox/inbox 在事务中持久化。
重复 command 返回原收据，参数变化冲突；租约以 fencing token 拒绝过期进程回写。
外部提交未知时只对账原身份，不重发。Host busy 时排队，不 steer 用户输入。
停止 Agent 与停止训练是不同操作；后者走固定项目 Controller 并等待外部终态。

`galatea-app --config CONFIG --backup DESTINATION` 用 SQLite backup API 导出一致备份。
恢复副本默认禁止业务写入、验证、项目激活和 Outbox，完成外部执行和 Host 原生日志对账前不要解除 `recovery_mode`。
`/health/live` 为进程检查，`/health/ready` 为配置检查。部署单元是模板，路径、用户和可写目录
必须按实际机器填写；不要把 `/opt` 示例直接安装到当前机器。

当前限制、未覆盖验收和平台故障均列在验收记录，不能以 fixture 或单测替代真实联调。
