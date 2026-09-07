# 独立训练 Agent：部署、恢复与端到端验收

更新：2026-09-07。适用 Python MCP、独立 Runner/Skill 和已注册 Ray workload。
本地结果见 [本地验证记录](09-local-validation.md)；本文是实际部署操作手册，
其中真实 SDK、Linux 服务、数据权限和计算验收仍为 **pending**。

## 1. 发布前准备

使用专用 Python 3.11+ 环境，命令中明确解释器路径。不要依赖进入子目录后的 shell 自动激活。
MCP、Runner 各有环境，训练节点按 workload 的环境文件安装；不更新根共享环境代替这些步骤。

发布材料：

- MCP wheel、公开 `contracts/*.json`、[配置](../../services/galatea-mcp/deploy/config.example.json)、
  [清单模板](../../services/galatea-mcp/deploy/inventory.example.yaml)。
- Runner wheel、[配置](../../agents/training-agent-runtime/deploy/config.example.json)、
  [Runtime 锁](../../agents/training-agent-runtime/deploy/runtime-lock.json)。
- 自包含 Skill 归档、归档 `MANIFEST.json`、固定官方 SDK/Runtime 制品及摘要。
- workload 的不可变代码 ZIP、config、生成的 Registry、批准 Campaign 和冻结数据引用。

实际清单保存在受保护的 `acceptance/<release-id>/`，记录 UTC 时间、操作者、源码 revision 和工作树
补丁摘要、Python/依赖版本、每个发布制品 SHA256、配置/Skill/协议摘要，以及集群和数据身份。
源码工作树有未提交变更时，Git revision 不能单独代表制品来源。

每项真实验收采用如下记录；没有 observed 和可复核引用时不能改成 passed：

```json
{
  "id": "A08",
  "environment": "isolated-live-acceptance",
  "status": "pending",
  "expected": "两个等待 tick 的 decision_seq、SDK 调用次数及 token 计数不变",
  "observed": null,
  "evidence_refs": [],
  "checked_at": null,
  "checked_by": null
}
```

## 2. 可重复的独立包安装检查

先在构建环境准备依赖 wheelhouse，包含 MCP 和 Runner `pyproject.toml` 声明的依赖及传递依赖，
保留包摘要和解析版本。然后从仓库根执行：

```bash
/path/to/python3.11 scripts/verify_train_agent_installation.py \
  --wheelhouse /secure/build/wheelhouse \
  --output /secure/build/new-installation-check
```

输出目录必须是仓库外的新目录。脚本离线构建两个 wheel，在两个不继承系统 site-packages 的
venv 中安装，清除 `PYTHONPATH/PYTHONHOME`，删除临时构建源码后检查 CLI、Schema、包独立性、
包内测试与 `pip check`；Skill 解包后也独立运行校验和测试。结果、日志、依赖快照、制品摘要写入
该目录。构建解释器需预装满足 pyproject 要求的 setuptools/wheel；脚本不下载依赖。

MCP 基础包不安装 Codex、Runner 或 Ray/MLflow；Runner 不安装 MCP 服务或 Ray/MLflow。
这证明基础发布包独立性，真实 MCP 主机还需安装匹配 wheel 的 `platform` extra 并核实平台版本。
workload 通过自己的不可变 ZIP 发布与验证，不以 Runner 环境中的源码 import 作为部署方案。

## 3. 部署 MCP

1. 创建专用 `galatea-mcp` 账号和本地持久卷 `/var/lib/galatea-mcp`，目录 0700。
   使用独立 `/opt/galatea-mcp` 环境；安装已核验 wheel 与平台客户端依赖。
2. 将配置、Registry 和私钥放入 `/etc/galatea-mcp`。私钥和 `service.env` 为 0600、服务账号可读。
   Registry 的 `root` 指向服务可读的不可变 Release/config 目录。
3. 配置两个独立的 trainer/evaluator Ray 集群及头节点身份，设置不同角色的数据凭据。
   MCP 持批准对象校验与 Artifact 读取权限；Runner 只持模型认证和受限 MCP token。
4. 服务未启动时执行离线验证/登记；读写管理入口与服务使用同一个 writer.lock：

```bash
/opt/galatea-mcp/bin/galatea-mcp --config /etc/galatea-mcp/config.json validate
/opt/galatea-mcp/bin/galatea-mcp --config /etc/galatea-mcp/config.json register --spec /secure/campaign.json
/opt/galatea-mcp/bin/galatea-mcp --config /etc/galatea-mcp/config.json preflight
```

`validate/register` 不启动计算；`preflight` 经 API 读取集群身份和 MLflow Experiment，
不证明数据访问隔离。`reconcile` 会处理 pending 提交、超时和停止，属于有副作用运维命令。

