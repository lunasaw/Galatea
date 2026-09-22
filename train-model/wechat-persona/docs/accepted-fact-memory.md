# 机器确认事实的批量接受与记忆链路

## 当前结果（2026-09-18）

数据拥有者明确接受当前机器确认组作为已确认输入，用于先跑通流程。本次已将
`daily-memory_20bb25fd1141dbe2cd86` 的 1,660 个机器确认组编译为 1,660 张
`confirmed` 记忆卡，并构建独立 BM25 索引。3,157 个延期组和 9,323 个排除组不进入该快照。

受控输出：

```text
/srv/galatea-private/wechat-persona/accepted-fact-memory/accepted-facts_3fc7e960b05133b442ec/
├── manifest.json       # 来源、计数、索引身份和全部输出哈希
├── selection.json      # 确切组、候选、审核 revision 和卡片哈希
├── acceptance.json     # 本次用户批量接受的依据及用途
├── verification.json   # 仅工程验证的聚合结果
└── index/
    ├── cards.json
    └── index_manifest.json
```

选择 digest：`7416b8f3a320c551963a0f4f54beecba1580d3e0b2355ab6cac06d27d8524036`。
索引 digest：`d54aac07a8de0f3d18c5095c25d8ffc17c47132a8069bbbb85beb70ed0774ff0`。

实际验证通过：1,660 张卡片回读一致；32 条按固定位置抽样的卡片自查询均在 top-5 找回；
32 次记忆提示词构造通过；跨 owner 返回为零；无匹配查询返回空；临时副本删除后无残留；
编译内容隐私扫描硬命中为零。自查询用于证明链路接通，不能解释为真实问题准确率或人工质量验收。
独立进程加载、检索及提示词构造通过；重复执行返回 `already_built`。新增契约测试及已有记忆、
检索、隔离和删除回归合计 24 项通过。

## 接受语义与范围

卡片状态为 `confirmed`，另记 `owner_batch_accepted=true`，可以用于本次私有记忆链路。
原机器审核事件及源快照保持原样，`source_review_kind=machine`；逐条人工审核仍未发生，
因此 `human_review_completed=false`。这不会把事实组变成真实聊天回复，也不会赋予 SFT
训练或最终测试资格。没有运行训练、外部模型调用或切换现有推理服务。

当前编译器保留原有事实键和被选中的值，不新增主体判断、多值归并或当前有效期推断。
`source_days`、证据消息、session、候选、审核 revision 和 `available_at` 都保留在卡片血缘中。
历史事实的时间含义沿用源候选；不得将此索引注入历史 SFT 样本或宣称旧 test 未暴露。

## 可复现入口

实现位于 [`fact_resolution.py`](../src/wechat_persona/fact_resolution.py)，CLI 位于
[`compile_confirmed_facts.py`](../scripts/compile_confirmed_facts.py)。首先运行只读计划：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  train-model/wechat-persona/scripts/compile_confirmed_facts.py \
  --snapshot-dir /srv/galatea-private/wechat-persona/memory-daily/daily-memory_20bb25fd1141dbe2cd86 \
  --review-dir /srv/galatea-private/wechat-persona/memory-daily-reviews/daily-memory_20bb25fd1141dbe2cd86 \
  --consent /srv/galatea-private/wechat-persona/consent/consent-c-001.json \
  --controlled-root /srv/galatea-private/wechat-persona \
  --plan
```

已有明确接受依据时，将 `--plan` 替换为 `--execute`，并绑定：

```text
--output-root /srv/galatea-private/wechat-persona/accepted-fact-memory
--selection-sha256 <本次只读计划返回的 digest>
--acceptance-reference <已有用户接受决定的可追溯引用>
```

本次用户已经给出接受决定，不需要重复确认同一批次。源审核、授权、代码或选择变化会产生新
digest，不能冒用旧批次身份。命令会校验当前授权、源文件哈希、终态审计与状态一致性、
owner 和消息血缘；构建后用原子 no-replace 发布。重复执行验证已有产物哈希并返回
`already_built`，不会覆盖现有版本。目录权限为 700，文件为 600。

读取索引复用 [`rag.retrieve`](../src/wechat_persona/rag.py) 和
[`build_grounded_messages`](../src/wechat_persona/rag.py)：`index_ref` 指向上述 `index/`，
`owner_scope` 使用 `selection.json` 内的绑定值。检索结果可进入记忆提示词；接入在线服务是独立步骤。

合成契约测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/wechat-persona/tests -p 'test_fact_resolution.py' -v
```

覆盖只读计划、机器来源保留、精确批次绑定、陈旧审核、审计领先于状态、证据篡改、
原子发布、幂等回读、索引篡改和私有路径约束。
