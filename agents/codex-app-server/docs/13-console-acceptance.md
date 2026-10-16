# 13 Console 实施与本机验收

日期：2026-10-15。对象为[网页聊天与追踪台方案](../../../doc/train-agent/console/README.md)。
用户确认以参考项目的 Codex 0.154.0 为目标。本记录证明本机实现和测试结果，不是生产部署接纳。
基线提交为 `dcf62ca031723b2ae06a4994323104a06dffaa3c`，实现尚在工作区。

## 交付行为

- 原生 RPC 在 initialize 前接入双向观察；未知方法、未绑定消息、成功/错误回执都可查询。
  数字/字符串 ID、0、交错请求与晚绑定有独立测试，drain 失败关联此前出站 event_id。
- 观察端口为异步 open/commit/read_events/snapshot/wait_for_change/stats/close，内存适配器
  原子提交事件与投影；同 batch_id 重试幂等，冲突拒绝。失败保留批次并自动退避重试。
  不设置 TTL、队列容量、采样、淘汰或正文/列表截断；查询分页不代表删除历史。
- 对话按受理请求去重；delta 累积、最终文本替换。工具按 thread/turn/call 合并，普通 item
  同样按 thread/turn/item 区分。终态与累计用量采用实际 Runtime 通知。
- 私有内容在查询前脱敏，包含嵌套 JSON、凭据、签名 URL、私有 reasoning、指令、测试数据、
  原始日志与 Artifact 正文；省略有路径和原因。公开摘要、长文本及 totalTokens 保留。
- 页面包含对话、执行流程、原生事件、运行实例未绑定事件、详情和固定水位历史分页。
  SSE 支持重连、晚绑定重载、UTF-8 半帧和 epoch 重置。慢连接不阻塞采集。
- 取消 Console Token，默认 `0.0.0.0:18880`；同源 POST 保护保留，访问者共用配置 principal。
  平台权限和发布门禁继续有效。Host 会话、HTTP 幂等、工具 journal、进程记录与锁仍持久化，
  仅观察历史在进程重启时清空。

代码入口见[观察服务](../src/codex_agent/console/service.py)、[内存适配器](../src/codex_agent/console/adapters/memory.py)、
[HTTP/SSE](../src/codex_agent/console/server.py)和[页面](../src/codex_agent/console/static/index.html)。
现行 API 见 [05 Console](05-console.md)，迁移方式见 [06 配置与部署](06-config-deployment.md)。

## TDD 与回归证据

先建立存储、RPC、投影与 HTTP 合同测试，再实现对应行为并回归既有 Host/Registry/发布测试。
期间确实通过失败测试定位并修复了：投影或关联异常导致原文不可查/worker 退出、提交故障缺少
自动重试、第一次“更早消息”重复最新页、私有复数字段遗漏，以及跨 Turn 同 item ID 覆盖。
浏览器夹具补齐控制接口后，重新验证按钮到 HTTP 的交互；它不替代真实 Runtime 中断语义测试。

| 验证 | 本次结果 | 可复现入口 |
| --- | --- | --- |
| Agent 全量 Python 测试，开启真实 Runtime 测试 | 131 项通过，无跳过 | `tests/test_*.py` |
| 干净 wheel 安装后的同一套 Agent 测试 | 131 项通过，无源码包回退 | 临时目录内仅复制 tests/config/scripts/deploy |
| 平台业务回归，使用干净安装的 galatea-mcp | 48 项通过，外部后端为 fake | `services/galatea-mcp/tests/` |
| SSE 浏览器传输单元测试 | 2 项通过 | `tests/test_console_transport.cjs` |
| Chromium 真实页面测试 | 通过 | `tests/test_console_browser.cjs`、`console_browser_fixture.py` |
| 渲染 systemd 候选 unit 静态检查 | `systemd-analyze verify` 通过 | 干净虚拟环境的可执行路径，未安装或启动服务 |

浏览器覆盖无登录创建/发送、完整最终文本只出现一次、刷新恢复当前进程历史、XSS 文字转义、
中断/关闭/恢复按钮、工具步骤、原生详情、method 筛选和 450 条消息固定水位翻页。
重启提示使用一次 HTTP `EPOCH_MISMATCH` 响应注入；服务端旧 epoch 拒绝与持久化恢复分别由
HTTP/Host 测试验证。真实 Runtime 的中断待终态、恢复复用 thread 由 Host 合同测试验证。