5. 在目标 Linux 主机调整并验证 [MCP unit](../../systemd/galatea-mcp.service) 后安装：

```bash
systemd-analyze verify systemd/galatea-mcp.service
sudo systemctl daemon-reload
sudo systemctl start galatea-mcp.service
sudo systemctl is-active galatea-mcp.service
```

unit 必须先按目标主机复制到 systemd 搜索路径；上述命令仅在实际部署步骤执行。
MCP 监听 loopback 8791；跨主机访问使用 [TLS 代理片段](../../services/galatea-mcp/deploy/nginx.example.conf)。
验收正确 token 的 initialize/tools/list、缺失/错误 token 被拒绝、越权项目/Campaign 被拒绝，
公开工具恰为 17 个。`/mcp` 是协议端点，不以普通 GET 返回 200 代替握手验收。

## 4. Release 接纳与计算准入

按 [workload README](../../train-model/ray-tabular-regression/README.md) 生成代码 Release 和登记材料：

```bash
/path/to/workload/python train-model/ray-tabular-regression/scripts/check_plan.py \
  --config train-model/ray-tabular-regression/configs/baseline.yaml
/path/to/workload/python train-model/ray-tabular-regression/scripts/build_release.py \
  --output /secure/releases/tabular-v1.zip \
  --execution-public-key /secure/keys/execution-public.pem \
  --registration-output /secure/releases/tabular-v1 \
  --environment-identity 'immutable-environment-lock-identity'
```

生成目录包括 Release ZIP 副本、`configs/baseline.json`、`project.generated.json` 和
`campaign.generated.json`。生成的 config 字节摘要、Release 路径和内部配置能够直接通过 MCP
Registry 校验；这不授予示例数据和批准字段实际效力。管理员仍须补齐对象 VersionId、内容摘要、大小、
manifest/split/holdout 身份、代码 revision、环境身份、MLflow Experiment、批准人和有效期。
登记文件采用 `{"schema_version":"galatea.registry/v1","projects":[项目对象]}` 包装。

当前 Campaign 样例只有 baseline、Champion、evaluate 三个槽位，`max_trials=0`，CPU 上限 990 秒。
要验收四任务路径，必须在首次登记前批准一个 Trial 槽位、令 `max_trials=1` 并重算总预算。
默认每个槽位 `1 CPU × 1 worker × (300 + 30) 秒`，四槽位总计 **1320 CPU 秒、0 GPU 秒**。
这是预算示例，不是本次训练授权；不得用 `amend` 临时加槽位，它仅允许受限批准字段更新。

使用普通 MCP client 或 Runner 执行同一协议：

| 步骤 | MCP 动作与应保留的证据 |
| --- | --- |
| 检查 | get_capabilities、inspect_project、get_campaign；固定已批准 IDs |
| baseline | plan_run → submit_job；保存 plan_id、operation_id、submission_id |
| 等待 | observe_job/list_operations；等待 Ray 终态及 quality/integrity 核验 |
| Trial | 在批准槽位提交；仅比较兼容 train/validation 证据 |
| 候选冻结 | compare_runs → freeze_candidate；保存 run_id 和 evidence_digest |
| Champion | 使用冻结配置，干净重训；保留模型摘要和 MLflow Run ID |
| 最终评价 | evaluate 槽位、独立 evaluator、同一 Champion 模型；test-once marker 先于提交持久化 |
| 交付 | verify_candidate；保存平台 report_ref/evidence_refs/outcome/integrity/final_test_status |

发生断连先用操作列表找回原 operation/submission，不生成新的请求键推测未提交。
只有固定 Release → MCP Plan/授权 → 签名 Ray Driver 可以启动拟合；不运行 Driver shell 命令或
通用 `ray job submit`。对象权限拒绝测试使用隔离账号和受控探针，不在日志中输出测试样本或标签。

## 5. 部署 Runner 与官方 Runtime 验收

创建 `training-agent` 专用账号，其 home、Codex 会话、workspace 和状态均在受保护持久卷。
安装 Runner wheel 和经独立验收的官方 SDK/Runtime，保留对应版本制品。
解包完整 Skill 到 `/opt/galatea-training-skills`，入口为
`/opt/galatea-training-skills/skills/training-campaign/SKILL.md`，整包只读并保留旧版本供恢复。

