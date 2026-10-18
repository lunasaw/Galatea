# Codex Agent Host

基于原生 `codex app-server` 的通用 Agent 与网页工作台。Agent 自主选择 Skill、工具和下一步行动；Host 负责进程、会话、审批、调用记录和恢复，不定义训练阶段或业务节点。

当前完成第一阶段：可独立运行，通过配置连接 Skill、自定义 Tool 和外部 MCP。第二阶段再接入 Galatea 等业务服务，并增加阶段、资源和产物可视化。

仓库整体职责见 [Codex 与 Galatea 架构](../../doc/agent-galatea.md)。本组件不内置 Galatea Adapter；
项目 YAML 固定入口与 MCP Campaign 服务分别维护业务授权和执行证据。旧训练 Skill 包的
`wait_external` 不会触发 Host 自动轮询或恢复 Campaign，使用该约定需另行实现并验收外部编排。

## 启动

在本目录执行：

```sh
sh start.sh
```

脚本在 `.local` 创建独立 Conda 环境，安装本项目依赖与固定版本 Codex，构建前端。首次从 `CODEX_AGENT_AUTH_DIR`（默认 `~/.codex`）复制模型连接与认证；也可提供 `OPENAI_API_KEY`。无需安装 Galatea Python 包或运行训练平台。

首次生成 `.local/agent.json` 与 `.local/codex-home/config.toml`，之后保留用户编辑。`CODEX_AGENT_MODEL`、`CODEX_AGENT_WORKSPACE` 控制首次生成的模型和工作目录，`CODEX_AGENT_HOST`、`CODEX_AGENT_PORT` 控制脚本监听地址。

也可以在任意目录安装、使用独立配置启动：

```sh
python -m pip install -e '.[test]'
codex-agent --config /absolute/path/agent.json serve --dev
```

参考 [agent.example.json](config/agent.example.json) 和 [配置与部署](docs/06-config-deployment.md)。原生配置中的模型、权限和 MCP 继续由 Codex 管理，固定协议版本为 **0.163.0**。

## 接入能力

- **Skill**：在 `agent.json` 中设置 `skills.roots`。每个目录包含若干 `<skill>/SKILL.md`，通过原生 `skills/extraRoots/set` 注册；Codex 仍发现工作区和原生 Skill。页面可选择 Skill，Agent 也可根据任务判断使用。
- **MCP**：在 `codex-home/config.toml` 配置 `[mcp_servers.<name>]`，支持独立 stdio 进程和远程服务。模板见 [mcp.example.toml](config/mcp.example.toml)。Host 不实现上传、训练等业务服务。
- **MCP Apps**：工具声明 `ui://` HTML 资源即可展示交互页面，支持消息/上下文桥、工具与资源、全屏/画中画/右侧固定、下载与权限确认。业务页面由 MCP 服务提供；接入约定和能力边界见 [通用 MCP Apps Host](docs/18-mcp-apps.md)。
- **插件**：设置中的插件页面按原生市场展示目录、详情、Skills、MCP 和应用，支持安装、卸载、同步及授权提示。Skill、MCP 与插件管理的设计对照和验收见 [扩展管理](docs/16-integrations.md)。正式 Release 的配置修改保持只读。
- **自定义 Tool**：通过 `tools.catalog_dir` 和 `tools.providers` 加载独立 Python 扩展。示例见 [echo](examples/echo/provider.py)；未配置时无 Host 自定义工具，原生编码工具仍可用。

例如将以下字段加入 `agent.json`，并替换绝对路径：

```json
{
  "skills": {"roots": ["/absolute/path/skills"]},
  "codex": {
    "binary": "/absolute/path/runtime/bin/codex",
    "runtime_version": "codex-cli 0.163.0",
    "inherit_env": ["EXAMPLE_SERVICE_TOKEN"]
  },
  "tools": {
    "catalog_dir": "/absolute/path/examples/echo",
    "actions": ["echo"],
    "providers": [{"path": "/absolute/path/examples/echo/provider.py", "options": {"prefix": "hello "}}]
  }
}
```

这是配置片段，需要与模板中的 `schema_version` 和 `paths` 合并。外部服务的授权与幂等由服务负责；Host 自定义 Tool 额外提供参数校验、操作日志和未知结果恢复接口。详见 [Tool 扩展契约](docs/03-tool-registry.md)。

## 重构边界

仅接受 `codex-agent/v2` 配置；旧 `principal`、`galatea`、`paths.contracts` 和固定工具目录已删除。旧配置不会自动迁移，已有文件不会被覆盖。升级应新建配置和状态目录，重新配置业务扩展；旧部署的接纳证据不能用于新版本。

当前网页保留通用对话、模型/权限设置、文件与终端、Skill/MCP、审批、事件和会话恢复。业务节点的定义与判断交给 Agent 和 Skill；业务阶段图、训练仪表盘和产物面板留在第二阶段。

