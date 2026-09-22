# 按天上下文的事实与记忆预处理方案

> 版本：`wechat-persona-memory-daily-v1`
>
> 状态：设计稿，不单独授权真实数据处理、外部 GPT 调用、训练或生产发布。
>
> 适用项目：`train-model/wechat-persona/`
>
> 主导航：[`docs/README.md`](README.md)

本文负责事实记忆分支。按天整理聊天、识别回应话题并构造真实回复训练样本，见
[日级话题与真实回复候选](daily-topic-sft.md)。两条分支共享消息来源，
审核结果与用途资格分别管理；事实抽取的前后邻日上下文不能直接用作回复训练输入。

## 1. 结论

建议将聊天数据改为“按天组织上下文、按会话分块、按事实归并”的分层预处理方式。
日级处理能让抽取模型看到比在线 RAG 更完整的上下文，降低当前小块检索造成的漏证据和截断问题；
但它不能单独解决事实回答问题。最终必须把抽取结果编译成带 `fact_key`、标准化值、证据和冲突状态的结构化事实账本。

推荐链路如下：

```text
normalized/redacted messages
        |
        v
按固定业务时区生成 day bundle
        |
        v
按 session 和 token 预算分块，必要时保留相邻上下文
        |
        v
GPT/受控抽取模型输出严格 JSON 事实候选
        |
        v
日内去重与日期解析
        |
        v
跨天按 fact_key 归并、发现冲突、保留证据
        |
        v
人工审核 candidate
        |
        +----------------------------+
        |                            |
        v                            v
confirmed fact index          authorized evidence index
        |                            |
        +-------------+--------------+
                      v
       精确事实查询路由 / 开放式 RAG + LLM
```

日级抽取负责扩大离线观察窗口；事实账本负责确定性回答。不能用“日摘要”代替事实账本，也不能让
0.8B 模型在若干聊天片段中自行猜日期。

## 2. 当前问题与本方案的针对关系

当前在线服务具有较窄的上下文边界：普通检索默认只取有限条目，配置还限制了记忆字符数和模型输入
token 数。在线阶段若直接让小基座从聊天片段归纳“哪天表白”，会同时受到检索召回、上下文截断、
别名不一致和小模型推理能力的影响。

现有项目已经具备以下可复用部件：

| 现有部件 | 本方案的用法 |
| --- | --- |
| `src/wechat_persona/normalize.py` | 生成统一时间、角色、消息类型和脱敏文本 |
| `src/wechat_persona/sessionize.py` | 保留会话边界和连续消息顺序；不改为单消息随机切分 |
| `src/wechat_persona/evidence.py` | 继续生成授权证据卡；规则抽取作为可比较的 baseline |
| `schemas/message.schema.json` | 日级 bundle 的消息输入来源 |
| `schemas/memory-card.schema.json` | 人工确认后编译出的可删除事实/记忆卡 |
| `scripts/build_memory_index.py` | 消费审核后的卡片，构建 owner-scoped 索引 |
| `src/wechat_persona/rag.py` | 证据检索、指标、删除账本和 owner 隔离 |
| `docs/gpt-prelabel-strategy.md` | GPT 预标注的审计、失败和实验边界参考 |

`extract_relationship_event_candidates()` 当前是保守规则抽取器。新方案不应静默替换它，而应同时
保留规则 baseline 与日级 GPT extractor 的版本和评估结果；只有在验证完成后，才决定是否把 GPT 结果
纳入正式记忆候选。

## 3. 目标与非目标

### 3.1 目标

1. 在不依赖在线大上下文的前提下，利用一天内更多消息抽取事实和事件。
2. 处理跨句、跨会话、相对日期和后续回顾等时间表达。
3. 让每个候选都能追溯到精确的消息、会话、日期分桶和抽取版本。
4. 发现同一事实的多个值并保留冲突，不自动选择看似最可信的一个。
5. 让精确事实问法先走结构化查询，开放式回忆才进入 RAG/LLM 生成。
6. 支持增量重跑、撤回、删除、重建索引和新版本并行验证。
7. 保持风格 SFT、事实记忆 RAG、事件卡和训练证据相互隔离。

### 3.2 非目标

