# 图像生成设置

## 配置文件与生效方式

`agent.json` 支持顶层 `image_generation`，模板见
[agent.example.json](../config/agent.example.json)。内置默认值为不启用、复用聊天连接、
`gpt-image-2`、中等质量、`1024x1024`。
下面是独立 Images API 的配置片段，合并到现有 `agent.json`，保留其他配置：

```json
{
  "image_generation": {
    "enabled": true,
    "connection": "custom",
    "base_url": "https://images.example.com/v1",
    "api_key_file": "image-api-key",
    "model": "gpt-image-2",
    "quality": "medium",
    "size": "1024x1024"
  }
}
```

`api_key_file` 只写密钥文件路径，不在 JSON 中写 Key；文件内容是 API Key 本身，
权限设为 `0600`。相对路径以 `agent.json` 所在目录为基准。
`connection: "current"` 使用聊天的默认服务，省略 `base_url` 和 `api_key_file`；
聊天网关必须支持 Images API 才能出图。

开发实例配置通常位于 `.local/agent.json`，修改配置文件后重启 `sh start.sh`。
启动时会生成或恢复 `imagegen-api` Skill 并刷新原生 Skill 列表；正式实例也可使用配置文件默认值，
不需要先打开网页保存。正式配置变更仍须按发布流程重新接纳。

优先级为：网页已保存设置 > `agent.json` 默认值 > 内置默认值。网页覆盖保存在
`<paths.state_dir>/console/image-generation.json`；需要完全恢复配置文件默认值时，
停止服务、将该覆盖文件移到备份位置后重启。启动产生的
`console/image-generation-runtime.json` 是 Skill 实际读取的配置，不能作为配置入口编辑。
网页保存立即生效，重启保留；无需修改聊天模型。

## 网页设置

设置中的「图像生成」用于配置会话可以显式调用的 API 绘图入口。它采用与「配置」相同的逐行设置布局，保存以下内容：

- 是否启用 API 绘图；
- 复用当前聊天连接，或填写独立的 Images API 地址和 Key；
- GPT Image 模型、质量和尺寸；
- 连接检查返回的模型列表及服务端权限结果。

选择「复用默认聊天连接」时，Host 只读取当前 OpenAI provider 的地址，并引用已有的仅所有者可读密钥文件。浏览器不会收到 Key，生成的 Skill 也不会包含 Key。选择独立连接时，Key 仅写入 Host 状态目录的 0600 配置文件；更换地址后必须重新输入对应的 Key。

独立连接支持 HTTP / HTTPS 网关，填写以 `/v1` 结尾的基础地址；生成图片使用 `/images/generations`，不使用 `/chat/completions`。未提供 `/models` 的网关仍可以手动指定模型，连接检查会继续检查图像接口。

启用后，Host 会生成 `imagegen-api` Skill。聊天中可引用 `$imagegen-api`，由 Skill 调用已保存的模型、地址和凭据。保存设置不会自动生成图片；实际调用仍受原生沙盒、网络批准、服务商额度和分组权限约束。连接检查只读取模型并发送空提示词的参数校验请求，因此不会把检查结果误报为已经成功出图。

当前网关如果能列出 `gpt-image-*` 模型，但在 Images API 返回“分组未开启图像生成”，仍需在网关为该 Key 所属分组开通绘图权限；能读取模型列表并不代表能出图。页面会保留这个明确错误，便于区分配置问题与服务端授权问题。

正式实例的发布配置为只读；开发模式保存后会刷新原生 Skill 列表。更换聊天模型或 provider 后，需要回到该页保存一次，让「复用默认聊天连接」同步新的地址和凭据来源。
