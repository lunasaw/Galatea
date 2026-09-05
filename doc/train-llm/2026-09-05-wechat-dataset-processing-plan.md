# 微信对话数据集处理与训练前置方案

## 1. 本次执行结论

本次不把现有候选池伪装成已批准 SFT 数据。当前 `data-deal` 目录里已经有一轮
脱敏和候选构造结果，但授权记录尚未在流水线中核验，人工审核队列的 5,000 条记录全部为
`uncertain`，正式 `datasets/{train,validation,test}.jsonl` 为空。因此本次只生成可审计的
审核用分层候选快照：

```text
/data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964/review_exports/v1/
├── train_candidates.jsonl
├── validation_candidates.jsonl
├── test_candidates.jsonl
├── review_manifest.json
└── README.md
```

这些文件只用于本地人工审核和抽样检查，不能作为训练输入。审核通过并重新通过全部门禁后，
才允许写入同一数据集下的 `datasets/` 目录。

## 2. 已知数据状态

| 项目 | 当前值 | 解释 |
| --- | ---: | --- |
| 脱敏消息 | 295,202 | 已生成的标准化/脱敏消息 |
| session | 4,505 | 按 `chronological_session` 切分 |
| 候选池 | 60,426 | 去除无上下文、超长等不合格目标后的唯一候选 |
| 审核快照 | 5,000 | 机器筛选后的候选规模 |
| 候选 split | 3,776 / 759 / 465 | train / validation / test，按完整 session 分配 |
| 审核状态 | 5,000 `uncertain` | 没有任何自动批准 |
| 正式 SFT 文件 | 0 / 0 / 0 | 有意为空，阻断正式训练 |
| 授权状态 | `not_verified_in_pipeline` | 必须补齐受控 consent ledger 引用 |

现有原始导出文件未出现在当前工作区；如果需要从源头重跑，必须先交付只读原始文件的受控
路径，并记录源文件 SHA-256。不能用 `normalized/messages.jsonl` 反推或伪造原始导入阶段。

## 3. 目标产物与状态机

```text
原始导出（只读）
  -> 标准化消息
  -> 脱敏消息
  -> session
  -> 候选池
  -> review_exports（本次生成，review_only）
  -> 人工审核 keep / redact_keep / reject / uncertain
  -> 质量、隐私、授权、泄漏门禁重跑
  -> datasets/train.jsonl、validation.jsonl、test.jsonl
  -> 训练前小样本 smoke
  -> 正式训练
```

状态转换规则：

- `uncertain` 只能由审核者改为 `keep`、`redact_keep` 或 `reject`；机器分数不能完成转换。
- `redact_keep` 必须保存修改后的内容哈希、修改原因和审核者/时间信息，不覆盖原候选。
- 只有 `keep` 和 `redact_keep` 可以进入正式 SFT 导出；`reject`、`uncertain` 永不进入。
- 任何授权撤回都会使受影响的候选、数据集、Run、adapter 和索引失效并进入删除账本。

## 4. 分阶段处理方案

### 阶段 A：授权和数据保管

1. 在受控目录建立 consent ledger，不把授权正文写入 Git、日志或 MLflow。
2. 至少记录授权主体、用途、目标角色风格授权、允许的消息/媒体类型、保留期限、撤回标识和
   当前数据集 ID。
3. 原始导出、身份映射和未脱敏中间文件只读保存；训练客户端只读取脱敏派生物。
4. 在源 manifest 中将 `authorization_status` 更新为真实可验证的状态，不能手工把
   `not_verified_in_pipeline` 改成 `verified`。

**门禁：**授权引用可由受控系统核验，范围覆盖聊天处理和目标角色风格学习；否则停止。

### 阶段 B：导入和完整性

1. 交付原始 JSON/CSV/TXT/HTML 中的一种明文导出，明确格式和时区。
2. 计算完整源文件 SHA-256；输入路径只读、拒绝 symlink escape。
3. 流式导入，保留源记录索引、消息 ID、解析错误位置和输入顺序。
4. 生成 `source_manifest.json`、`import_report.json`，解析错误必须清零或显式豁免。

**门禁：**源哈希、数据集 ID、配置哈希和处理版本可复算；原始文件不被修改。

### 阶段 C：标准化、脱敏和媒体策略

1. 统一时间、Unicode、空白和消息类型；发送者映射为 `self/target/other/unknown`。
2. 对上下文和目标同时执行联系方式、证件、支付标识、凭据、位置、账号和第三方信息脱敏。
3. 系统、支付、通话、文件和未经单独授权的媒体默认排除；有限表情只保留语义占位符。
4. 执行二次扫描，报告只保留计数和类别，不保留完整私人正文。

**门禁：**`unknown` 不进入 SFT；支付/凭据/原始媒体不进入 SFT；二次扫描通过。

