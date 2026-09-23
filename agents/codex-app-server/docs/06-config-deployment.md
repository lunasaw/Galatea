# 06 配置与部署

## 配置合同

CLI 使用 `--config /absolute/path/agent.json`，JSON 模板见
[agent.example.json](../config/agent.example.json)。相对路径以该文件所在目录解析；拒绝未知字段、
路径穿越、symlink、无效 principal 和越界并发配置。`CODEX_HOME` 固定为配置目录内的
`codex-home/`，Host 状态、事件与工作区也置于该受控目录。

将 [codex.example.toml](../config/codex.example.toml) 审查后复制到 `codex-home/config.toml`，
填写批准的模型。模板按实测 0.153.4 禁用 shell、文件、web、MCP、plugin、协作及 Skills 等非本版
批准的工具能力。模型和工具面仍需实际请求验收；以后增加 Skill 能力需显式版本化合同与重新接纳。

Galatea 的 `registry_path`、`state_root`、`platform` 使用
[原服务模板](../../../services/galatea-mcp/deploy/config.example.json) 的同名字段。
`platform.py` 直接组装原 Service、Principal 和官方 Ray/MLflow/Object backends；不导入 MCP server。
principal 必须与 Host 配置完全相等，不能由模型或 Console 扩大。

Host 子进程环境只含固定 `/usr/bin:/bin` PATH、locale、受控 HOME/CODEX_HOME，以及显式配置的
`model_api_key_file`。不继承宿主 OPENAI_API_KEY 或任何 backend token。模型 key 和 Console token
须是受保护的普通文件（0600），不进入事件、请求记录和模型工具参数。

当前部署威胁模型防止模型通过工具面或环境继承意外接触后端凭据；**同 UID 不提供被攻破 runtime
与 Host 之间的强秘密隔离**。有强隔离要求的环境必须提供独立 UID/容器或 credential broker 的
证据，不能把本版模板当作已经实现该边界。

## Runtime 与 release 接纳

采用统一 CLI package：`variant=codex`、`entrypoint=bin/codex`。复制完整包及许可证到
`runtime/`，保留 `codex-package.json`、资源和执行位，不允许 symlink 或特殊文件。

```bash
codex-agent --config /var/lib/galatea-agent/agent.json inspect-runtime
```

此命令核对版本、binary/package tree/protocol schema/compatibility/catalog/config 摘要，并使用
短命 app-server `config/read(includeLayers=true,cwd=...)` 记录整个有效配置和来源摘要。
不创建 Thread/Turn，不调用模型。该命令输出是待审查测量值，不能自动变成已接纳 release。
完整配置响应不打印到 Console。配置中不得放置明文 secret。

管理员接纳的 `release/manifest.json` 必须包含上述全部测量值；`release/stage0.json` 包含：

```json
{
  "status": "passed",
  "release_sha256": "<manifest canonical JSON SHA-256>",
  "checks": {
    "source_provenance": {
      "status": "passed",
      "evidence_path": "source-provenance.json",
      "evidence_sha256": "<evidence file SHA-256>"
    }
  }
}
```

`checks` 不能仅包含这个示例项，必须覆盖 `stage0.REQUIRED_CHECKS` 的所有项目：来源、包、协议、
catalog、配置、opt-in 正负测试、full/subset 新建和恢复工具面、成功/错误回执、崩溃窗口、
reconciler freshness、HTTP 鉴权/CSRF 和秘密边界。证据相对路径不能逃逸目录，每份文件验证摘要。
证据索引由部署管理员保护；代码验证完整性与绑定，不声称鉴别证据生产者真实性。

模型 wire schema 固定在 `contracts/runtime-compatibility.json`，不能在验收时自动重新生成。
当前 0.153.4 已通过 npm registry 签名和 Sigstore/SLSA 验证，完整资源包与发布 artifact 相同，
已核验来源为 `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`，不是设计时的参考 checkout。
收集工具为 `scripts/verify_runtime_provenance.py`；具体证据见
[生产接纳记录](12-production-readiness.md)。启动门禁还要求 release 文件及其祖先、runtime
和 contracts 资源树由管理员持有且不可被其他身份修改。来源验证不代替最终配置接纳。

