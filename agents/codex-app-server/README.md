# Galatea Codex App Server Agent

已实现原生 Codex app-server Host、17 个 Galatea 工具的进程内 Registry、持久化恢复边界和
带鉴权的 HTTP/SSE Console。**runtime schema 兼容性调整已确认并通过实测**：模型侧校验冻结的
wire schema，Host 继续严格校验原始工具 schema。

设计入口见 [docs/README.md](docs/README.md)，本次实测、兼容性决策和部署限制见
[实施记录](docs/11-stage0-implementation.md)。生产候选包、已验证证据及切换步骤见
[生产接纳记录](docs/12-production-readiness.md)。

## 安装与测试

在独立应用环境安装本包及仓库内 Galatea 包；不会启动 MCP transport：

```bash
python -m pip install -e services/galatea-mcp -e 'agents/codex-app-server[test]'
```

平台依赖按原服务的 `platform` extras 和主机环境管理。开发测试可直接运行：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agents/codex-app-server/tests -p 'test_*.py'

PYTHONPATH=services/galatea-mcp/src \
  /data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s services/galatea-mcp/tests -p 'test_*.py'
```

显式加入真实 binary 测试（本地 mock Responses endpoint，无真实模型调用、训练或 final test）：

```bash
GALATEA_TEST_RUNTIME_PACKAGE=/absolute/path/to/codex-package \
  /data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
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
codex-agent --config /var/lib/galatea-agent/agent.json serve \
  --token-file /etc/galatea-agent/console-token
```

`inspect-runtime` 生成供审查的摘要，不授予发布权限；`check` 要求全部 release 证据；`preflight`
只读核对官方 Ray/MLflow/S3 API，加 `--artifact-index` 检查受限 Run 的 Artifact 目录。
它不依赖 release 已接纳，也不打开业务状态。`serve` 同样强制门禁，使用 ASGI lifespan 在同一事件循环启动/停止
Host，并通过原 Service 权限、Campaign、Release 和资源预算决定工具执行。

打开 `http://127.0.0.1:18880/`，输入访问令牌即可新建会话、发送消息、观察工具步骤、中断、关闭
或核对并恢复。令牌仅保存在当前页面内存；API/SSE 使用 Authorization header，不使用 URL token。

## 证据与限制

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  agents/codex-app-server/scripts/runtime_probe.py \
  --package /absolute/path/to/codex-package --output /tmp/galatea-codex-probe-new
```

探针只接受 `entrypoint=bin/codex` 的统一包。退出码 `1` 表示协议/兼容性失败；`2` 表示协议通过，
正式部署证据仍不完整。最新证据位于仓库根目录的
`outputs/codex-app-server-compatibility-20260923/`（Git 忽略），历史失败 fixture 保留原始来源记录。

当前 `0.153.4` 的 npm 签名和 Sigstore/SLSA 来源已验证，完整包与已安装 runtime 相同；
实际来源 commit 是 `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`。生产候选包在独立
Python 3.12.12 环境完成了带哈希锁的离线安装和 100 项应用测试、48 项服务测试；本机真实平台
只读预检通过。目标模型配置/凭据、有效工具面、完整 release 接纳及服务切换仍待完成。
当前没有安装或切换系统服务，也没有提交真实训练或更改模型 Alias。