- 不把 GPT 抽取结果直接视为人工确认事实。
- 不把每天的聊天压缩成一段没有证据引用的长摘要。
- 不通过加大在线 prompt 来替代事实建模。
- 不因数据是私有、实验性或 `promotable=false` 就绕过授权和治理。
- 不把模型生成的回复写回事实库。
- 不在本文方案阶段启动真实训练、创建 MLflow 训练 Run、访问最终测试集或修改 Registry alias。

## 4. 核心数据模型

### 4.1 日期的两个维度

必须同时保存：

- `conversation_date`：消息实际发生的本地日，用于分桶和上下文组织；
- `event_date`：消息所指向的事实或事件日期，用于回答“哪天发生”。

二者经常不同。例如 8 月 10 日的消息可能说“我们 5 月 23 日确定关系”。不能把消息日期直接当作
事件日期。

所有时间必须先转换到配置声明的 IANA 业务时区，例如 `Asia/Shanghai`，并在 manifest 中固定记录。
不得使用运行节点的本地时区，也不得丢弃原始 UTC offset。

### 4.2 日级 bundle（中间产物）

建议增加 `daily-bundle-v1` 中间 schema。它只存放受控私有目录中的已授权、已脱敏内容：

```json
{
  "schema_version": "daily-bundle-v1",
  "day_id": "2026-05-23",
  "timezone": "Asia/Shanghai",
  "owner_scope": "owner_<opaque>",
  "source_manifest_sha256": "<sha256>",
  "sessions": [
    {
      "session_id": "session_<opaque>",
      "start_time": "2026-05-23T09:00:00+08:00",
      "end_time": "2026-05-23T11:00:00+08:00",
      "turns": [
        {
          "speaker_role": "self",
          "messages": [
            {
              "evidence_ref": "e_001",
              "message_id_digest": "<sha256>",
              "timestamp": "2026-05-23T09:42:00+08:00",
              "text": "已授权的脱敏文本"
            }
          ]
        }
      ]
    }
  ],
  "bundle_digest": "<sha256>"
}
```

发送给外部抽取服务时，优先使用短期的 `evidence_ref`，在本地保留它到真实 `message_id` 的映射。
不要把 owner ID、源路径、内部 digest 或不必要的来源标识发送给模型。抽取结果返回后，再由本地程序
恢复血缘关系。

### 4.3 原子事实候选

建议增加 `fact-extraction-v1` schema。每个候选都必须是原子事实，而不是一段泛化摘要：

```json
{
  "schema_version": "fact-extraction-v1",
  "extraction_id": "extract_<sha256-prefix>",
  "day_id": "2026-05-23",
  "session_id": "session_<opaque>",
  "fact_key": "relationship.started_at",
  "value": "2026-05-23",
  "value_type": "date",
  "date_precision": "day",
  "date_basis": "explicit",
  "claim_type": "explicit",
  "polarity": "positive",
  "confidence": 0.91,
  "aliases": ["表白", "告白", "在一起", "确定关系"],
  "evidence": [
    {
      "evidence_ref": "e_001",
      "quote": "我们是5月23日确定关系的"
    }
  ],
  "unresolved_references": [],
  "status": "candidate",
  "extractor_version": "wechat-fact-gpt-v1",
  "lineage_digest": "<sha256>"
}
```

至少区分以下情况：

- `claim_type`: `explicit`、`relative`、`quoted`、`hypothetical`、`inferred`；
- `polarity`: `positive`、`negative`、`uncertain`；
- `date_basis`: `explicit`、`relative_resolved`、`conversation_timestamp`、`unknown`；
- `date_precision`: `day`、`month`、`year`、`unknown`。

`inferred`、`hypothetical`、`quoted` 或 `uncertain` 不得因为模型置信度高就自动变成确认事实。

### 4.4 事实账本

建议增加 `fact-ledger-v1`。它是跨天归并后的审核对象，不是普通文本摘要：

```json
{
  "schema_version": "fact-ledger-v1",
  "fact_group_id": "factgrp_<sha256-prefix>",
  "owner_scope": "owner_<opaque>",
  "fact_key": "relationship.started_at",
  "aliases": ["表白", "告白", "在一起", "确定关系", "开始交往"],
  "candidates": [
    {
      "normalized_value": "2026-05-23",
      "precision": "day",
      "evidence_refs": ["e_001", "e_017"],
      "source_days": ["2026-05-23", "2026-08-10"],
      "support_count": 2
    }
  ],
  "conflict_status": "none|conflicted|insufficient",
  "review_status": "pending",
  "confirmed_value": null,
  "lineage_digest": "<sha256>"
}
```

