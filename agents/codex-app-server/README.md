# Galatea Codex App Server Agent

已实现原生 Codex app-server Host、17 个 Galatea 工具的进程内 Registry、持久化恢复边界和
无 Token 的网页聊天与完整协议追踪 Console。**runtime schema 兼容性调整已确认并通过实测**：模型侧校验冻结的
wire schema，Host 继续严格校验原始工具 schema。

设计入口见 [docs/README.md](docs/README.md)，本次 0.154.0 目标版本、TDD 和浏览器/安装验证见
[Console 验收记录](docs/13-console-acceptance.md)。旧版生产接纳记录仅保留历史事实。

## 一键源码启动

需要已安装 Conda，以及模型登录信息（默认读取 `~/.codex/auth.json`）或 `OPENAI_API_KEY`。
在任意目录执行：

```bash
sh /data/ai/chenzhangyue/galatea-model/galatea/agents/codex-app-server/start.sh
```

脚本创建项目内 Conda 环境、安装 Python/Node 依赖和固定版本的 Codex 引擎、构建网页，
然后以前台方式从源码启动。再次执行会先停止该脚本启动的旧实例及其子进程，再重新启动；
`Ctrl+C` 停止服务。默认监听 `0.0.0.0:18880`，通过 `http://服务器IP:18880/` 访问。
可用 `GALATEA_HOST`、`GALATEA_PORT` 调整监听地址和端口。

环境、引擎、配置、认证副本及运行数据全部放在本目录 `.local/`（Git 忽略）；
配置为 `.local/agent.json` 和 `.local/codex-home/config.toml`，首次生成后保留修改。
可用 `GALATEA_AUTH_DIR` 指定首次导入认证和模型设置的目录，`GALATEA_MODEL` 指定初始模型。
此方式不需要 `/opt/galatea-agent`、`/var/lib/galatea-agent`、systemd 或已部署的 Ray/MLflow/MinIO。
默认提供原生 Codex 编码工作台：Shell/文件、审批与提问、Skills/MCP/插件、图片输入、会话恢复及 SQLite 历史。
使用训练等平台工具时，仍需配置对应平台服务和权限。完整范围和验证边界见 [原生工作台](docs/15-native-workspace.md)。
`serve --dev` 使用源码启动检查；下述正式部署命令继续要求已接纳 release。

## 安装与测试

在独立应用环境安装本包及仓库内 Galatea 包；不会启动 MCP transport：

```bash
python -m pip install -e services/galatea-mcp -e 'agents/codex-app-server[test]'
```

平台依赖按原服务的 `platform` extras 和主机环境管理。开发测试可直接运行：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agents/codex-app-server/tests -p 'test_*.py'

PYTHONPATH=services/galatea-mcp/src \
  /data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s services/galatea-mcp/tests -p 'test_*.py'
```

显式加入真实 binary 测试（本地 mock Responses endpoint，无真实模型调用、训练或 final test）：

```bash
GALATEA_TEST_RUNTIME_PACKAGE=/absolute/path/to/codex-package \
  /data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agents/codex-app-server/tests -p 'test_*.py'
```

它包含全工具/受限 principal 的新建、续轮、跨进程恢复、成功/失败回执，以及使用生产
`ManagedProcess → JsonRpcPeer → AgentHost → ToolRegistry` 的端到端测试。兼容性 fixture
必须经过审查；测试不能根据当前 runtime 输出自动重写期望值。

## 运行

生产使用独立配置目录，参照 [配置及部署](docs/06-config-deployment.md) 准备 runtime、原始合同、
兼容性合同、模型认证文件、平台配置和已接纳 release。模板保持未接纳状态，不能直接启动：

```bash
codex-agent --config /var/lib/galatea-agent/agent.json inspect-runtime
codex-agent --config /var/lib/galatea-agent/agent.json check
codex-agent --config /var/lib/galatea-agent/agent.json preflight
codex-agent --config /var/lib/galatea-agent/agent.json serve
```

`inspect-runtime` 生成供审查的摘要，不授予发布权限；`check` 要求全部 release 证据；`preflight`
只读核对官方 Ray/MLflow/S3 API，加 `--artifact-index` 检查受限 Run 的 Artifact 目录。
它不依赖 release 已接纳，也不打开业务状态。`serve` 同样强制门禁，使用 ASGI lifespan 在同一事件循环启动/停止
Host，并通过原 Service 权限、Campaign、Release 和资源预算决定工具执行。

默认监听 `0.0.0.0:18880`，通过 `http://服务器IP:18880/` 访问，无登录步骤。
部署必须先限制可信内网来源；可用配置 `console.host/port` 或 CLI `--host/--port` 调整。
页面支持聊天、执行流程、协议原文及分页；源码启动默认持久化 Console 历史，旧 memory 配置仍只保留当前进程记录。
Host 会话、执行幂等、工具 journal 和恢复边界继续持久化。

## 证据与限制

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python \
  agents/codex-app-server/scripts/runtime_probe.py \
  --package /absolute/path/to/codex-package --output /tmp/galatea-codex-probe-new
```

探针只接受 `entrypoint=bin/codex` 的统一包。退出码 `1` 表示协议/兼容性失败；`2` 表示协议通过，
正式部署证据仍不完整。本次证据见[Console 验收记录](docs/13-console-acceptance.md)，原始日志位于
仓库根目录的 `outputs/console-20261015/`（Git 忽略）。此前证据保留在
`outputs/codex-app-server-compatibility-20260923/`，历史失败 fixture 保留原始来源记录。

当前目标版本由用户确认为参考项目的 `codex-cli 0.154.0`。已核验真实 schema、工具面、
只读模拟模型往返和跨进程恢复；详见验收记录。0.153.4 的历史来源/部署证据不能迁移到新版本。
0.154.0 的最终来源验证、真实模型/平台部署和网络 ACL 现场验收仍须独立接纳。
本次没有切换系统服务、配置隧道、运行真实训练或更改模型 Alias。
