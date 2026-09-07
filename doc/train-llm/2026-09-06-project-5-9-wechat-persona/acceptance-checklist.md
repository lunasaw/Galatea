# 项目 5–9 验收清单

> 勾选代表证据已由受控系统产生并可按 ID/API 回读；代码存在或本地脚本成功不等于通过。

## A. 统一治理门（所有项目）

- [ ] 项目根、`README.md`、`configs/`、`src/`、`scripts/`、`tests/` 和 `conda.yaml` 存在。
- [ ] `galatea.project.yaml` 声明 Ray backend、固定 Driver、objective metric/direction 和资源。
- [ ] 所有 Training Run 都有 Dataset Snapshot、consent/role、Release、readiness、Ray Job、MLflow Run。
- [ ] config/source/manifest/split/model/tokenizer/code/environment/seed/resource digest 齐全。
- [ ] 失败重试使用新 Run/attempt，并有 `retry_of` 或 `resumed_from`，不覆盖成功工件。
- [ ] Artifact API 下载、SHA-256 和新进程加载通过；无服务端 MinIO/`mlflow.db` 直读。
- [ ] validation-only 选参、candidate freeze 和 test-once 证据可回读；无 test 泄漏。
- [ ] 撤回可定位 raw、派生数据、index、Run、checkpoint、adapter、备份并完成删除/失效。

## B. 项目 5：微信数据工程

- [ ] consent ledger 覆盖处理、风格、RAG、评估用途和媒体/第三方范围；验证未过期。
- [ ] TXT/CSV/JSON/HTML importer 通过流式、错误定位、源 hash 和 symlink escape 测试。
- [ ] 标准消息 schema、角色映射、时间/顺序和媒体策略报告通过。
- [ ] 上下文和目标均执行版本化脱敏；二次 PII/secret/canary 扫描为零硬泄漏。
- [ ] session 合并和 chronological/group split 稳定；零跨 split session/近重复/未来消息。
- [ ] 审核 100% 结案；`uncertain` 为 0；`redact_keep` 有修改 hash/原因/审核者/时间。
- [ ] 正式 `train.jsonl`、`validation.jsonl`、`test.jsonl` 非空，manifest 与 lineage 可复算。
- [ ] 删除一个 session 和一个 consent scope 的 dry-run/execute/verify 均通过。

## C. 项目 6：RAG

- [ ] memory card 含 owner、来源、时间窗、sensitivity、status、revision 和人工确认状态。
- [ ] BM25 baseline 在冻结 query set 上有 Recall@K/MRR、延迟和 owner isolation 报告。
- [ ] embedding 使用固定本地 revision/pooling；reranker 仅在 validation 证据支持时启用。
- [ ] 无证据、过期、冲突和第三方敏感场景遵守不确定/拒答策略。
- [ ] 删除 card/session/scope 后检索残留率为 0，index manifest/digest/receipt 齐全。
- [ ] Prompt-only/RAG 输入、生成和 test-once 协议一致，证据不混淆。

## D. 项目 7：真实角色 LoRA

- [ ] 真实 SFT 仅使用已审核、已脱敏、已授权的 target 回复；事实进入 RAG 而非风格标签。
- [ ] 2-sample/2-step preflight、10-step smoke、1 epoch baseline 均经 governed Ray Driver。
- [ ] 五组 Base/Prompt-only/RAG/LoRA/RAG+LoRA 的 protocol、prompt、index、seed 和输入一致。
- [ ] 至少 100 个匿名配对盲测；`LoRA_vs_PromptOnly_win_rate >= 0.60` 或明确证据不足。
- [ ] PII/canary、不安全行为、冒充真人、排他依赖和内疚操纵硬门全部通过。
- [ ] `RAG+LoRA` 事实正确率相对 `LoRA` 至少 +5 个百分点，或记录未达标停止决定。
- [ ] candidate freeze、test-once、Artifact round-trip、人工 review 和安全报告齐全。

## E. 项目 8：容量/QLoRA

- [ ] 1.7B BF16 LoRA 在与 0.8B 相同冻结数据/protocol 下完成兼容比较。
- [ ] 主指标提升 ≥5 个百分点（或预先声明的等价差异），且资源代价在预算内。
- [ ] 4B QLoRA 仅在容量瓶颈书面确认后开始；量化库/GPU/runtime preflight 通过。
- [ ] 量化配置、bitsandbytes/Transformers/PyTorch/CUDA revision、显存和吞吐证据齐全。
- [ ] 采用/停止决定独立记录，不能以参数量或单一辅助分数替代。

## F. 项目 9：本地原型/脚本

- [ ] 原型只加载 `quality_evidence_status=accepted`、人工审核通过且未撤回的候选/索引。
- [ ] 聊天入口显示 AI 身份，支持关闭记忆、清除日志、owner 隔离和安全降级。
- [ ] PII/canary、未授权事实、冒充/依赖和 prompt injection 运行门通过。
- [ ] 事件卡含事实、关系阶段、情绪转折、来源 ID、置信度和审核状态。
- [ ] 脚本每个事实可追溯至事件卡/已授权 session；双方可编辑/删除并验证删除效果。
- [ ] 时间码、阶段顺序和改写规则通过；无声音/肖像/自动发消息资产。
- [ ] 上游撤回或 digest 变化会自动禁用入口并保留删除账本。

## G. 当前验收结论

在 2026-09-06 的仓库状态下，项目 5–9 应标记为 `implementation_complete /
formal_evidence_blocked`。代码、配置、契约测试和只读检查已完成；不能因为已有
`wechat_preprocess.py`、`memory.py`、私有 Trial 或本地 baseline 报告而勾选正式验收项。