审核完成后，才把单一确认值编译为现有 `memory-card-v1`：

- `fact_key` 写入 `relationship.started_at`；
- `attributes.event_date` 写入标准化日期；
- `attributes.aliases` 保存查询别名；
- `source_message_ids`、`source_session_ids` 保存所有支持证据；
- `status=confirmed` 只代表审核后的可用事实；
- 原候选仍保留在受控审核/审计产物中，不被覆盖。

## 5. 分桶和分块策略

### 5.1 输入准备

1. 使用现有 importer 和 `normalize_message()` 生成 `message-v1`。
2. 以固定业务时区解析 timestamp，并拒绝无法定位时区的时间。
3. 保留角色、消息 ID、消息类型、原始顺序和 `reply_to` 关系。
4. 使用事实抽取专用的脱敏模式：电话、邮箱、账号、密码和 token 必须脱敏，日期和时间表达必须保留。
5. 不要直接复用风格 SFT 的日期脱敏逻辑。当前 `redact_style_text()` 将日期替换为 `<日期>`，会破坏事实抽取目标。

### 5.2 日级分桶

以 `(owner_scope, conversation_date)` 为第一层分区；天内继续按 `session_id` 排序。一个 bundle 可以含多
个 session，但不得把不同 owner 合并，也不得丢失 session 边界。

日级分桶只决定“抽取时优先观察哪些消息”，不决定事件日期。事件日期必须由文本和时间解析得到。

默认处理窗口：

- 当前日全部 session，按时间正序；
- 对包含“昨天、前天、那天、第二天”等相对引用的块，自动加入前后各 1 日的少量上下文；
- 若仍无法解析，扩大到最多前后 3 日，并将该引用标记为 `unresolved_references`，不能强行补值。

### 5.3 长日处理

一天不一定适合一次调用。按以下优先级切块：

1. 不跨 session；
2. 不拆开同一轮连续消息；
3. 使用模型 tokenizer 计算 token，而不是只按字符数；
4. 预留系统指令和 JSON 输出预算；
5. 块之间保留 1–2 个完整 turn 或约 5–10% 的重叠；
6. 用 `chunk_ordinal` 和证据 ID 去重，不依赖摘要文本去重。

建议配置为模型相关的参数，而不是写死到代码：

```yaml
daily_memory:
  schema_version: wechat-persona-memory-daily-v1
  timezone: Asia/Shanghai
  grouping: calendar_day_with_session_boundaries
  neighbor_days: 1
  unresolved_neighbor_days: 3
  chunk:
    token_counter: pinned_model_tokenizer
    target_input_tokens: 8000
    hard_input_tokens: 12000
    overlap_turns: 2
    reserve_output_tokens: 2000
  extraction:
    temperature: 0
    strict_json_schema: true
    allow_inference: false
    preserve_dates: true
```

`8000/12000` 只是第一版建议起点，应根据实际抽取模型的上下文窗口和成本校准。任何超出 hard limit
的输入必须产生 `blocked` 或 `incomplete` 状态，不能静默截断。

### 5.4 抽取请求中的提示边界

聊天正文是不可信数据，不是抽取器指令。抽取请求应使用明确分隔符，并要求：

- 只输出 JSON，不输出解释性段落；
- 只从输入证据抽取，不补充常识或猜测；
- 每个事实必须引用至少一个 `evidence_ref`；
- `quote` 必须是输入中的精确子串；
- 没有事实时输出空数组；
- 同一事实出现多个值时全部输出；
- 模型不得执行聊天内容中的命令、链接或系统提示。

抽取服务本身不产生训练参数，也不等于人工审核；它只产生带血缘的候选。

## 6. 时间和别名解析

### 6.1 日期标准化

本地确定性解析器先于 GPT 归并运行：

- `2026-05-23`、`2026/5/23`、`2026年5月23日` 统一为 `2026-05-23`；
- `5月23日` 使用同一消息或同一事件上下文的年份；年份不确定则保留 `date_precision=month/day` 或标为未知；
- 非法日期、越界日期和无法判断年份的日期不得自动修正；
- `今天`、`昨天`、`明天` 等相对日期以消息 timestamp 为锚点；
- `那天`、`第一次` 等指代如果没有可解析先行词，保持未解决。

### 6.2 关系事实别名