### 阶段 D：session、候选与去重

1. 先按时间排序，以不活动间隔和最大 session 时长切分。
2. 合并同一发送者的短时间连续消息，保存完整 message ID 列表。
3. 对每个目标角色回复只取同一 session、目标之前的上下文；目标之后的信息绝不进入输入。
4. 生成候选机器分数、重复组和风险原因；近重复折叠不跨 session 泄漏。
5. 按完整 session 冻结时间切分，输出 `split_manifest.json` 和内容 digest。

**门禁：**session 不跨 split；未来消息不在输入；目标角色唯一；重复组不跨 split。

### 阶段 E：本地人工审核

审核页面或审核工具只显示脱敏内容和非身份化 ID。每条记录至少检查：

- 上下文是否足够且连贯；
- 目标回复是否确实来自 `target`；
- 脱敏是否完整；
- 是否存在第三方、秘密、支付、定位或媒体依赖；
- 回复是否代表希望学习的风格/行为，而不是需要进入 RAG 的事实记忆；
- 是否重复、过短、过长或明显无意义。

审核结果必须结构化记录：`keep`、`redact_keep`、`reject`、`uncertain`，并记录拒绝/修改原因。
审核抽样要覆盖时间阶段、session 长度、长短回复、提问/安慰/调侃/拒绝/结束等行为类型。

### 阶段 F：正式 SFT 导出

1. 只读取审核状态为 `keep` 或 `redact_keep` 的候选。
2. 按冻结的 session 映射写入 `datasets/train.jsonl`、`validation.jsonl`、`test.jsonl`；
   不按单条消息重新随机切分。
3. 重新执行 schema、隐私二次扫描、未知发送者、目标角色、未来信息、重复组和跨 split 检查。
4. 为每个文件和整个数据集生成 SHA-256，并记录上游候选、审核版本、配置和 consent scope。
5. 任何一个 split 为空或质量报告仍为 blocked，都不得开始正式训练。

### 阶段 G：训练前和训练

1. 先用少量审核通过样本执行 tokenizer chat template、assistant-only loss、LoRA 注入和
   checkpoint 恢复 smoke。
2. 记录模型 revision、tokenizer revision、代码提交、数据/manifest digest、随机种子、环境和资源。
3. 正式训练使用独立的真实数据项目配置，不复用项目 2–4 的 Toy 数据配置或数据目录。
4. MLflow 通过 Tracking/Artifact API 记录 Run 和工件；Ray Driver 是唯一父 Run owner。
5. 测试集只在候选冻结后评估一次；模型 alias 变更需要单独人工批准。

## 5. 本次导出执行与核验

可复现命令：

```bash
python doc/train-llm/export_review_candidates.py \
  --dataset-root /data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964
```

导出器会拒绝：缺少 split 映射、重复 sample、未知角色、原始文本引用、非法审核状态、符号链接
逃逸或覆盖已有快照。导出结果必须检查：

```bash
jq '{export_status,formal_training_eligible,blocking_reasons,candidate_count,candidate_status_counts,split_counts,split_session_counts}' \
  /data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964/review_exports/v1/review_manifest.json
```

预期结果是 `export_status=review_only`、`formal_training_eligible=false`、5,000 条候选，
split 计数为 `3776/759/465`，并保留三个正式阻断原因。正式 `datasets/*.jsonl` 应继续为空。

## 6. 当前阻断项和解除条件

| 阻断项 | 证据 | 解除条件 |
| --- | --- | --- |
| 授权未核验 | `source_manifest.authorization_status=not_verified_in_pipeline` | 受控 ledger 引用和范围校验通过 |
| 人工审核未完成 | `review_report.status_counts.uncertain=5000` | 审核完成且每条有结论/原因 |
| 正式 SFT 为空 | 三个 `datasets/*.jsonl` 为 0 字节 | 导出至少一批批准样本并重跑门禁 |
| Qwen3.5 环境兼容性 | 当前共享环境不能识别 `qwen3_5` | 使用支持该架构的项目环境完成加载 smoke |
| LoRA 依赖 | 当前环境未安装 `peft` | 项目环境安装并锁定兼容版本 |

在这些条件完成前，允许继续做审核、报告和数据质量工作；不允许启动真实 LoRA 训练、创建正式
MLflow Run 或发布模型 alias。

## 7. 删除与撤回

删除按 `consent_scope -> source message -> session -> candidate -> dataset -> run/checkpoint/adapter`
反向追踪。删除账本只写对象 ID、范围、时间和结果，不写被删除正文。若数据已经进入 LoRA
adapter，不能只删除 JSONL；必须将受影响 adapter 标记不可用、删除受控副本，并从清理后的数据
重新训练。