官方 SDK/Runtime 的真实验收遵循 [Runner 的八步清单](../../agents/training-agent-runtime/README.md#real-environment-end-to-end-acceptance)。
先在隔离账号以有预算的官方 SDK 探针验证原生 start/resume、SkillInput、结构化输出和进程清理，
保留证据后由管理员填写 runtime-lock 并批准 `acceptance_status=accepted`。
源码级 fake 测试不能据此将随包 pending 锁改为 accepted。
锁校验已覆盖安装的 SDK 版本、Runtime 实际可执行文件摘要/版本输出和完整 Skill 包摘要；
SDK 制品及传递依赖的来源核验仍由发布流程承担。

```bash
/opt/training-agent/bin/training-agent --config /etc/training-agent/config.json register \
  --revision 1 --prompt 'Execute the registered, authorized campaign within its approved slots and budget'
/opt/training-agent/bin/training-agent --config /etc/training-agent/config.json preflight
systemd-analyze verify agents/training-agent-runtime/deploy/training-agent.service
/opt/training-agent/bin/training-agent --config /etc/training-agent/config.json run --once
```

`run --once` 可能触发真实模型和已批准计算，只有获得明确模型/训练预算后执行。
注册与 pending 锁不会自动授予执行权。完整服务使用同一命令去掉 `--once`；安装并启动
[Runner unit](../../agents/training-agent-runtime/deploy/training-agent.service) 前确认旧 Runner 不在运行。
即使 Runtime 暂不可用，现有任务的观察和取消仍通过 MCP 完成。

## 6. 故障恢复与回退

先在隔离 fake Campaign 做故障注入，实际 Ray 故障只作用于已批准验收任务。

| 故障 | 操作与验收结果 |
| --- | --- |
| 接纳提交后丢响应 | 重启 MCP，观察同一 submission；Job 数不增加；unknown 不自动重发 |
| Ray succeeded，Artifact 未发布 | Runner 为 waiting_evidence；至少两个 tick 不增加模型调用；发布证据后恢复原 Thread |
| Runner 在提交后退出 | 保留状态与 worker/identity.json；重启先检查旧进程组再对账；不重新请求模型生成操作 ID |
| 旧 worker 仍活跃 | 第二 Runner 拒绝接管；核对账号、PID/进程组及 systemd cgroup 后清理原组；不盲删 marker |
| Thread/Turn 记账中断 | 从 journal 找回身份；usage_unknown 禁止新推理，仍观察作业；平台已核验报告可完成交付 |
| JSON 损坏/写入中断 | 保留原文件/备份并停止决策；恢复完整一致快照后经平台 API 对账，不重置空任务 |
| 第二 MCP/Runner 写进程 | 文件锁拒绝；不能把 NFS 锁当作跨主机接管协议 |
| 取消/超时 | 先持久取消阻止新提交，再中断 worker、stop 原操作；直到终态才 cancelled |
| 无模型用量/预算不足 | 禁止新决策，保留观察/核验能力；token 阈值只限制后续 Turn，不是计费硬上限 |
| 报告被替换/产物损坏 | Runner 拒绝外来 report_ref；Artifact 缺失/摘要不符不能 accepted |
| 最终测试重复/质量失败 | test-once 拒绝重复访问；测试已消费标记保留；质量失败只能按平台证据 best-effort/blocked |

正常停止 Runner 的 systemd 服务只停止 Runner/app-server，不代表 Ray 已停止。主动取消使用：

```bash
/opt/training-agent/bin/training-agent --config /etc/training-agent/config.json cancel
```

备份包含 MCP `campaigns/` 与 `evaluation-uses/`，Runner `campaigns/`、`worker/`、原 workspace、
Codex 自有会话卷，以及匹配的只读 Release、Registry、config、Skill/Runtime 锁。
暂停写入后做一致快照；MLflow/MinIO 按平台既有一致备份方案处理，不读取其数据库或对象服务器目录。

升级/回退顺序：暂停新 Turn/提交 → 处理旧 worker 和不明提交 → 备份 → 切换兼容制品 →
保留原 IDs 与 test-once marker → API 对账 → 恢复调度。旧版本不支持当前 Schema 时停止回退并做
显式转换，不能清空状态。跨主机恢复必须搬迁受保护卷并确认旧执行组已停止；首版无自动接管。
`plugins/` 不承担新服务的代发或回退，不修改其源码、配置、Release 或运行入口。

## 7. 真实上线判定

逐项填写 [A01–A17 验收矩阵](implementation/08-deployment-and-acceptance.md#3-验收矩阵)，
附当前安装清单和证据引用。必须实测 HTTPS 身份、Linux cgroup 清理、SDK/Skill 发现、真实集群身份、
角色数据权限、Artifact 干净进程加载与合成输入推理、最终测试单次消费。
最终报告应由 MCP 核验并登记；Agent 声称成功、Ray SUCCEEDED 或本地 fake 的 accepted 均不能代替。

先完成一个固定 workload 的批准小预算闭环，不开启自动搜索，不改生产 Alias。
本手册和本地测试完成后，尚未获得真实观测的项目保持 pending。