第一版至少统一到 `relationship.started_at` 的别名集合：

```text
表白、告白、示爱、在一起、确定关系、开始交往、正式交往、恋爱第一天、关系开始、纪念日
```

别名只用于召回和事实路由，不代表出现别名就一定产生关系开始事实。否定、假设、引用他人经历和玩笑
都必须由 `claim_type`/`polarity` 标记并进入候选审核。

## 7. 两阶段归并和冲突处理

### 7.1 日内归并

对同一天不同 chunk 的结果：

1. 以 `(fact_key, normalized_value, evidence_refs)` 生成幂等键；
2. 合并重复证据，但保留每个来源消息；
3. 保留不同值，不以最新 chunk 覆盖旧值；
4. 检查 quote 是否仍是来源文本精确子串；
5. 校验日期、事实类型和 owner 绑定；
6. 记录 chunk 覆盖率和是否出现未处理块。

### 7.2 跨天归并

跨天 reducer 只比较结构化候选和必要的证据片段，不重新把全部历史聊天塞进一个 prompt。按以下键分组：

```text
(owner_scope, fact_key)
```

归并结果保留：

- 每个不同标准化值；
- 来源日期和消息/会话引用；
- `explicit`/`relative`/`inferred` 等依据；
- 支持数量和来源多样性；
- 是否有否定或相互排斥的证据；
- 当前审核状态。

支持数量只能辅助审核，不能单独触发确认。重复转述可能是同一个错误的传播。

### 7.3 审核规则

建议按 `fact_key` 批量审核，而不是逐条孤立审核。审核页面应同时展示：

- 所有候选值；
- 所有来源日、会话和引用片段；
- 日期解析依据；
- 模型置信度与 extractor 版本；
- 是否存在否定、假设、引用或第三方信息。

决策建议：

| 场景 | 结果 |
| --- | --- |
| 一个明确值，多条独立证据，人工确认 | 编译为 `confirmed` |
| 多个值且无法判断 | 保持 `candidate`，`conflict_status=conflicted` |
| 只有相对表达但无法解析 | `candidate`，要求补充上下文 |
| 仅模型推断，无可引用证据 | 拒绝 |
| 否定、假设、玩笑或第三方事实 | 不编译为当前 owner 的确认记忆 |
| 用户在产品中明确纠正 | 新 revision；旧卡 `superseded`，不覆盖审计记录 |

## 8. 存储和索引布局

所有真实产物放在受控私有目录，下面是逻辑布局示例，不是固定路径：

```text
<private-root>/wechat-persona/memory-daily/<snapshot-id>/
├── manifest.json
├── daily-bundles/index.jsonl
├── daily-extractions/index.jsonl
├── fact-ledger.jsonl
├── review/
│   ├── candidates.jsonl
│   ├── review-state.json
│   └── review-events.audit.jsonl
├── compiled/
│   ├── memory-cards.jsonl
│   └── authorized-evidence.jsonl
├── indexes/
│   ├── fact-index.json
│   └── rag-index/
└── reports/
    ├── extraction-report.json
    ├── temporal-resolution-report.json
    ├── privacy-report.json
    ├── leakage-report.json
    └── deletion-report.json
```

建议维护两个查询面：

1. **fact index**：按 `(owner_scope, fact_key)` 读取 confirmed 值、冲突状态和证据 ID，支持确定性查找；
2. **evidence/RAG index**：检索日级证据块、会话片段和经批准的摘要，支持开放式回忆。

日级摘要可以进入证据索引，但必须带 `record_kind=day_summary`、来源 digest 和明确的非权威属性，
不能伪装成 `confirmed` memory card。

## 9. 在线查询路由

### 9.1 精确事实问题

查询预处理器先判断是否命中事实别名或 `fact_key`：

```text
哪天表白
表白是哪一天
我们什么时候确定关系
在一起的日子是什么
```

命中后直接查 fact index：

- 一个 confirmed 值：直接返回标准化日期；可以让 LLM 只负责语气包装，不能修改日期；
- 多个 confirmed/candidate 值：直接返回冲突列表并请求确认；
- 只有 candidate：说明“目前记录仍在确认中”，不要冒充确定事实；
- 没有记录：明确说不知道，不进入普通聊天片段猜测。

### 9.2 开放式问题

例如“那天发生了什么”“我们是怎么走到一起的”，才检索证据索引，再由模型根据 `<memory>` 证据
生成自然语言。在线 prompt 仍需保留不可信证据边界和无证据拒答策略。