对应方案矩阵中的协议 E01–E06、内存 M01–M05、投影 P01–P04、持久化/配置 R01–R05，
以及 S01、S03–S06 均有本机测试。C01/C02 使用确定性模型/工具夹具；C03 通过能力接口验证。
C04 网络来源限制与 S02 另一台内网客户端访问尚未现场验收。没有通过等待一天来证明 TTL；
本实现没有 TTL/淘汰路径，容量测试验证超过旧数量限制仍可完整读取。

从仓库根目录复现 Python 回归：

```bash
GALATEA_TEST_RUNTIME_PACKAGE=/absolute/runtime \
  /data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agents/codex-app-server/tests -p 'test_*.py'
```

`GALATEA_TEST_RUNTIME_PACKAGE` 必须包含完整 Runtime 资源，不仅是 bin/codex。
浏览器测试使用单独安装的 Playwright/Chromium，仅作为测试工具，不是应用运行/构建依赖。

## Runtime 0.154.0 合同

测试包来自用户指定参考项目的 `runtime/`，使用完整资源复制做协议探测；没有导入参考项目业务代码。
真实 binary 与本机 mock Responses 服务完成 experimentalApi 反例、完整/受限工具集、新建、
动态工具回执、继续及跨进程 resume。生产模型和训练平台没有参与这些测试。

| 绑定 | SHA-256 |
| --- | --- |
| binary | `3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022` |
| package tree | `3cc41caee17e08d177a0213b881424d41eff0377627ace4d90ada506110ec3c1` |
| 导出协议 schema | `2a6d036d1833df268e237997b3b88bea60682542da46ecc380f6b84276515943` |

夹具及摘要在 [codex-0.154.0](../tests/fixtures/codex-0.154.0/provenance.json)；原 0.153.4 夹具保留作历史。
ServerRequest/ServerNotification 的每种 method 均验证通用捕获，但该测试不声称逐一执行了所有
真实功能。模型侧 schema 按实际 Runtime 转换更新[兼容合同](../config/contracts/runtime-compatibility.json)，
Host 严格工具参数合同保持原值。Runtime 来源仍标为 unverified-development-fixture，
stage0/provenance 保持 blocked，没有因协议通过而伪造生产接纳。

## 干净安装与独立性

Linux x86_64、CPython 3.12.12。创建无 system-site-packages 的虚拟环境，使用本地 wheelhouse
`--no-index` 安装应用、galatea-mcp 和 httpx；`pip check` 通过。应用四个静态资源全部在 wheel 内。
隔离导入确认使用 site-packages，未加载参考项目业务模块、Redis 或 MCP server transport。
galatea-mcp 的既有依赖仍包括 MCP SDK；平台可选 Ray/MLflow/boto3 未在此次虚拟环境中安装或验证。

应用 wheel SHA-256：`5cc4667028191a09b8542b2cad5b07157e998e4e5c678529e12c8d080fff7da4`。
galatea-mcp wheel SHA-256：`2fe7ad2656548108b17c4f6e4808536a9c24e19ff783d9ccf981f12d4b08f2aa`。
平台包从临时干净源码构建，以免仓库已存在的旧 build/lib 缓存混入 wheel；没有改动其业务源码。
此 wheelhouse 是本次开发验证产物，没有替换历史生产依赖锁或现有 release。

本机安装目录为 `/tmp/galatea-console-acceptance/venv-final`，离线 wheelhouse 为同级 `wheels/`。
完整运行仍需要独立、受控的 Codex 包、配置模型服务和既有 Galatea 平台后端；Console 不提供替代实现。

## 合成容量测量

入口：[console_benchmark.py](../scripts/console_benchmark.py)。单进程、无浏览器订阅，先同步注入
8300 条合成通知，再交错 100 次本地 RPC echo，补充 1200 步、450 条消息和 70000 字符正文/
1100 项列表；全量分页计数并核对完整正文。耗时使用 perf_counter，RSS 读取 Linux VmRSS。
这是一次短时开发机测量，不是生产 SLA、峰值 RSS 或长期容量保证。

