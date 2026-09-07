# 方案 01：最小状态与提交约束实施计划

> 后续实现使用 superpowers:executing-plans，逐任务验证。本文件保留旧名称以维持链接。

**目标：**用少量持久文件支持安全提交和恢复，不建设独立控制平台。
**架构：**所有业务校验位于 Python MCP 包内；单写进程更新有界 Campaign JSON。
**技术栈：**Python、pathlib、JSON、文件锁、原子替换；无 SQL/ORM。
**共同约束：**遵守 [实施索引](README.md)，不抽取或改造 plugins 中任何代码。

## 1. 文件与接口

以下路径相对于 Galatea/services/galatea-mcp：

| 文件 | 职责与接口 |
| --- | --- |
| pyproject.toml、依赖锁 | 本服务独立 Python 环境 |
| src/galatea_mcp/contracts.py | CampaignSpec、Plan、Operation、错误类型；与发布 Schema 对齐 |
| src/galatea_mcp/state.py | `read_campaign(id) -> dict`、`save_campaign(id, snapshot) -> None`、单写锁 |
| src/galatea_mcp/projects.py | 读取管理员项目注册，验证项目/Release/数据身份 |
| src/galatea_mcp/plans.py | `plan_run(context, request) -> dict`，预检和确定输入摘要 |
| src/galatea_mcp/jobs.py | `submit_plan(context, request) -> dict`，稳定 ID、外部提交和对账 |
| src/galatea_mcp/policy.py | 校验批准范围、槽位、重试、阶段、保守资源额度 |
| tests/test_state.py、test_submission.py、test_policy.py | 临时目录和 fake Ray 故障测试 |

context 由认证层生成，包含 principal_id 和允许的 project/campaign/action；请求不能生成 context。
工具字段与返回见 [MCP 契约](../02-platform-mcp-contract.md)。管理员 CLI 导入已批准 spec，
之后 MCP 是运行清单唯一写者；授权修改需受信入口串行处理，模型无权写这些文件。

## 2. 最小磁盘布局

```text
<state-root>/writer.lock
<state-root>/campaigns/<id>.json
<state-root>/evaluation-uses/<holdout-identity>.json
<config-root>/projects.yaml
<config-root>/principals.yaml
```

单 Campaign 文件包含 spec/revision、plans、operations、候选和取消标记。
操作数量受 max_trials、重试与阶段数限制，首版不承载无限历史或全局查询。
评价使用标记的创建/恢复顺序见方案 07，不再增加 Candidate/Claim 关系表。

持久化必须：进程锁全程持有；异步请求在进程内串行修改；同目录临时写入，
flush/fsync 后 os.replace，再 fsync 目录。模型无法写 state-root。
输入 ID 用受限字符集并检查根目录归属，拒绝路径穿越、符号链接和不支持的 Schema。
损坏文件保留并返回 state-corrupt，不自动初始化为空。日志不承担提交事实。

## 3. 提交和预算算法

以 `campaign_id + step_id + attempt` 唯一确定一次计算，输入摘要覆盖配置、Release、数据、seed、role。
同 key 同摘要返回原操作；不同摘要 conflict。新的 idempotency_key 也不能新建同 key 的计算。
step 来自批准槽位，真实重试才增加 attempt，不能用新 Thread/decision_seq 增加预算。

```text
serial critical section:
    authenticate and load campaign
    reject cancelled/expired/invalid scope
    return existing operation if same step + attempt + digest
    reject conflicting digest, invalid Plan or exhausted limits
    add pending operation + deterministic submission_id + resource ceiling
    atomically save the whole Campaign
persist submitting before the network call
submit the approved Release using the stored submission_id
persist accepted status, or unknown if the result is ambiguous
```

pending 表示尚未进入网络调用；submitting 之后可能已有外部副作用。
后台恢复前者，后者先查 Ray；找不到而不能证明未执行时停止自动重发。
同一写进程只允许一个活跃 Campaign/Job；启动时从全部任务清单恢复活跃占用，不能只看内存锁。

保守占用 = 各已接纳计算的全部 worker 资源 × 最大批准时长（含明确清理余量）。
搜索须保护最终重训/评价保留额。成功、失败和未知执行默认保留该次最大额度，不自动退款；
单纯重复请求不重复占用。真实用量另行记录，不能把最大额度展示成实际消费。
内嵌未登记子 Trial 的搜索首版不支持，资源约束范围仅为新 MCP 入口。

## 4. 可评审任务

### T1：文件状态

- [ ] 写临时卷测试：原子替换前崩溃仍可读旧快照；损坏/越界路径拒绝；第二写进程拿锁失败。
- [ ] 实现 read/save、Schema 校验、fsync/replace 和全程进程锁。
- [ ] 用 `python -m unittest discover -s tests -p 'test_state.py' -v` 验证，预期全部通过。

### T2：Plan 与去重

- [ ] 固定摘要向量；测试同 Plan 换请求键返回同 ID、同 step 不同摘要 conflict。
- [ ] 实现 Plan 重核、批准槽位检查、稳定提交 ID 和单文件预算更新。
- [ ] 测试两次提交抢最后额度仅一次接纳，重试超限和取消后提交被拒绝。

### T3：故障窗口

- [ ] fake Ray 注入接受后丢响应，使用原磁盘状态重建服务；断言只存在一个 Job。
- [ ] 注入 pending 后退出、submitting 后退出、Ray 查询不可用和历史 404；验证恢复或 unknown。
- [ ] 运行 `python -m unittest discover -s tests -p 'test_submission.py' -v`，未知状态不得产生新 ID。

提交相关成功条件通过后再接真实 API；没有任何旧插件切换或兼容 adapter 任务。
回退保留状态和 ID，不用恢复旧入口重新发送新 Campaign。