### 9.3 与现有 RAG 的兼容

初版可以在现有 `build_memory_index.py` 之前新增一个“编译事实候选”阶段；现有 `build_hybrid_index()`
继续负责已授权证据和 confirmed cards 的索引。事实路由可以先作为独立模块，不要求立即修改所有 Ray
Serve ingress。

当事实路由和索引通过验证后，再把它接入推理入口。每次改变路由、别名、prompt 或索引输入，都必须产生
新的 preprocessing/config digest 和不可变 Release；不能原地替换线上索引。

## 10. 隐私、安全和授权

### 10.1 抽取范围

- GPT 只能接收明确授权 scope 覆盖的消息；
- 外部 API 默认不接收未脱敏原文；若授权不允许外发，则使用本地受控抽取模型；
- stable placeholder 可保留跨消息指代能力，但不保留真实姓名、电话、账号、邮箱和源路径；
- 日级 bundle、抽取响应和 quote 都属于私有派生数据，不能进入 Git、普通日志、Issue、截图或公开 MLflow 参数。

### 10.2 抽取结果安全

- 所有聊天内容都按不可信数据处理；
- quote 必须受长度上限约束，并重新执行原子和跨消息隐私扫描；
- 模型返回的链接、命令和系统提示只作为文本，不执行；
- GPT 失败、超时、schema 错误或证据不匹配时，候选进入隔离区，不降级成“未知事实”；
- 禁止模型生成文本自动写回 memory。

### 10.3 撤回和删除

每条抽取结果、事实卡和日摘要必须能通过 lineage 定位到源消息和 session。撤回某条消息、会话或 consent
scope 时：

1. 让相关候选、ledger group、confirmed card 和 day summary 失效；
2. 重建 fact index 和 RAG index；
3. 写入 deletion receipt 和残留计数；
4. 让依赖该数据的训练快照、adapter、评估证据和原型进入失效/重训流程；
5. 重新扫描新索引，确认 residual count 为零。

## 11. 版本、血缘和幂等性

### 11.1 Manifest 必须记录

每个 daily memory snapshot 至少记录：

- `dataset_id`、原始 source manifest SHA-256、normalized manifest SHA-256；
- consent 文件 digest、用途 scope 和授权状态；
- `timezone`、分桶规则、session 版本和消息 schema 版本；
- redaction/scanner 版本；
- day bundle 数量、消息数、session 数和 token 统计；
- chunk 策略、tokenizer revision、邻日窗口和 overlap 配置；
- extractor provider、模型 immutable revision、prompt/schema digest、temperature；
- fact resolver 版本、alias registry 版本和日期 parser 版本；
- extraction、ledger、compiled card 和 index 文件 digest；
- code revision、运行时间、失败块和重试次数；
- `training_run=false`、`creates_mlflow_run=false`（仅做 RAG 预处理时）。

### 11.2 幂等键

抽取块的幂等键建议为：

```text
sha256(
  source_manifest_sha256,
  owner_scope,
  day_id,
  session_id,
  chunk_ordinal,
  chunk_input_digest,
  extractor_model_revision,
  prompt_digest,
  schema_version
)
```

重试只能写入相同输入和版本对应的结果；不得覆盖不同版本结果，也不得把部分完成的日快照声明为可用。
同一 snapshot 的发布使用临时目录和 no-replace/原子 rename。

### 11.3 训练边界

GPT 抽取本身不更新模型参数，但其输出如果被用于 memory-grounded SFT、LoRA、baseline、Trial 或
Champion 数据，就成为训练输入：

- 必须先完成人工审核和 formal dataset 门禁；
- 记录新的 preprocessing/schema/manifest digest；
- 若项目声明 Ray，任何训练 Run 必须走 immutable Release → Galatea readiness → 固定 Ray Driver；
- 不能把本地抽取结果或本地训练结果包装成 governed evidence；
- candidate 选择只能使用 train/validation，final test 只能在候选冻结后一次性使用。

## 12. 评估协议

评估必须把“抽取正确”和“回答正确”分开，不能只看模型主观回答质量。

### 12.1 离线抽取指标