## 启动与 HTTP

```bash
codex-agent --config /var/lib/galatea-agent/agent.json check
codex-agent --config /var/lib/galatea-agent/agent.json preflight --artifact-index
codex-agent --config /var/lib/galatea-agent/agent.json serve \
  --token-file /etc/galatea-agent/console-token
```

`check` 核对已接纳 release；`preflight` 独立检查真实 Ray head identity、MLflow experiment 和
冻结 train/validation Object 版本元数据。`--artifact-index` 使用 Tracking/Artifact API 核对
当前 principal 项目内的 baseline/trial Run 及 `reports` 索引，不下载内容。预检不打开 Campaign
StateStore、不取得 writer lock、不构造完整 Service，因此可在接纳前、旧服务仍运行时执行。
没有受限 Run 时返回 incomplete，不能记为通过。不提交 Job、不读取 final test、不更改 Alias。

`serve` 在 backend 构造前执行相同门禁。ASGI lifespan 在同一事件循环内取得 Host flock、验证
release、首次 reconcile、初始化 runtime、比较有效配置与来源；全部成功后才完成 startup。
每次新建、发送和恢复还会再次核对有效配置。reconciler 退出/超过 60 秒不成功，或 RPC 退出，
readiness 都失败。

Console 仅允许 loopback，默认端口 18880。静态登录页和 `/health/live` 可匿名访问；API、SSE、
`/health/ready` 必须 Bearer，写请求还必须匹配配置的 Origin。每个写请求使用
`Idempotency-Key`（消息也支持相同的 `request_id`）；令牌永不放 URL。
请求体上限 1 MiB，消息上限 256 KiB；SSE 默认 32 个连接，读取最多 256 条一批、发送超时 10 秒，
支持 Last-Event-ID、heartbeat、断线重连。HTTP 并发由 uvicorn 限制为 64。

HTTP 实现采用 uvicorn ASGI HTTP/lifespan，未使用 WebSocket。生产候选在独立 Python 3.12.12
环境实测 uvicorn 0.35.0、jsonschema 4.25.1、httpx 0.28.1，符合 Galatea 声明的固定依赖。
[完整依赖锁](../deploy/requirements-linux-x86_64-py312.lock) 固定全部传递依赖和 distribution
哈希，仅用于 Linux x86_64/CPython 3.12。安装说明及离线 wheelhouse 见生产接纳记录；不要以
共享 Conda 环境的不同版本代替接纳。

## systemd 和停机

模板：[galatea-codex-agent.service](../deploy/galatea-codex-agent.service)。用户、安装路径、配置路径
和 Host backend secret 环境文件须由目标主机提供。Unit 使用 control-group 清理和只读系统目录。

```bash
systemd-analyze verify agents/codex-app-server/deploy/galatea-codex-agent.service
```

`render-unit` 按实际配置生成允许写入的状态目录、只读 Codex 配置、Python 导入环境防护和
可选 `Conflicts=galatea-mcp.service`。配置根目录、runtime、contracts、release 和已安装代码
应由 root 持有，业务身份只拥有明确的状态子目录。生产 CLI 不允许替换业务 service factory。
当前候选 unit 使用实际 staging 可执行路径的 `systemd-analyze verify` 已通过；`/opt` 下的
最终路径尚未安装，不能把静态验证描述为服务已经运行。父进程退出后的 helper 清理和真实进程
崩溃窗口已有测试。

停机先停止 admission、取消 reconcile/工具请求、关闭原生 RPC/process group，再等已运行的 backend
线程结束后释放 writer lock。Python 无法安全强杀线程；backend 必须配置网络超时，systemd 的
TimeoutStopSec 是最终进程边界。超时、断线与崩溃均不自动重放副作用。