| 指标 | 本次结果 |
| --- | --- |
| 捕获输入速率 | 约 91,125 条/秒 |
| 全部提交速率 | 约 9,878 条/秒 |
| 积压 | 突发后 8301，flush 后 0 |
| 已保留/完整分页读回 | 10,153 条，观察状态 ready |
| 已编码事件体积 | 7,674,767 bytes；不等于总堆内存 |
| 本地 RPC p50 / p95 / max | 0.208 / 0.272 / 0.613 ms |
| 历史查询 200 条 p50 / p95 | 2.464 / 2.507 ms |
| 快照查询最近 200 条 p50 / p95 | 8.941 / 9.830 ms |
| 进程 RSS 前 / 后 | 24,180 / 47,376 KiB |

内存历史随进程运行持续增长，快照按固定水位重放增量；此实现不承诺恒定内存或查询耗时。
后续 Redis 适配须实现同一异步端口及合同测试，不能以引入 Redis 为由丢历史或接管 Host 执行。

原始测试日志、测量 JSON 和聊天截图保存在本机忽略目录 `outputs/console-20261015/`；
不把生成输出、Runtime 包、虚拟环境或业务运行状态纳入源码提交。

## 升级与现场待验收

删除旧 `paths.event_dir`、`console.token_file`、`console.allowed_origins` 和 `--token-file`；
旧字段明确报迁移错误。配置只接受 memory/process_lifetime，Redis 会拒绝。默认监听地址/端口
及 CLI 覆盖见部署文档。旧 JSONL 不导入、不删除；会话和幂等文件继续使用原目录。

尚未执行：目标主机可信/非可信来源 ACL 验证、另一台内网机器浏览器访问、正式 systemd 安装/
重启、0.154.0 生产来源核验与完整 release 接纳、真实模型和训练平台联调。

## 追加：真实网页前台调试（2026-10-15）

按用户要求以前台进程运行，不启用 systemd 托管；曾安装的候选 unit 已停止并取消开机自启。
当前监听 `127.0.0.1:18880`，启动入口为本机 `/var/lib/galatea-agent/start-console.sh`：

```bash
sudo /var/lib/galatea-agent/start-console.sh
```

脚本前台输出日志，Ctrl+C 停止；以独立 `galatea-agent` 用户运行，使用
`/opt/galatea-agent` 隔离环境及 `/var/lib/galatea-agent/agent.json` 配置。
从另一台电脑调试时先建立 SSH 隧道：

```bash
ssh -N -L 18880:127.0.0.1:18880 用户名@10.26.0.140
```

浏览器打开 `http://127.0.0.1:18880`。API 授权复制自当前 `/root/.codex/auth.json`，
独立凭据文件限制为服务用户读取；模型为 `gpt-6-astra`，沿用当前配置的模型接口。
原生 0.154.0 的模型目录覆盖固定为直接工具调用，完整/受限工具集的新建和跨进程恢复
均通过协议探测，未引入独立 Agent Loop 或替代模型客户端。

本次实际验证：

- Runtime npm 签名和 Sigstore 来源核验通过；仅聊天和能力查询范围的 release 绑定通过。
- 修复 Ray 导入其 vendored 依赖后改变已安装包摘要的问题：固定测量解释器安装目录，
  避免依赖导入顺序影响摘要。新增回归测试先失败、修复后通过；重新构建安装后 Agent
  132 项测试通过，平台既有 48 项回归通过，`pip check` 通过。
- 前台真实进程的 `/health/ready` 和观察服务均就绪。Chromium 页面新建会话、发送真实模型
  请求、成功执行 `galatea_get_capabilities`、收到 `GALATEA_WEB_OK`，Turn 完成且无页面异常；
  刷新后仍显示当前进程历史。证据为本机 `/var/lib/galatea-agent/debug-evidence/real-web.json`
  和同目录 `real-web.png`。
- 后端连接使用显式开发平台配置，Ray 集群身份只读校验通过；当前项目和 campaign 范围为空，
  只允许能力查询。未执行训练、数据对象读取、真实训练工具联调或外部网络 ACL 验收。

此追加不代表完整生产或训练能力验收。前台脚本显式使用 `umask 022`；候选 systemd 的
`UMask=0077` 会影响临时协议导出目录权限及摘要，尚待修复和重新验收。
非回环开放前必须完成网络限制，旧 `http_auth_csrf` 证据索引必须换成本版本的无 Token/同源/
网络隔离证据。旧 release 不会因本次改造而自动通过新代码、配置与 Runtime 摘要校验。
本期没有提交训练、启用 Skill、改变模型 alias，也没有证明模型质量或优于原生 Codex。