- `day_coverage`：成功完成的日 bundle / 计划日 bundle；
- `chunk_coverage`：成功完成的 chunk / 计划 chunk；
- `truncation_rate`：必须为 0；任何超限块应显式失败；
- fact/event precision、recall、F1；
- `event_date_exact_match`：标准化到日的准确率；
- 相对时间解析准确率；
- quote/evidence 支持率；
- `conflict_precision`、`conflict_recall`；
- 重复候选率和无证据候选率；
- 外部抽取调用失败率、重试率、单位日成本。

### 12.2 在线检索和回答指标

固定一套不使用最终测试数据的 challenge set，覆盖：

- “哪天表白”“表白是哪一天”“什么时候确定关系”等别名问法；
- 同义改写、口语、省略主语和错别字；
- 跨天回顾和相对日期；
- 多日期冲突；
- 无证据和过期事实；
- 否定、假设、第三方和 prompt injection；
- owner 隔离和删除后的残留查询。

至少记录：

- fact route 命中率；
- confirmed date exact accuracy；
- candidate/conflict 正确拒答率；
- evidence support rate；
- no-evidence uncertainty rate；
- `Recall@k`、MRR 和 p50/p95 延迟；
- owner isolation 必须为 100%；
- PII/canary 泄漏必须为 0。

建议第一版起始门槛为：`truncation_rate=0`、证据支持率不低于 0.98、owner isolation=1.0、
PII/canary=0；日期准确率和冲突召回率需先用人工 gold set 建立基线，再决定正式放行值。

### 12.3 对照实验

至少比较以下四种方案，并使用相同的冻结查询集和证据定义：

1. 当前 session/chunk 规则抽取；
2. 日级 chunk 规则抽取；
3. 日级 GPT 抽取 + 结构化归并；
4. 日级 GPT 抽取 + 结构化归并 + 精确事实路由。

比较时分别报告召回、日期准确率、冲突识别、成本、延迟和隐私指标。不能只用最终生成文本的流畅度
判断方案优劣。

## 13. 实施拆分

### Phase 0：只读设计和 fixture

- 新增 schema fixture 和不含真实私密文本的测试样例；
- 实现日期标准化、日分桶、token 计数、chunk 边界和 lineage digest 的纯函数；
- 验证跨时区、跨午夜、空日、超长日和重复消息；
- 保持 `training_run=false`，不调用真实 GPT，不写生产索引。

### Phase 1：日级 bundle 生成

- 新增 `daily_memory.py` 或等价项目模块；
- 新增 `scripts/prepare_daily_memory.py --plan/--build`；
- `--plan` 只读输出日数、token 分布、超长日和预计调用量；
- `--build` 只在授权私有目录生成不可变 bundle，并产出 manifest/report；
- 不改变现有 SFT dataset 和 split。

### Phase 2：受控 GPT 抽取

- 新增严格 JSON Schema 请求封装和响应校验；
- 外部调用使用显式模型 revision、温度、prompt/schema digest 和授权 scope；
- 失败块隔离、可重试、可审计；
- 输出只允许 `candidate`，不直接修改 `memory-card-v1`；
- 与现有规则 extractor 做 validation-only 对照。

### Phase 3：事实账本和审核

- 使用 `fact_resolution.py` 和 `scripts/compile_confirmed_facts.py`；
- 实现日内去重、跨天归并、日期 parser、别名 registry 和冲突报告；
- 审核 UI 按 `fact_key` 聚合显示候选和全部证据；
- 用户确认后调用现有 memory card compiler，生成 confirmed cards。

### Phase 4：双索引和查询路由

- 新增 fact index reader，先在本地 fixture 和受控 inference-only 流程验证；
- 将已审核 cards 交给现有 `build_memory_index.py`；
- 保留 evidence/RAG index 处理开放式问题；
- 接入 Ray Serve ingress 前，完成 owner、冲突、无证据和删除验收；
- 任何服务配置变化都建立新的 Release，不原地修改已发布配置。

### Phase 5：是否用于训练

- 默认不把事实卡训练进 LoRA；事实优先留在可删除的 RAG；
- 若确实需要 memory-grounded SFT，先生成脱敏且已审核的训练 snapshot；
- 使用项目声明的 governed Ray 训练路径，不使用本地全量训练或通用 Ray 提交；
- 对 Base、Prompt-only、RAG、LoRA 和 RAG+LoRA 使用同一冻结协议比较。

## 14. 现行代码和后续扩展

以下映射包含已有实现和后续可选扩展：

