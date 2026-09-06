# 项目 5：微信聊天数据工程

## 1. 目标和明确不做的事

目标是把合法导出的微信 TXT/CSV/JSON/HTML 转成可审计的脱敏 session 数据、人工审核候选、正式
SFT 数据、记忆候选和事件卡。项目 5 不训练模型、不调用外部在线模型、不读取或破解加密数据库，
也不因有一份导出文件就推断双方已同意角色风格学习。

当前仓库的 `wechat_preprocess.py`、`export_review_candidates.py` 和已有处理计划可作为纯函数及
报告格式的起点，但需要迁移进 `train-model/wechat-persona/src/wechat_persona/`，并增加授权、
撤回、人工审核和正式导出门禁。

## 2. 输入、授权和保管

### 2.1 Consent ledger

导入前必须存在受控 ledger 记录（见 [`schemas/consent.schema.json`](schemas/consent.schema.json)）：

- `consent_id`、主体的不可识别 ID 和成年人确认状态；
- 用途：`processing`、`persona_style`、`memory_rag`、`evaluation` 分开授权；
- 消息/媒体类型、目标时间范围、保留期限、可见范围和撤回标识；
- 目标角色风格授权及第三方内容策略；
- 授权签署/验证时间、ledger 版本和验证者引用。

训练客户端只接受 ledger 的签名/摘要引用，不接受在 YAML 中写“已同意”的布尔值。范围不足或
已过期时，状态为 `consent_missing`。

### 2.2 保管边界

原始导出和身份映射写入受控本地只读目录；标准化、脱敏和审核产物使用不可识别的 dataset ID。
不把原文写入 Git、MLflow 参数/日志、普通日志、截图、外部 API 或公开数据集。所有阶段产物采用
追加式版本目录，禁止覆盖原始文件或已有 dataset 版本。

## 3. 导入器接口和标准消息

```python
class Importer(Protocol):
    def detect(self, path: Path) -> bool: ...
    def iter_messages(self, path: Path, timezone: str) -> Iterator[RawMessage]: ...
```

实现 `text.py`、`csv.py`、`json.py`、`html.py`，统一输出 [`schemas/message.schema.json`](schemas/message.schema.json)
中的字段：`message_id`、`source_record_index`、带时区 `timestamp`、`speaker_role`、`message_kind`、
`text_redacted`、`source_hash`、`parse_status` 和受控 `source_ref`。大型文件必须流式读取；解析
错误要有行号/偏移，不得静默丢弃。源 manifest 记录完整输入 SHA-256、格式、时区、解析统计和
importer 版本。

角色映射只允许 `self`、`target`、`other`、`unknown`。`unknown` 默认排除；群聊、转发和第三方
内容若无额外授权不得混入双人 SFT。

## 4. 脱敏、过滤和 session

处理顺序固定为：Unicode/时间规范化 → 消息类型过滤 → 发送者映射 → 上下文/目标同时脱敏 →
连续消息合并 → session 切分 → 候选生成。规则版本化并运行二次扫描。

至少替换联系方式、证件/支付标识、密码/验证码/token、精确地址/坐标、账号、第三方姓名和内部
链接参数；使用稳定标签如 `<PII_CONTACT>`、`<SECRET>`、`<PRIVATE_LOCATION>`。图片、语音、视频
首版仅在单独授权且能安全生成语义占位符时保留，否则排除。

session 由不活动间隔、最大持续时间和主题/引用边界决定。同一发送者在短间隔内的连续消息合并，
保留全部原始 message ID。目标回复只能看到目标之前的上下文，不能把未来消息或后续修订带入输入。

## 5. 候选、人工审核和正式导出

候选行使用 [`schemas/candidate.schema.json`](schemas/candidate.schema.json)，初始状态只能是
`uncertain`。审核者在脱敏界面逐条选择：

- `keep`：无修改通过；
- `redact_keep`：记录修改后内容哈希、原因、审核者和时间；
- `reject`：记录可分类原因；
- `uncertain`：需复核，永不进入训练。

审核抽样必须覆盖时间阶段、短/长回复、提问、安慰、调侃、拒答、结束、媒体占位和高风险类别。
机器质量分不能替代人工结论。正式导出只读取 `keep`/`redact_keep`，再次运行 schema、PII、
未知角色、未来信息、重复和 split 门禁。

## 6. Split 和数据对象

按完整 session（必要时按 scenario/近重复组）做 deterministic chronological split，默认 80/10/10。
输出 `split_manifest.json`、每个 split 的样本/会话计数和 SHA-256；任何 session、近重复组或模板
变体跨 split 都阻断。训练集、validation、test 和 challenge set 分离：test 在 candidate freeze
前不可读。

SFT 样本最后一条必须是 `target` 的 assistant 回复，且 `assistant_only_loss=true`。事实性内容优先
变成 memory card，不把全部共同经历当作风格标签。

## 7. 撤回和删除闭环

`deletion.py` 接收 `consent_scope`、主体/来源 session 或 source message ID，生成计划并执行：

```text
raw -> normalized/redacted -> candidates -> datasets
    -> memory cards/index -> event cards
    -> MLflow runs/checkpoints/adapters -> backups
```

每个对象通过 lineage manifest 定位；删除账本只保存对象 ID、范围、时间、结果和校验摘要，不保存
被删除正文。若数据进入 adapter，必须使受影响 adapter 失效并从清理后的数据重新训练，不能声称
“只删 JSONL 即完成撤回”。删除后重新运行检索、canary 和数据摘要检查。

## 8. 实现任务和验收

| 任务 | 主要接口/产物 | 验收 |
|---|---|---|
| 5.1 consent | `verify_consent()`、ledger digest | 缺范围/过期/第三方授权时 fail-closed |
| 5.2 import | 四种 importer、source manifest | 流式、源哈希、错误可定位、无 symlink escape |
| 5.3 normalize/redact | `normalize_message()`、redaction report | 二次扫描零硬泄漏，规则有版本 |
| 5.4 session/split | `sessionize()`、split manifest | 零跨 session/split、未来消息不泄漏 |
| 5.5 review/export | review event log、正式 JSONL | 每条有人工结论；空 split 或 uncertain 时阻断 |
| 5.6 deletion | deletion plan/ledger | 可反向列出并确认所有下游对象 |

项目 5 通过条件：`authorization_status=verified`、三份正式 SFT 非空、人工审核全结案、隐私/血缘/
split 报告通过、撤回演练成功。未满足前只能保持 `review_only`/`blocked_for_formal_training`。

