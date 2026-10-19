# 会话与恢复

Agent Loop 完全由原生 Codex 负责。Host 的 `ready`、`starting`、运行中、`recovery_required`、`closed` 等状态描述会话可操作性，不代表训练业务阶段。

Host 使用原子 JSON 存储会话、消息请求与 Tool 调用，并通过单写者锁禁止多个 Host 共用状态目录。消息提交先写 intent；断连或崩溃后结果未知时，恢复从原生 thread/read 核对，不自动重发请求。

可恢复会话绑定 agent 身份、工具集合、运行时、配置和扩展源码摘要。扩展变化不会静默复用旧线程能力；需要新建会话或重新接纳。原生配置编辑仅开发模式开放并更新绑定。

关闭时先中断原生请求和进程，等待已有工具线程退出，再释放 Provider 与单写者锁。执行中的 Python 线程无法安全强制终止，因此 Provider 必须遵守 deadline/cancel 和自己的网络超时。

通用会话恢复不等于业务操作恢复。外部任务身份与回执由 Provider/MCP 保持，详见 [Tool 契约](03-tool-registry.md)。
