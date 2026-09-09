# Galatea MCP

独立 Python 服务；无 Codex、Harness、旧插件或模型框架的源码依赖。
实现 `galatea.tools/v1` 的 17 个工具、单写 JSON 状态、批准槽位/预算、幂等提交与恢复、
验证集比较、冻结候选、干净 Champion、test-once 和 API Artifact 核验。

## 安装和本地验证

在本目录使用独立 Python 3.11 环境：

```bash
python -m pip install .
PYTHONPATH=src python -m unittest discover -s tests -v
```

真实平台部署安装 `.[platform]`。固定 MCP SDK 1.26.0；不跟随上游 v2 自动升级。
Ray 2.48.0、MLflow Skinny 3.14.0、boto3 1.40.0 是客户端依赖选择；远端版本、身份和权限仍须验收。
测试 fake 只替换 API I/O，不训练、不创建真实 MLflow Run，也不读取真实测试数据。

## 配置与入口

[配置样例](deploy/config.example.json)中 `registry_path` 指向：

```json
{"schema_version":"galatea.registry/v1","projects":[]}
```

每个项目符合 [Project Schema](contracts/project.schema.json)；Campaign 符合
[Campaign Schema](contracts/campaign.schema.json)。参考 workload 的构建命令生成真实代码/config 摘要，
管理员补齐已批准对象 VersionId/内容 SHA256、split、代码/environment 身份后登记。
`holdout_identity` 必须等于冻结 test 对象内容 SHA256；对象别名变化不能重置该标记。
这只保证相同冻结字节人口，语义相同但重新序列化的数据仍须管理员统一身份和审查历史使用。

```bash
galatea-mcp --config /etc/galatea-mcp/config.json validate
galatea-mcp --config /etc/galatea-mcp/config.json register --spec /secure/campaign.json
galatea-mcp --config /etc/galatea-mcp/config.json preflight
galatea-mcp --config /etc/galatea-mcp/config.json serve --transport http
```

`validate/register/amend` 离线运行，不连接平台。`preflight` 只读 Ray 头节点与 MLflow Experiment；
它不是数据隔离或训练成功证明。`reconcile` 会恢复已接纳 pending、对账与停止超时 Job，属于有副作用运维入口。
所有入口持同一 writer.lock；离线登记/修改先停止 MCP 写进程，保留正常运行 Job。

`amend --campaign-id ID --changes FILE` 只接受 `request_revision/expires_at/approved_by/budget`，
要求更高 revision、无活动/未知操作和非取消 Campaign，预算不可降低，不增加槽位、不重置测试标记。
旧 Plan 必须重新生成。MCP 不提供模型可调用的授权修改工具。

## 平台连接与身份

HTTP 服务仅绑定 `127.0.0.1:8791`，使用固定受限 Principal + bearer token，每次重新检查项目、Campaign 和动作。
外部访问通过 TLS 代理；[nginx 片段](deploy/nginx.example.conf)保留 token、重写 Host 并关闭缓冲。
本地 stdio 继承专用服务账号的固定身份；只供受信本机进程。

MCP 账号持自己的 S3 读取/MLflow API 凭据。trainer/evaluator 的环境变量从
服务端 `env_refs` 注入，模型不能指定。两种角色使用不同 Ray 地址、不同数据凭据，预配置环境具有固定依赖。
只靠 Ray namespace 不构成隔离。V1 Release 不嵌入签名公钥；MCP 发出 canonical execution binding，
Driver 将其与 Jobs API 的真实 job_id、不可变输入和 exact metadata 匹配。旧版 workload 如仍声明
签名密钥可继续使用兼容模式，但不是 V1 的部署要求。
API 暂不可用/头节点变化/metadata 不符时保留 unknown，不把 404 当作尚未执行。

单服务允许一个活跃 Job；真实资源保留量是 `workers × 每 worker 资源 × (seconds + cleanup_seconds)`，
首个 workload 仅支持一个 worker。已开始或可能开始的计算不退款；保留最终训练和评价预算。
CPU/GPU 指标是保守上限，不是账单。deadline 由 workload 与 MCP watchdog 双重处理；平台失联仍有故障边界。

## 证据和接口限制

- Artifact 只接受 MLflow 代理 URI 与批准相对路径，临时下载后验证大小和 SHA256；不会加载模型。
- Trial 查询只开放 `train_`/`val_`，评价只开放 `test_`；模型样本、测试标签和原始 Ray 日志不经 MCP 返回。
- 原始日志默认关闭，capability 返回 `raw_logs=false/log_bytes=0`；比设计的 8 KiB 上限更严格。
- 页默认 50、最多 100；返回 envelope 最大 256 KiB，小 evidence 64 KiB；曲线直接使用官方 Tracking HTTP API 的 max_results/page_token，每次只获取一页。
- accepted 必须有干净 Champion、绑定同一模型的最终评价、质量门槛通过与可下载完整产物；否则 best-effort 或 blocked。
- 新数据上传、动态 Release 构建、pause/resume、LoRA 产品化与生产 Alias 不在首版工具集合。

状态路径：`campaigns/<id>.json`、`evaluation-uses/<test-sha256>.json`、`writer.lock`。
文件损坏保留原件并阻止执行。备份/回退必须连同测试标记，禁止靠清空状态重试。
完整真实验收见 [端到端联调手册](../../doc/train-agent/08-e2e-integration-runbook.md)。