## 项目与权限交互

侧栏的 `＋` 可添加服务器上的已有目录，或创建文件夹作为项目。`…` 可创建、重命名分组；
项目、分组和会话支持拖动排序，项目菜单也提供上移、下移和移动分组。
桌面端可拖动会话列表右边缘调整宽度，双击恢复默认；宽度保存在当前浏览器，收拢后重新展开也会保留。
每个项目有独立 ID 和名称，多个项目可以选择同一个文件夹，会话归属不按目录合并。
会话列表、聊天记录、模型与项目目录独立加载，已到达的内容立即展示，其余信息在后台补齐；
切换会话时聊天记录不等待健康检查，发送操作仍等待当前会话状态核对完成。
项目名称、多个工作目录、项目顺序和会话归属使用原生项目协议保存；项目分组名称与顺序由同一 Host 共享保存。
折叠状态和本地展示偏好仍保留在浏览器。旧 localStorage 项目通过原生 `project/import` 幂等迁移。
非项目会话另用原生会话分组。共享范围是同一个 Host / Codex Home，不承诺跨主机同步。
编辑项目可增加、移除、调整工作目录，第一个目录用于新聊天。
跨项目拖动会话、输入框下方切换项目都会通过 `thread/settings/update` 更新该会话后续任务的工作目录；
正在运行的会话需结束回复后才能移动。

输入框下方的权限菜单直接切换「请求批准」「帮我批准」「完全访问权限」，`/permissions` 打开同一个菜单。
设置使用原生 `approvalPolicy`、`approvalsReviewer` 和 `sandboxPolicy`；新会话在创建时传入对应权限，
已有会话更新成功后才改变显示，原生拒绝时保留原设置。自定义权限以「自定义权限」显示。
输入框 `＋` 提供文件上传、目标、计划模式，以及 Apps、插件、Skills 引用列表。
在输入框中键入 `$` 可搜索当前工作目录的可用 Skill；用 ↑/↓ 选择、Enter/Tab 确认，也可直接点击。
选中后当前 `$关键词` 转为可移除的引用标签；Esc 关闭候选列表，未选择的文字保持原样。
引用以标签随下一条消息发送；目标与计划输入模式互斥，目标只接受文字。
待发送消息以窄条贴在主输入框上方，显示预览和删除按钮，点击消息可编辑，更多菜单可排序或立即发送。
运行中 Enter 补充当前回复（steer），Tab 将内容加入队列；也可点击输入框内的队列按钮。
快捷键沿用仓库原生 CLI 默认绑定，Skill 和斜杠菜单补全优先；队列由原生运行时在聊天空闲时依次发送。
目标卡片在输入框上方，文件、审批记录、后台终端和子 Agent 在聊天右侧。
`/review` 或「… → 审查更改」打开 CLI 风格的审查选择器：与基准分支比较、未提交更改、指定提交、自定义要求。
支持 ↑/↓、Enter 和 Esc；分支可搜索，提交显示最近 100 条并支持标题或完整 SHA 搜索，可选择当前聊天或原生 detached 独立审查聊天。
麦克风录音作为音频附件，蓝色语音按钮启动原生实时对话。终端提供沙箱命令与主机进程两个标签。
完整功能位置、原生接口与验收边界见 [Web 能力对齐](docs/17-web-capability-parity.md)。
设置现含全部 103 个原生配置域、账户与登录、聊天资源及高级原生参数；
逐项能力清单、配置集上游限制和验证结果见 [原生能力补齐与验收](docs/20-native-capability-completion.md)。
图像生成设置和 API Skill 的连接、密钥保护及网关权限边界见 [图像生成设置](docs/22-image-generation.md)。

聊天右上角「… → 复制」提供深度链接、整段会话 Markdown 和工作目录复制；「导出 Markdown」
将同一份会话内容下载为 `.md` 文件。导出会读取当前 Host 的全部历史分页，保留 Markdown 原文、
公开思考摘要、工具记录与附件引用；附件二进制不打包。回复仍在进行或部分历史不可用时，文件中会注明。
深度链接打开同一 Host 上的指定会话，仍需要原有访问权限。

## 验证

```sh
python -m unittest discover -s tests -p 'test_*.py'
cd frontend
npm test
npm run build
```

真实原生协议验证通过 `CODEX_TEST_RUNTIME_PACKAGE=/absolute/path/runtime` 开启，使用本地模拟 Responses 服务与 MCP echo 进程，不调用付费模型或实际训练。完整说明见 [测试验收](docs/09-test-acceptance.md)。

开发模式不要求部署接纳文件。正式启动仍要求 runtime 来源、协议、扩展和恢复证据；参见 [生产状态](docs/12-production-readiness.md)。
