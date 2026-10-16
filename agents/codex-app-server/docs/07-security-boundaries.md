# 权限和扩展信任

管理员控制配置、Skill、Tool Provider 和 MCP 连接；模型不能选择任意 Python 模块作为 Host 扩展。Provider 在发布验证后导入，以 Host 身份运行，因此只能安装可信扩展。

自定义 Tool 的 actions allowlist 与输入 JSON Schema 在服务端校验；这些权限只控制 Host Dynamic Tools。Shell/文件权限及 MCP 审批交给原生 Codex。业务项目权限、训练预算、发布资格等必须由项目固定入口或外部业务服务验证，不能依赖模型 prompt。原生审批通过不表示 Campaign 已获批，也不会增加数据、GPU、最终测试或模型晋级权限。

Host 是单身份工作台，不是多租户权限边界。stdio MCP 使用原生子进程环境；远程 MCP 令牌按 config.toml 配置。敏感值不应写入 Skill、catalog 或可提交的 Provider options，应从独立受保护配置或显式环境变量读取。

发布绑定运行时、Host 源码、安装依赖、配置、Provider 目录和显式 Skill 根。工作区/原生目录中的可变 Skill、外部 MCP 服务和远端模型仍属于部署信任边界，不能用本地源码摘要证明外部服务代码未变化。

未知写操作保持 unknown，读取后端回执后才完成；不能为恢复而重复提交。关闭等待已有工具线程，扩展必须实现有界网络调用。
