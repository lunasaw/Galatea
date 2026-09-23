# 12 生产候选与接纳记录

更新：2026-09-23。**生产候选已完成构建、离线重装及回归，最终 release 尚未接纳，服务尚未切换。**
当前 `galatea-mcp.service` 继续持有原业务状态 writer lock。下面区分可复用的软件证据和必须在
目标部署完成的配置/运行验收，不能用前者代替后者。

## 已验证证据

完整证据保存在仓库根目录 `outputs/codex-app-server-production-20260923/`，由 Git 忽略。

| 项目 | 已建立的证据 | 文件 |
| --- | --- | --- |
| Runtime 来源 | npm 11.16.0 验证 registry 签名和 Sigstore/SLSA attestation；下载资源树与安装包逐项相同 | `source-proof-verified/source-provenance.json` |
| Python 依赖 | Python 3.12.12、87 个外部依赖 distribution 的固定版本/哈希，2 个本仓库 wheel | `requirements-production.lock`、`wheelhouse.sha256` |
| 离线重装 | 新建 venv，只用 89 个本地 wheel，`--no-index --require-hashes` 成功；`pip check` 通过 | `install-offline.log`、`installed-software.json` |
| 应用回归 | 100 通过，0 跳过，含 6 个真实 runtime 测试 | `offline-app-tests.log` |
| 原 Galatea 服务 | 48 通过，含 MCP 兼容性和 fake backend 治理测试 | `offline-service-tests.log` |
| 仓库合同 | 7 通过 | `repository-tests.log` |
| 本机平台 | 真实 Ray head identity、MLflow experiment、S3 train/validation 版本元数据和受限 Artifact 索引通过 | `platform-isolated-preflight.json` |
| 部署 unit | 使用已构建 staging executable 的 `systemd-analyze verify` 退出 0 | `systemd-verify.log` |

静态 unit 检查还报告了主机既有 `tat_agent.service` 的旧 `/var/run` 路径提示，与本次 unit 无关。
未运行新 systemd 服务，因此尚无新服务的 liveness/readiness 或维护窗口恢复证据。

Runtime 为 `codex-cli 0.153.4`、`x86_64-unknown-linux-musl`：

- source repository：`https://github.com/openai/codex`
- source commit：`3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`
- source ref：`refs/tags/rust-v0.153.4`
- build workflow：`.github/workflows/rust-release.yml`
- binary SHA-256：`56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da`
- package tree SHA-256：`2574264f46dab839626ec0d7034d54c5454c2d619987107b93e208e1fbd5867a`
- protocol schema SHA-256：`74d5cdc8998ea238aac6af751caa40d95d6cceb01512d5cae542aa3786d2e87d`

来源证明使用通过 npm 验证的 DSSE payload，不把本地 checkout revision 当作二进制来源。
脚本会核对 npm artifact subject SHA-512、仓库、tag、workflow、commit 和全部 runtime 字节；
仅解析 attestation JSON 不算验签。重新取证：

```bash
python agents/codex-app-server/scripts/verify_runtime_provenance.py \
  --package /absolute/path/to/codex-package \
  --output /absolute/new/private/evidence-directory
```

## 本次生产加固与 TDD

新增测试先验证失败，再补实现：

- `source_provenance`：错误 artifact/source/tag/workflow 不接受；来源证据绑定实际 runtime。
- `software_measurement`：Host/Galatea 代码和 Console 静态文件纳入测量；runtime 字节不匹配时，
  在运行该 binary 之前拒绝。最终 wheel 中的两份代码摘要与测试源码逐项一致。
- `release_permissions`：release 祖先、runtime 和 contracts 必须为管理员所有；拒绝可写资源、
  symlink 和特殊文件。systemd 将 Codex 配置文件挂为只读，阻止服务身份改写。
- `release_gate`：生产 CLI 不再接受未纳入测量的 `--service-factory`，固定组装官方后端。
- `platform_preflight` / `artifact_preflight`：接纳前可执行元数据预检，不构造 Service、打开
  Campaign 状态、读取签名密钥或解析 Job 凭据；Artifact 只查询本 principal 的 baseline/trial。
- `deployment_render`：校验 systemd 参数，限制可写路径，清除 Python 外部导入环境，支持与旧
  MCP 服务互斥，不执行安装或启动。
- `crash_process`：实际子进程分别在持久 intent 后、handler 执行中、receipt 保存前退出；重启
  不再执行未知副作用。使用 fake 外部效果标记，没有真实训练。

平台预检在原服务运行时执行，没有下载 Artifact 内容、访问 final test、提交 Job 或更改 Alias。
Artifact 内容完整性、正式 Campaign 的治理行为和 Job stop/reconcile 仍需对应的现场验收。

## 可重建交付物

[外部依赖输入](../deploy/requirements.in) 对齐两个包的 pyproject，
[哈希锁](../deploy/requirements-linux-x86_64-py312.lock) 固定 Linux x86_64 / CPython 3.12 的
完整依赖图。测试版本包括 Ray 2.48.0、MLflow Skinny 3.14.0、boto3 1.40.0、pydantic 2.12.5、
uvicorn 0.35.0、jsonschema 4.25.1、cryptography 45.0.6。共享 Conda 环境不是部署依赖来源。

