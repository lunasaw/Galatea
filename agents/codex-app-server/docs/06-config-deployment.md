# 配置与部署

## 配置文件

仅支持 [agent.example.json](../config/agent.example.json) 中的 `codex-agent/v2`，不提供旧模式或自动迁移。相对路径以 agent.json 所在目录为基准，禁止 `..` 和符号链接。

| 配置 | 用途 |
| --- | --- |
| paths.codex_home | 原生 config.toml、认证与 rollout |
| paths.runtime_dir / codex.binary | 固定版本运行时包 / 二进制 |
| paths.state_dir | Host 会话、调用 journal 与 Console SQLite |
| paths.workspace_dir | 默认工作目录 |
| agent.id / max_active_turns | Host 单身份及并发限制 |
| skills.roots | 原生额外 Skill 根目录 |
| tools.catalog_dir / actions / providers | 可选自定义工具；见 Tool 契约 |
| codex.inherit_env | 传给原生 Codex 与 MCP 子进程的环境变量名 allowlist |
| codex.model_api_key_file | 可选绝对路径密钥文件，仅 owner 可读写 |
| codex.compatibility_profile | 自定义 Dynamic Tools 正式发布所需协议证据 |
| console | 监听地址与 memory/sqlite 观察存储 |
| release | 正式启动的 manifest 与 stage0_record |

原生模型、权限和 MCP 放在 `codex_home/config.toml`，参考 [原生配置](../config/codex.example.toml) 与 [MCP 模板](../config/mcp.example.toml)。MCP 服务声明的 env_vars / bearer_token_env_var 若依赖宿主环境，需要同时列入 codex.inherit_env。Host 控制 HOME 和 CODEX_HOME，不继承其他环境变量。

## 本地开发

`sh start.sh` 只安装本项目，不安装业务服务。已有旧配置会明确拒绝；请备份原文件并按 v2 模板新建配置及状态目录。独立配置可直接运行 `codex-agent --config /absolute/path/agent.json serve --dev`，无需 start.sh。

首次 bootstrap 只复制模型连接与认证，不复制个人 MCP、插件或业务配置；这些能力应在该 Host 的 config.toml 中明确指定。

## 正式部署

`serve` 不加 `--dev` 时要求管理员接纳的 manifest 和完整证据。先使用 `inspect-runtime` 测量实际运行时和 effective config；该命令本身不会生成接纳结论。配置、协议、源码、扩展或依赖变化均需重新接纳。

使用 [systemd 模板](../deploy/codex-agent.service) 或 `render-unit` 为当前路径生成 unit。模板不包含任何业务数据目录；使用单独服务运行 MCP，按需要显式开放子进程的网络和目录权限。

原生编码工具受 systemd 沙箱限制；模板的文件系统写目录为 codex-home、process-home、state 和 workspace，若需要设备或其他执行资源，应按实际部署修改。`0.0.0.0` 需要受控内网或带认证的反向代理。
