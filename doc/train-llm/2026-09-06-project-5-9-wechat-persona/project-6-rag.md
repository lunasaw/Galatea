# 项目 6：关系记忆 RAG

## 1. 目标和边界

RAG 负责可更新、可过期、可删除和可追溯的关系事实；LoRA 负责语言风格。项目 6 不把记忆全文
写入模型参数，不在索引中默认保存高敏内容，不向外部 embedding/LLM 服务发送私人原文。

第一阶段同时实现可复现的 BM25-like 检索和本地 embedding 检索；embedding 不训练。只有在冻结
查询集上 BM25 的 Recall@K 不足时，才评估本地 reranker。所有索引构建和评测都记录 owner namespace、
memory manifest digest、embedding model revision 和 index version。

## 2. 记忆卡数据契约

每张记忆卡符合 [`schemas/memory-card.schema.json`](schemas/memory-card.schema.json)：

- `memory_id`、`owner_scope`、脱敏 `content`、`source_session_ids/message_ids`；
- `created_at`、`valid_from`、`valid_to`、`confidence`、`sensitivity`；
- `status`: `candidate|confirmed|superseded|deleted`；
- `fact_key`/属性、抽取器版本、人工确认记录和 lineage digest。

只允许 `confirmed`、未过期且当前 owner 的记录进入默认检索。高敏记录默认不索引；候选和已删除
记录只留在审核/删除账本中。冲突事实按 `revision_key=(valid_from, created_at, memory_id)` 稳定选最新，
必要时同时返回“不确定”信号。

## 3. 索引架构

```text
confirmed memory cards
      ├─ token normalization + owner partition -> BM25 index
      └─ local embedding model + fixed pooling -> vector index
query -> owner filter -> candidate retrieval -> optional rerank
      -> top-k evidence IDs -> grounded prompt -> answer policy
```

索引分片至少按 `owner_scope` 隔离；查询 API 必须要求 owner，不接受全局搜索。记录 `index_manifest`
（卡片 digest、计数、敏感度过滤、tokenizer/embedding revision、构建时间和代码版本）。删除采用
copy-on-write 新索引或 tombstone + compact，直到删除验证完成前不得宣称完成。

## 4. 评测协议

冻结人工标注的 query-memory 对和无答案/冲突/过期/第三方敏感/prompt injection challenge set。
在同一问题和生成协议下比较 `Prompt-only` 与 `RAG`，之后再与 `LoRA`、`RAG+LoRA` 比较。

报告至少包括：

- `Recall@1/3/5/10`、MRR、结果数分布和 p50/p95 检索延迟；
- owner 越界率、无关结果率、最新 revision 命中率；
- evidence support rate、unsupported private-claim rate、无证据不确定率；
- 删除后残留检索率和 canary/PII 泄漏率（必须为 0）。

生成文本只保存按授权允许的受控审核记录或哈希/聚合标签；普通 MLflow 日志不得包含私聊正文。
Test query 只在 candidate freeze 后一次性运行；validation query 可用于选择 k、阈值和是否加入
embedding/reranker。

## 5. 实现接口

```python
build_memory_cards(messages, consent_scope, extractor_version) -> MemoryManifest
build_bm25_index(cards, index_dir) -> IndexManifest
build_embedding_index(cards, model_ref, index_dir) -> IndexManifest
retrieve(query, owner_scope, index_ref, k, now) -> list[RetrievedMemory]
evaluate_retrieval(cases, index_ref, protocol) -> RetrievalReport
delete_memory(memory_id, owner_scope, index_ref) -> DeletionReceipt
```

`retrieve()` 复用现有 `MemoryStore` 的 owner/status/expiry 语义，但正式实现必须持久化索引清单、
删除账本和 Artifact/API round-trip 证据。外部来源 ID 只作为内部审计字段，UI 是否显示需单独开关。

## 6. 验收门

1. 100% 查询按 owner 隔离；候选、superseded、deleted、过期和高敏默认不返回。
2. 在冻结标注集上发布 Recall@K/MRR/延迟报告，查询集和 embedding revision 可复现。
3. 无相关记忆时模型遵循“不知道/不确定”，不得用常识补全私人事实。
4. 删除一张卡、一个 session 和一个 consent scope 后，检索不到受影响卡；索引重建 digest 变化可追踪。
5. Prompt-only 与 RAG 使用完全相同输入和生成参数，不能把 RAG 的收益归因给 prompt 或 LoRA。
6. PII/canary、第三方敏感信息、prompt injection challenge 全部通过；失败保持 `blocked`。