输出目录已包含可供复核和复制的 `wheelhouse/`、`wheelhouse.sha256` 和包含本地 wheel 哈希的
`requirements-production.lock`。源码或依赖变更后必须重建 wheel、更新锁、回归并重新测量，
不能沿用本次证据。构建器使用 setuptools 及 wheel；发布安装不依赖在线构建步骤。

在目标 Linux x86_64 主机使用批准的 Python 3.12.12，以下命令安装已审查候选包：

```bash
bundle_dir=/absolute/path/to/reviewed-production-bundle
cd "$bundle_dir"
sha256sum --check wheelhouse.sha256
python3.12 -m venv /opt/galatea-codex-agent
/opt/galatea-codex-agent/bin/python -m pip install \
  --no-index --find-links "$bundle_dir/wheelhouse" --require-hashes \
  --only-binary=:all: -r "$bundle_dir/requirements-production.lock"
/opt/galatea-codex-agent/bin/python -m pip check
```

本次只在 `outputs/.../offline-venv/` 演练上述离线安装，没有写入 `/opt`。wheelhouse 不包含
Python 解释器、系统库和 Codex binary；它们分别由目标环境与已验签的 runtime 包提供，并纳入
发布测量。软件版本列表和哈希不等于所有 Python 依赖的供应链签名。

## 当前主机部署候选

私有 `deployment/` 目录包含：

- `agent.json`：保留当前 deployment 的项目、Campaign、principal 和业务状态引用。
- `galatea-codex-agent.service`：最终 `/opt` 路径；使用既有 `galatea-mcp` 身份和 backend
  environment file，`Conflicts=galatea-mcp.service` 防止两个服务同时运行。
- `galatea-codex-agent-staged.service`：只用于本次静态路径验证，不用于安装。
- `codex.toml.pending-model`：禁用未批准工具能力的模型配置模板，尚未填入目标 provider/model。

候选使用 `/var/lib/galatea-codex-agent` 保存 Host/Codex 状态，业务状态继续使用
`/var/lib/galatea-mcp`；不能复制正在写入的状态后启动另一个控制器。`/opt` 代码、配置根目录、
runtime、contracts、release 和 `agent.json` 由 root 持有，服务身份仅拥有 unit 明确允许的状态
子目录。`codex-home/config.toml` 应 root 持有、服务组可读，并保留 unit 中的只读文件挂载。
模型 key 和 Console token 单独存放，0600，服务身份可读；不要放进配置正文、日志或仓库。

复核时还需确认服务身份可遍历 registry/project/release 引用的所有受限目录，不要扩大为全盘
读写权限。密钥边界沿用本版设计：子进程环境不继承后端凭据；同 UID 的被攻破进程仍可能读取
Host 凭据。若目标要求强隔离，独立 UID/容器/broker 验收是前置条件，当前候选不声称已实现。

## 接纳、切换与回滚

尚需确定目标主机及独立模型配置/凭据文件路径；不能擅自复制 root 的 Codex 账号认证作为服务
凭据。确认后补齐最终模型配置，使用实际安装位置执行 `inspect-runtime`，记录 effective config
及全部来源。对目标配置重新捕获新建/恢复请求，证明工具面与批准的 full/subset 合同一致；
只使用受控协议 fixture，不让验收请求触发真实业务 mutation。

完整接纳记录必须覆盖 `stage0.REQUIRED_CHECKS` 并绑定最终 manifest；本次 readiness 报告和
旧的协议报告都不是可直接启动服务的 `stage0.json`。有效配置、secret 边界或任一证据缺失时，
`check`/`serve` 继续失败关闭。

切换会中断旧 MCP transport，并转移共享业务状态的唯一 writer。维护窗口应按以下顺序执行：

1. 完成配置/来源/权限/release 接纳和最终 unit 静态验证，记录旧 unit、package 与配置版本。
   先备齐可恢复的软件；此时旧服务继续运行。
2. 停止旧服务，确认进程和 writer lock 已释放，再备份完整业务状态。备份同时包含 Host 的
   state/events/codex-home（如果已存在）；受限目录中保存校验和，禁止把密钥或业务状态提交 Git。
3. 安装已审查 unit，启动新 Host。核对匿名 liveness、带 Bearer 的 readiness，以及首次 reconcile、
   runtime 配置绑定、认证/Origin、SSE 恢复和 process-group cleanup。首次仅做受限只读调用。
4. 任一接纳失败，停止新 Host 并确认其进程组和 writer 已释放；保留 Host 日志、会话和 unknown
   operation，再恢复旧服务用于核对。不能在原进程仍存活时启动第二 writer。
5. 若切换后已产生业务状态更新，不把切换前备份直接覆盖当前状态；先对照持久 operation 和真实
   后端状态核对。未知提交不重放，恢复备份不能造成第二次 Job 提交。

真正的受治理训练 smoke、最终测试或 Alias 推广需要各自明确授权及现有 Release/预算/证据门禁。
它们没有在本次软件接纳中执行，也不通过伪造 Run/Job 证据补齐。