| 类型 | 建议位置 | 作用 |
| --- | --- | --- |
| 日分桶/分块 | `src/wechat_persona/daily_memory.py` | 日 bundle、token budget、邻日窗口、幂等键 |
| GPT 抽取 | `src/wechat_persona/fact_extraction.py` | 请求、严格 schema、响应校验、失败隔离 |
| 时间归一化 | `src/wechat_persona/temporal.py` | 绝对/相对日期和事件日期解析 |
| 跨天归并 | `src/wechat_persona/fact_resolution.py` | fact ledger、冲突和证据合并 |
| 确定性查询 | `src/wechat_persona/fact_query.py` | alias → fact_key → confirmed/candidate 路由 |
| 日 bundle schema | `schemas/daily-bundle.schema.json` | 输入契约 |
| 抽取 schema | `schemas/fact-extraction.schema.json` | GPT 输出契约 |
| 账本 schema | `schemas/fact-ledger.schema.json` | 跨天审核契约 |
| 预处理入口 | `scripts/prepare_daily_memory.py` | 日级计划/生成 |
| 归并入口 | `scripts/compile_confirmed_facts.py` | 编译已明确接受的事实并构建私有索引 |
| 索引入口 | `scripts/build_memory_index.py` | 消费 confirmed cards；禁止绕过审核 |
| 项目测试 | `tests/test_daily_memory_*.py` | 分桶、截断、时间、冲突、幂等和隐私测试 |

实现时不得把这些 workload-specific 逻辑移动到仓库级平台模块。

## 15. 验收清单

### 数据和分桶

- [ ] 使用固定 IANA 时区，跨午夜和夏令时行为有测试。
- [ ] `conversation_date` 与 `event_date` 分开保存。
- [ ] day/session/chunk 的消息顺序、speaker、message ID 可复现。
- [ ] 超长日按完整 turn/session 分块，`truncation_rate=0`。
- [ ] 相对日期无法解析时不会自动猜测。

### GPT 抽取和事实账本

- [ ] 所有抽取结果严格通过 JSON Schema。
- [ ] 每个候选都有 evidence ref，quote 能在原文中定位。
- [ ] 模型输出中的命令、链接和提示不会被执行。
- [ ] 同一 `fact_key` 的多个值全部保留并生成冲突报告。
- [ ] 只有人工审核才可生成 `confirmed` card。
- [ ] 规则 baseline 与 GPT 版本可以独立比较。

### 隐私和治理

- [ ] 事实抽取保留日期，但不保留不必要的直接身份标识。
- [ ] 日 bundle、GPT 请求/响应和 quote 都在授权私有范围内。
- [ ] consent digest、source/dataset/preprocessing/model/schema digest 完整记录。
- [ ] 重试不会覆盖其他版本，也不会发布部分完成快照。
- [ ] 撤回可定位并删除/失效所有 day、fact、card、index 和下游工件。
- [ ] 若输出进入训练，具备 formal snapshot、Release、readiness 和 governed Ray 证据。

### 查询和服务

- [ ] “哪天表白”等问法走 fact index，不依赖普通 top-k 文本猜测。
- [ ] confirmed、candidate、conflicted、无证据四种结果对外语义不同。
- [ ] 精确日期只允许由结构化查询返回，LLM 只能包装语气。
- [ ] 开放式回忆仍保留 evidence/RAG 证据边界。
- [ ] owner isolation=100%，PII/canary leakage=0，删除后 residual=0。

## 16. 第一版建议决策

建议先按以下默认值落地，之后通过 validation evidence 调整：

1. 日级主分区，天内保留 session 边界；
2. 默认邻日窗口 ±1 日，无法解析相对日期时最多扩展到 ±3 日；
3. 超长日按 session/完整 turn 分块，token hard limit 超出则阻断；
4. GPT 只做候选抽取，不做事实确认；
5. `relationship.started_at` 作为第一个事实 key，先覆盖表白/在一起/确定关系别名；
6. 人工审核按 fact group 批量进行，冲突全部展示；
7. confirmed fact index 与 evidence/RAG index 双轨保存；
8. 先修复记忆链路，再评估 3B/7B 基座；换基座不能直接复用旧 LoRA；
9. 事实抽取结果默认不用于 LoRA 训练，除非另行通过 governed training 流程；
10. 当前服务继续保持 `promotable=false`，直到事实准确率、冲突处理、隐私和删除验收全部通过。
