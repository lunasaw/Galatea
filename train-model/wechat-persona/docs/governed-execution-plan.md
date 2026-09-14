# 微信 Persona 数据到正式训练：完整执行计划

> 这是数据治理阶段的详细执行附录。项目 5–9 的主导航是
> [`docs/README.md`](README.md)；本文件不能单独授权训练。

## 0. 目的和当前结论

本文是一份可以照着执行的运行手册，说明如何把微信 Persona 数据从“待审核历史版本”
推进到“经过治理、可以开始正式训练”的状态。

本次只写文档，不修改数据目录、不修改旧版本、不生成 reviewed.jsonl、不创建 MLflow Run，
也不启动 Ray 或模型训练。

当前版本必须继续保持：

~~~text
dataset_status=review_only
formal_training_eligible=false
superseded=false
privacy_status=blocked_derived_chain
~~~

旧版本目录：

~~~text
/data/ai/chenzhangyue/code/data/wechat-deal/wechat_2234f547cf224b33730f
~~~

正确顺序：

~~~text
冻结旧版本
→ 修复全链路隐私扫描和 manifest 语义
→ 从原始导出重新生成新的不可覆盖版本
→ 核验 consent、计数、split、lineage、权限和隐私
→ 100% 审核明确范围内的候选
→ 编译 reviewed.jsonl
→ 生成正式 SFT snapshot
→ 生成 FORMAL_DATASET_READY 证据
→ immutable Release
→ Galatea readiness / execution authorization
→ 2-sample/2-step preflight
→ 10-step governed Ray smoke
→ validation-only baseline/Trial
→ candidate freeze
→ test-once
→ 显式 promotion
~~~

任一阶段没有通过，都不能跳到下一阶段。

## 1. 名词解释

| 名词 | 简单解释 |
| --- | --- |
| 原始导出 | 从聊天工具导出的原始文件，只能读不能改。 |
| 脱敏 | 把电话、密码、地址等换成占位符。 |
| session | 一段连续聊天；按 session 切分可避免上下文泄漏。 |
| candidate | 可能适合学习的上下文加目标回复，尚未人工批准。 |
| review event | 审核人对一个 candidate 的决定和证明，只保存 ID、hash、状态。 |
| manifest | 数据清单，记录来源、数量、版本和摘要。 |
| SHA-256/digest | 文件指纹；内容改变，指纹也会改变。 |
| formal snapshot | 完成人工审核和全部门禁后的正式 SFT 快照。 |
| immutable Release | 封存的代码、配置和环境包。 |
| Galatea readiness | 训练前检查数据、代码、资源和授权的总检查单。 |
| Ray Driver | 唯一允许启动正式训练并创建 MLflow Run 的固定入口。 |
| MLflow Run | 一次正式实验记录。 |
| test-once | 候选冻结后只使用测试集一次。 |

## 2. 项目执行边界

项目声明文件：

~~~text
train-model/wechat-persona/galatea.project.yaml
~~~

关键配置：

~~~yaml
spec:
  task: causal-language-model-sft-lora
  executionBackend: ray
~~~

以下动作都算 Training Run，必须经过 immutable Release、Galatea 和固定 Ray Driver：

- 更新模型参数或 LoRA adapter；
- 跑完一个 epoch 或真实数据的重要部分；
- 生成 checkpoint、adapter、模型或可恢复状态；
- 生成用于 baseline、Trial、tuning 或候选证据的持久 MLflow Run；
- 名称叫 smoke、baseline、Trial、retraining 或 Champion 的运行。

可以本地做的事情仅限于只读或 forward-only：

- 查看 manifest、配置、文件摘要；
- 校验 consent；
- 重新计算 SHA-256；
- schema、隐私、split、lineage 测试；
- tokenizer、loss mask、张量形状和模拟 checkpoint 测试；
- 不产生训练证据的有限 inference-only 检查。

experimental_only、promotable=false、formal_training_eligible=false、私有或合成数据，
都不能成为本地训练的例外。

## 3. 当前已知历史事实

以下数字来自旧版本，仅作历史参考，不能直接当作新版本证据：

| 项目 | 历史值 |
| --- | --- |
| 原始文件 | /srv/galatea-private/wechat-persona/raw/export.json |
| 原始文件大小 | 463617699 bytes |
| 原始文件 SHA-256 | 63ce0e55db1dcefa69366db39bd752103edfb5ebf68aa1a2a10166ebc3891219 |
| 原始解析记录数 | 297500 |
| 过滤后消息数 | 226632 |
| 保留 self/target 文本数 | 226628 |
| 旧 candidate 总数 | 54981 |
| 当前 consent 文件 SHA-256 | 64ca3c9318b1aa76f7c13330f21a13be7da9b43b3fa6cbe4681a1b8b8795d186 |
| 当前 consent canonical digest | c96dee7bcad9e52806d9f760cfe85524ae06b6af20e067f481e9f6af5bd16182 |
| 旧 preflight 中的 SHA | 20ff586e...，只能作为历史诊断 |

旧版 privacy report 自己报告为零，不代表全链路已通过。新版本必须重新扫描 messages、
sessions、candidates、reviewed rows 和 formal snapshot。

## 4. 参与角色

| 角色 | 责任 |
| --- | --- |
| 数据工程负责人 | 修复扫描器、manifest、consent 校验和导入流程；生成新版本。 |
| 隐私审核人 | 在受控环境查看脱敏内容和 ID，判断 secret/PII/第三方风险。 |
| 审核负责人 | 确定审核范围、审核者、状态枚举，并确认 uncertain=0。 |
| 训练负责人 | 维护正式配置、Release、Ray Driver 和 MLflow/Artifact 证据。 |
| 平台管理员 | 登记项目、Release、readiness、授权和 Ray 资源。 |
| 最终审批人 | 签署 FORMAL_DATASET_READY，并单独决定 promotion。 |

审核内容不能复制到 Git、普通日志、截图、Issue、MLflow 参数或公开报告。

# 阶段 A：冻结旧版本

## A1. 目标

保留旧版本作为历史证据，但停止继续加工，禁止作为正式训练输入。

## A2. 操作

不要编辑旧目录。只读计算摘要：

~~~bash
OLD_DATASET=/data/ai/chenzhangyue/code/data/wechat-deal/wechat_2234f547cf224b33730f

sha256sum \
  "$OLD_DATASET/manifests/source_manifest.json" \
  "$OLD_DATASET/manifests/split_manifest.json" \
  "$OLD_DATASET/manifests/lineage.jsonl" \
  "$OLD_DATASET/reports/privacy_report.json" \
  "$OLD_DATASET/review/candidates.jsonl"
~~~

在新的受控目录保存冻结记录：

~~~text
/srv/galatea-private/wechat-persona/governance/history/
~~~

冻结记录只写状态、原因、文件摘要和时间，不写聊天原文：

~~~json
{
  "dataset_id": "wechat_2234f547cf224b33730f",
  "dataset_status": "review_only",
  "formal_training_eligible": false,
  "superseded": false,
  "privacy_status": "blocked_derived_chain",
  "reason_code": "derived_layer_privacy_not_proven",
  "recorded_at": "2026-09-07T...Z",
  "source_manifest_sha256": "...",
  "split_manifest_sha256": "..."
}
~~~

## A3. 通过标准

- 旧目录没有被修改或删除；
- 旧版本仍是 review_only；
- 冻结记录在新的受控位置可查；
- 没有因冻结动作创建 MLflow Run、checkpoint 或模型 alias。

发现旧目录被改动时：停止、记录摘要、不要继续信任旧证据，并从原始导出重建新版本。

# 阶段 B：修复代码和测试

代码没有修好前，不能直接重跑导入。

## B1. 主要修改位置

| 文件 | 任务 |
| --- | --- |
| src/wechat_persona/redact.py | 原子消息扫描、跨消息 secret 扫描、scanner version。 |
| src/wechat_persona/pipeline.py | consent purpose、计数、全链路扫描、manifest。 |
| src/wechat_persona/datasets.py | formal snapshot 完整门禁。 |
| src/wechat_persona/review.py | 审核 hash、事件字段、redact_keep。 |
| scripts/compile_reviewed.py | 完整候选或显式子集的审核证据校验。 |
| scripts/build_dataset.py | 仅在治理门全部通过后写 snapshot。 |
| tests/ | 回归测试和阻断测试。 |

## B2. 隐私扫描器

必须分两层：

1. 原子消息层：每条消息单独检测电话、邮箱、证件、支付标识、密码、验证码、token、
   API key、微信 ID、地址和经纬度。
2. 跨消息层：检测相邻的“密码标签 → 下一条疑似值”“验证码标签 → 下一条疑似值”等组合。

candidate 不能把所有正文拼成一段后直接扫描：

~~~text
self: ...
target: ...
~~~

应按结构化 turn 扫描正文。self 和 target 是角色标签，不是通过把它们加入排除名单来降低
SECRET_RE 强度。

建议提供：

~~~python
scan_atomic_text(text)
scan_adjacent_messages(messages)
scan_session(session)
scan_candidate(candidate)
~~~

扫描结果只返回对象 ID 和数字，不返回命中原文：

~~~json
{
  "object_id": "message_id_or_hash",
  "hard_leak_count": 0,
  "raw_secret_matches": 0,
  "raw_phone_matches": 0,
  "raw_email_matches": 0
}
~~~

全链路必须能统计：

~~~text
messages hard_leak_count
sessions hard_leak_count
candidates hard_leak_count
reviewed rows hard_leak_count
formal snapshot hard_leak_count
~~~

任意一层大于零都阻断。

## B3. 统一 hash 语义

统一规定：

~~~text
先规范化消息正文
再对规范化正文计算 SHA-256
~~~

审核事件区分：

~~~text
content_sha256
redacted_content_sha256   # 仅 redact_keep 需要
~~~

不要把 canonical digest 和普通字符串 SHA-256 混用而不说明含义。

## B4. consent 校验

导入 SFT 数据至少校验：

~~~python
required_purposes={"processing", "persona_style"}
required_message_types={"text"}
~~~

独立评估需要授权时，再校验：

~~~python
required_purposes={"evaluation"}
~~~

manifest 只记录 consent_id、consent_file_sha256、consent_digest 和 authorization_status=verified，
不写完整 consent JSON。

## B5. manifest 计数

新 manifest 至少包含：

~~~json
{
  "raw_message_count": 297500,
  "filtered_message_count": 226632,
  "retained_self_target_text_count": 226628,
  "excluded_by_consent": {
    "message_type": 70868,
    "time_scope": 0,
    "third_party": 0
  },
  "unknown_role_count": 0,
  "duplicate_message_id_count": 0,
  "timestamp_parse_failure_count": 0,
  "preprocessing_version": "...",
  "redaction_version": "...",
  "scanner_version": "..."
}
~~~

字段含义：

- raw_message_count：原始导入器解析出的全部记录；
- filtered_message_count：通过消息类型、时间和第三方策略后的记录；
- retained_self_target_text_count：有文本且角色为 self/target、可以进入 session 的记录。

## B6. dataset ID

新的 dataset ID 至少绑定：

~~~text
source_sha256
consent_digest
preprocessing_version
redaction_version
scanner_version
~~~

如果审核子集规则变化，还绑定 selection_version 和 selection_manifest_digest。代码变化但
dataset ID 不变化，是身份错误。

## B7. formal snapshot 门禁

build_dataset.py --execute 只有在以下全部通过后才能写新目录：

- consent purpose 和 digest 正确；
- source、manifest、split digest 对得上；
- event 数量和 candidate 范围对得上；
- uncertain=0；
- train/validation/test 都非空；
- 所有派生层隐私扫描为零；
- unknown role 为零；
- session 和 near-duplicate group 不跨 split；
- assistant_only_loss=true；
- lineage 可回溯到 source message ID；
- 输出目录不存在且不会覆盖旧 snapshot。

## B8. 必须增加的测试

~~~text
单条消息真实 secret 被发现
相邻“密码标签 + 下一条值”被发现
“密码 + self”不因角色标签产生误报
结构化 candidate context 能正确扫描
session/candidate/reviewed/snapshot 层扫描正确
manifest 三类计数正确
unknown role 会阻断
duplicate message_id 会阻断
timestamp parse failure 会被记录
dataset ID 会随 scanner/preprocessing 版本变化
pipeline 要求 processing + persona_style
正式 snapshot 不覆盖已有目录
~~~

运行测试：

~~~bash
cd /data/ai/chenzhangyue/code/galatea

/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/wechat-persona/tests \
  -p 'test_*.py'
~~~

通过标准：测试通过；没有通过排除 self/target 来降低扫描强度；没有修改旧数据；没有创建
MLflow Run。

# 阶段 C：只读准备和 consent 核验

## C1. 设置环境

~~~bash
cd /data/ai/chenzhangyue/code/galatea

source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

export PYTHONPATH="$PWD/train-model/wechat-persona/src"
export WECHAT_PERSONA_RAW_ROOT=/srv/galatea-private/wechat-persona/raw
~~~

## C2. 核验源文件和权限

~~~bash
sha256sum /srv/galatea-private/wechat-persona/raw/export.json

stat -c '%s %a %U:%G %n' \
  /srv/galatea-private/wechat-persona/raw/export.json \
  /srv/galatea-private/wechat-persona/consent/consent-c-001.json \
  /srv/galatea-private/wechat-persona/speaker-map.json
~~~

当前源文件预期摘要：

~~~text
63ce0e55db1dcefa69366db39bd752103edfb5ebf68aa1a2a10166ebc3891219
~~~

源文件必须是允许根目录中的普通文件；symlink escape 或权限异常时停止。

## C3. 核验 consent

~~~bash
PYTHONPATH=/data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python - <<'PY'
from pathlib import Path
from wechat_persona.consent import verify_consent

record = verify_consent(
    Path("/srv/galatea-private/wechat-persona/consent/consent-c-001.json"),
    required_purposes={"processing", "persona_style"},
    required_message_types={"text"},
)

print(record["authorization_status"])
print(record["consent_digest"])
PY
~~~

预期：

~~~text
verified
c96dee7bcad9e52806d9f760cfe85524ae06b6af20e067f481e9f6af5bd16182
~~~

## C4. 只读检查

~~~bash
python train-model/wechat-persona/scripts/import_chat.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --check

python train-model/wechat-persona/scripts/build_dataset.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --plan

systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
~~~

这些命令不能写正式数据，也不能创建 MLflow Run。

## C5. preflight 报告

建议实现 scripts/import_chat.py --check-source 或独立只读脚本，输出：

~~~text
source_sha256
source_size_bytes
consent_file_sha256
consent_digest
consent_id
dataset_id
preprocessing_version
redaction_version
scanner_version
raw_message_count
filtered_message_count
retained_self_target_text_count
privacy_counts_for_messages
privacy_counts_for_sessions
privacy_counts_for_candidates
~~~

preflight 不能创建 reviewed.jsonl、formal snapshot、Run、checkpoint 或 adapter。

# 阶段 D：从原始导出重建新数据版本

## D1. 选择新输出目录

例如：

~~~text
/data/ai/chenzhangyue/code/data/wechat-deal/rebuild-v2
~~~

如果目录已存在，不清空、不覆盖，改用 rebuild-v3 等新目录。

## D2. 执行导入

只有阶段 B、C 通过后才执行：

~~~bash
umask 077

PYTHONPATH=/data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/import_chat.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --execute \
  --source /srv/galatea-private/wechat-persona/raw/export.json \
  --consent /srv/galatea-private/wechat-persona/consent/consent-c-001.json \
  --speaker-map-file /srv/galatea-private/wechat-persona/speaker-map.json \
  --output-root /data/ai/chenzhangyue/code/data/wechat-deal/rebuild-v2
~~~

导入阶段只能产生新的、未审核版本：

~~~text
redacted/messages.jsonl
sessions/sessions.jsonl
review/candidates.jsonl
manifests/source_manifest.json
manifests/split_manifest.json
manifests/lineage.jsonl
reports/privacy_report.json
~~~

不能产生 reviewed.jsonl、formal snapshot、MLflow Run、checkpoint、adapter 或 model alias。

## D3. 失败处理

发生隐私、解析、权限或 manifest 错误时：

1. 保留失败目录作为诊断证据；
2. 不标记为可训练；
3. 修复代码或配置；
4. 使用新的输出版本重跑；
5. 不覆盖任何既有目录。

如果怀疑真实 secret 已进入任何派生文件，停止审核，回到原始导出重建，不要只改一条 candidate。

# 阶段 E：新版本完整核验

## E1. 目录和链接

~~~bash
NEW_DATASET_ROOT=/data/ai/chenzhangyue/code/data/wechat-deal/rebuild-v2

find "$NEW_DATASET_ROOT" -maxdepth 4 -type f -print | sort
find "$NEW_DATASET_ROOT" -type l -print
~~~

预期没有未经说明的 symlink，不含原始导出副本、checkpoint 或模型。

## E2. source manifest

必须有：

~~~text
dataset_id
source_sha256
source_size_bytes
consent_file_sha256
consent_digest
consent_id
authorization_status
raw_message_count
filtered_message_count
retained_self_target_text_count
excluded_by_consent
unknown_role_count
duplicate_message_id_count
timestamp_parse_failure_count
preprocessing_version
redaction_version
scanner_version
~~~

authorization_status=verified 只能由 consent 校验成功后的代码写入，不能人工修改。

## E3. 计数和 split

逐一记录 raw/filtered/retained 计数、三类 session 计数和三类 candidate 计数。
必须满足：

~~~text
train 非空
validation 非空
test 非空
同一 session 不跨 split
同一 near_duplicate_group 不跨 split
split_manifest digest 可复算
~~~

不能按单条 message 重新随机切分。

## E4. 全链路隐私扫描

建议实现只输出汇总的 scripts/audit_dataset_privacy.py：

~~~json
{
  "messages": {"hard_leak_count": 0},
  "sessions": {"hard_leak_count": 0},
  "candidates": {"hard_leak_count": 0},
  "reviewed": {"hard_leak_count": 0},
  "formal_snapshot": {"hard_leak_count": 0}
}
~~~

进入审核前至少要求 messages、sessions、candidates 三层为零；后续 reviewed 和 snapshot 也必须为零。
扫描脚本不得打印命中的聊天内容。

## E5. lineage

每个 session 和 candidate 都要能找到 source message IDs、source session ID、consent scope 和派生文件路径。
lineage 不放原始正文。

# 阶段 F：确定审核范围

## F1. 推荐审核全部候选

新版本产生多少 candidate，就审核多少 candidate。旧版 54,981 只是历史数字，不能直接套用。

每个 candidate 恰好一个事件：

~~~text
keep
redact_keep
reject
~~~

uncertain 只能表示未完成，不能进入正式 snapshot。

## F2. 合法的审核子集

若无法审核全部候选，必须先建立新的候选子集版本。子集 manifest 至少包含：

~~~json
{
  "parent_candidate_manifest_sha256": "...",
  "selection_rule": "确定性分层抽样规则",
  "selection_version": "candidate-selection-v1",
  "selected_sample_ids_sha256": "...",
  "selected_count": 5000,
  "split_counts": {
    "train": 3500,
    "validation": 750,
    "test": 750
  },
  "coverage_summary": {
    "time_ranges": ["..."],
    "risk_categories": ["..."],
    "behavior_types": ["..."]
  }
}
~~~

然后：

1. 写出 selected_candidates.jsonl；
2. 只审核 selected ID；
3. 编译器校验 selected manifest；
4. 未选中的 candidate 永远保持“未审核”；
5. 正式 snapshot 写明 selected candidate subset；
6. 不得声称完整候选池已审核。

子集必须测试：

~~~text
selected ID 不属于 parent candidate 时阻断
selected candidate 缺 event 时阻断
重复 event 时阻断
未选中的 candidate 不得进入正式导出
selected train/validation/test 为空时阻断
selection manifest digest 不匹配时阻断
~~~

在这些能力完成前，默认审核全部候选。

# 阶段 G：人工审核

## G1. 受控目录

~~~bash
install -d -m 700 /srv/galatea-private/wechat-persona/review-v2
~~~

建议文件：

~~~text
/srv/galatea-private/wechat-persona/review-v2/events.jsonl
/srv/galatea-private/wechat-persona/review-v2/reviewed-rows.jsonl
~~~

events.jsonl 只存 ID/hash；reviewed-rows.jsonl 只在 redact_keep 时保存受控脱敏修改结果。

## G2. 每条 candidate 的检查问题

审核人只看脱敏内容和 ID：

1. 上下文是否足够连贯？
2. 最后一条是否确实来自 target？
3. 是否仍有电话、邮箱、账号、地址等个人信息？
4. 是否有密码、验证码、token 或支付信息？
5. 是否包含第三方内容？
6. 是否依赖未保留的图片、语音或文件？
7. 是否是希望学习的风格，而不是应进入记忆库的私人事实？
8. 是否重复、过短、过长、无意义或不安全？

## G3. 状态规则

- keep：通过检查，原样保留，但记录审核人和时间。
- redact_keep：编辑成新脱敏内容；保存 edited row、reason、redacted_content_sha256、审核人和时间。
- reject：不能安全或正确用于 SFT；必须有标准原因。
- uncertain：尚未完成；正式编译前必须为零。

固定拒绝原因：

~~~text
secret_or_credential
pii_not_fully_redacted
third_party_content
media_dependency
wrong_target_role
duplicate_or_near_duplicate
fact_memory_not_style
low_quality
unsafe_or_sensitive
insufficient_context
~~~

secret 规则：

~~~text
secret 在目标回复中：优先 reject
secret 只在上下文中：完全脱敏并重新扫描通过后才可 redact_keep
同一 session 多个 candidate 共用泄漏：回到阶段 D 重建
~~~

## G4. 审核命令示例

keep：

~~~bash
PYTHONPATH=/data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/review_app.py \
  --candidate <one-redacted-candidate.json> \
  --event-log /srv/galatea-private/wechat-persona/review-v2/events.jsonl \
  --status keep \
  --reviewer-id <reviewer-id>
~~~

reject 必须有 reason：

~~~bash
... \
  --status reject \
  --reviewer-id <reviewer-id> \
  --reason secret_or_credential
~~~

redact_keep 必须提供编辑后的候选：

~~~bash
... \
  --status redact_keep \
  --reviewer-id <reviewer-id> \
  --reason pii_not_fully_redacted \
  --edited-candidate <edited-redacted-candidate.json> \
  --reviewed-row-log /srv/galatea-private/wechat-persona/review-v2/reviewed-rows.jsonl
~~~

## G5. 审核完成标准

~~~text
candidate 数量 = event 数量
candidate ID 集合 = event ID 集合
每个 candidate 恰好一个 event
重复 event = 0
uncertain = 0
缺 reviewer_id = 0
缺 reviewed_at = 0
reject 缺 reason = 0
redact_keep 缺 edited row = 0
redact_keep 缺 redacted_content_sha256 = 0
~~~

# 阶段 H：编译 reviewed.jsonl

## H1. 命令

~~~bash
PYTHONPATH=/data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/compile_reviewed.py \
  --candidates <new-dataset>/review/candidates.jsonl \
  --events /srv/galatea-private/wechat-persona/review-v2/events.jsonl \
  --reviewed-rows /srv/galatea-private/wechat-persona/review-v2/reviewed-rows.jsonl \
  --output /srv/galatea-private/wechat-persona/review-v2/reviewed.jsonl
~~~

没有 redact_keep 时可以省略 reviewed-rows。

## H2. 编译器必须阻断

~~~text
candidate/event ID 不一致
缺 event 或重复 event
非法 status
session_id 不一致
缺 reviewer_id 或 reviewed_at
reject 缺 reason
redact_keep 缺 reviewed row/hash
content hash 不一致
PII 二次扫描失败
输出文件已存在
~~~

## H3. 编译后核验

~~~text
reviewed rows hard_leak_count = 0
reviewed.jsonl 只包含 keep/redact_keep
reject 和 uncertain 不进入 reviewed.jsonl
每行保留 split 和 session_id
review event digest 可重算
~~~

# 阶段 I：生成正式 SFT snapshot

## I1. 正式配置

不要把带零值占位 digest 的诊断配置当正式配置。新建版本化配置：

~~~text
train-model/wechat-persona/configs/formal-sft-v2.yaml
~~~

填写真实的 dataset_id、source_sha256、manifest_sha256、split_sha256、consent_digest、
preprocessing_version、redaction_version、scanner_version，以及 model、tokenizer、LoRA、seed、
evaluation protocol 和 objective 配置。

## I2. 生成命令

~~~bash
PYTHONPATH=/data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/build_dataset.py \
  --config train-model/wechat-persona/configs/formal-sft-v2.yaml \
  --execute \
  --check-approved \
  --reviewed-jsonl /srv/galatea-private/wechat-persona/review-v2/reviewed.jsonl \
  --source-manifest <new-dataset>/manifests/source_manifest.json \
  --split-manifest <new-dataset>/manifests/split_manifest.json \
  --privacy-report <new-dataset>/reports/privacy_report.json \
  --lineage <new-dataset>/manifests/lineage.jsonl \
  --review-summary /srv/galatea-private/wechat-persona/review-v2/review-summary.json \
  [--selection-manifest /srv/galatea-private/wechat-persona/review-v2/selection-manifest.json] \
  --output /srv/galatea-private/wechat-persona/formal-snapshot/<new-version>
~~~

new-version 必须是新目录，不能覆盖已有 snapshot。

## I3. snapshot manifest 至少包含

~~~json
{
  "schema_version": "wechat-sft-v2",
  "dataset_id": "...",
  "source_sha256": "...",
  "manifest_sha256": "...",
  "split_sha256": "...",
  "consent_digest": "...",
  "review_evidence_digest": "...",
  "preprocessing_version": "...",
  "redaction_version": "...",
  "scanner_version": "...",
  "sample_counts": {
    "train": 0,
    "validation": 0,
    "test": 0
  },
  "assistant_only_loss": true,
  "formal_training_eligible": false,
  "requires_formal_dataset_ready_approval": true
}
~~~

示例中的 0 只是字段示意；实际三个 split 必须非空。阶段 I 只生成并冻结
snapshot，不会自动授予正式训练资格；只有阶段 J 的独立审批证据通过后，治理状态才能
转为 `formal_training_eligible=true`。

# 阶段 J：FORMAL_DATASET_READY

## J1. 授权人确认

脚本成功写出 snapshot 不等于治理自动通过。授权人检查报告后必须明确签署：

~~~text
FORMAL_DATASET_READY
~~~

证据只保存摘要、计数、hash、签名和时间，不保存聊天正文。

## J2. 证据内容

~~~json
{
  "status": "FORMAL_DATASET_READY",
  "dataset_id": "...",
  "dataset_manifest_sha256": "...",
  "split_sha256": "...",
  "consent_digest": "...",
  "review_evidence_digest": "...",
  "privacy": {
    "messages_hard_leak_count": 0,
    "sessions_hard_leak_count": 0,
    "candidates_hard_leak_count": 0,
    "reviewed_hard_leak_count": 0,
    "snapshot_hard_leak_count": 0
  },
  "review": {
    "candidate_count": 0,
    "event_count": 0,
    "uncertain_count": 0
  },
  "split_counts": {
    "train": 0,
    "validation": 0,
    "test": 0
  },
  "assistant_only_loss": true,
  "approved_by": "authorized-reviewer-id",
  "approved_at": "2026-09-07T...Z"
}
~~~

只有签署后，新数据版本才能标记 formal_training_eligible=true、human_review_completed=true、
pii_scan_passed=true、canary_scan_passed=true。旧版本仍为 review_only。

# 阶段 K：Release、Galatea 和 Ray

## K1. 禁止的训练方式

~~~bash
python train.py
python scripts/submit_train.py --run
ray job submit ...
直接 import Driver 后调用训练函数
~~~

这些方式没有绑定完整 Release、readiness 和执行身份。

## K2. Release

Release 封存：

~~~text
固定源码
固定训练入口
正式配置
项目环境声明
代码 revision
数据和 split digest 引用
~~~

源码、配置、入口或环境变化后构建新的 Release；旧 Release 不吸收工作区修改。

## K3. Galatea 操作顺序

~~~text
galatea_list_projects
galatea_select_project
galatea_inspect_project
galatea_plan_run
galatea_submit_job
galatea_observe_job
galatea_build_stage_evidence
galatea_verify_candidate
galatea_promote_model  # 只有用户明确要求时
~~~

逻辑：

1. 选择 wechat-persona；
2. 检查项目声明、资源和审批策略；
3. 用正式 config 和 Release 生成 readiness plan；
4. 核对 dataset/split/preprocessing/evaluation digest；
5. 获得本次 execution authorization；
6. 提交固定 Ray Driver；
7. 通过 MLflow Tracking/Artifact API 核验证据。

Plan 和 observe 不创建 MLflow Run；真正 submit 后才允许 Driver 创建 Run。

## K4. Driver 必须收到

~~~text
execution_mode=governed-ray-job
release_id
readiness_digest
execution_identity
attempt_id
ray_submission_id
ray_job_id
galatea_project=wechat-persona
role
promotable
~~~

缺任何一项或与 config 不一致，都阻断。

# 阶段 L：2-sample/2-step 和 10-step smoke

## L1. 2-sample/2-step

验证：

~~~text
数据可读取
tokenizer/chat template 正确
assistant-only loss mask 正确
LoRA target modules 存在
forward/backward 路径正确
checkpoint 可保存和独立加载
~~~

它包含 optimizer step 或可能产生 checkpoint，所以仍必须走固定 Ray Driver。

## L2. 10-step smoke

配置要求：

~~~text
run.role=smoke
training.max_steps=10
evaluation.test_access=untouched
promotable=false
~~~

train 用于参数更新，validation 只验证，test 不读取。至少记录 train_loss、learning rate、
gradient norm、validation loss/perplexity、耗时、GPU 显存、checkpoint digest 和 Artifact
round-trip。

通过标准：

~~~text
Ray Job 成功
MLflow Run 由 Driver 创建和结束
dataset/split/config/release identity 一致
checkpoint 上传成功
新进程能通过 Artifact API 下载并加载 adapter
artifact.roundtrip_verified=true
test_access=untouched
privacy/canary/unsafe gates 通过
~~~

Ray SUCCEEDED 只证明程序结束，不自动证明质量、隐私或可晋级。

# 阶段 M：validation-only baseline/Trial

## M1. 比较协议

Base、Prompt-only、RAG、LoRA、RAG+LoRA 如要比较，必须共享：

~~~text
dataset/split
prompt/input contract
tokenizer
generation protocol
seed 规则
metric definition
执行架构
~~~

不能把本地全量 baseline 和 governed Ray Trial 拼成一个结论。

## M2. 数据边界

~~~text
train：更新参数
validation：比较候选
test：保持 untouched
~~~

初始 baseline/Trial 使用 epochs=1；失败重试使用新的 attempt、Ray submission 和 MLflow Run，
不覆盖旧结果。

## M3. MLflow 记录

只能使用 Tracking、Artifact、Registry API：

~~~text
不打开 mlflow.db
不读 MLflow 服务端 MinIO 文件系统
不把长期对象存储凭据放进训练客户端
不上传聊天原文或生成原文
~~~

至少记录 task/project/role、dataset/split digest、preprocessing/scanner version、code revision、
hyperparameters、seed、environment、resources、Release/readiness/execution identity、Ray IDs、
train/validation metrics、artifact digest 和 round-trip 结果。

# 阶段 N：候选冻结、test-once 和 promotion

## N1. 候选冻结

只根据 train/validation 证据选择候选。冻结记录绑定 Trial Run ID、validation evidence digest、
checkpoint/model artifact digest、prompt/generation protocol、metric definition 和 dataset/split digest。

## N2. test-once

Champion 使用新的 readiness 和授权。固定 Driver 先取得原子 test-once claim，再读取 test。

结果必须带：

~~~text
test_evaluation_id
candidate_freeze_id
candidate/config/split digest
test_access claim
~~~

数据、split、checkpoint、prompt、阈值、评估规则、模型或 tokenizer 任一变化，都会使旧 test
结果失效。

## N3. Promotion

Promotion 是训练后的独立动作。只有以下全部满足才可调用：

~~~text
Artifact round-trip 通过
隐私门通过
安全门通过
质量门通过
候选已冻结
test-once 完成
人工安全审核通过
授权人明确要求修改 Model Registry alias
~~~

训练成功、Run 存在或 Ray SUCCEEDED 都不等于允许修改 alias。

# 5. 全部硬门禁

任意一条失败都停止：

~~~text
任何派生层 hard_leak_count > 0
真实 secret 没有被拒绝或完全脱敏
consent 缺 processing 或 persona_style
需要 evaluation 授权但没有 evaluation purpose
source SHA 不匹配
consent file SHA/digest 不匹配
manifest 或 split digest 不匹配
unknown_role_count > 0
duplicate_message_id_count > 0
timestamp parse failure 未解释
train/validation/test 任一为空
session 或 near_duplicate_group 跨 split
candidate/event ID 不一致
duplicate event > 0
uncertain > 0
reject 缺 reason
redact_keep 缺 edited row/hash
reviewed.jsonl 或 formal snapshot 计划覆盖已有目录
没有 FORMAL_DATASET_READY
没有 immutable Release
没有 Galatea readiness digest
没有 execution authorization
没有固定 Ray Driver
Artifact round-trip 失败
尝试本地训练或 generic Ray 绕过边界
~~~

失败后：保留证据、修复原因、新建数据版本或新 attempt，不覆盖别人的结果。

# 6. 推荐验收清单

## 6.1 数据工程

- [ ] 旧版本完整，状态为 review_only。
- [ ] 扫描器有原子消息层和跨消息层。
- [ ] candidate 按结构化 turn 扫描。
- [ ] source manifest 使用 raw/filtered/retained 三类计数。
- [ ] manifest 有 preprocessing/redaction/scanner version。
- [ ] dataset ID 绑定处理版本。
- [ ] pipeline 校验 processing + persona_style。
- [ ] 新数据来自原始导出。
- [ ] 新目录不覆盖旧目录。
- [ ] source/consent/dataset/split digest 可重算。
- [ ] 所有派生层隐私扫描为零。
- [ ] lineage 可回溯。

## 6.2 人工审核

- [ ] 审核范围明确为全部候选或 selected subset。
- [ ] 子集有 parent manifest、selection rule 和 digest。
- [ ] 每个范围内 candidate 恰好一个 event。
- [ ] uncertain=0。
- [ ] reject 都有标准原因。
- [ ] redact_keep 都有 edited row 和 redacted hash。
- [ ] 事件日志不含聊天原文。
- [ ] 审核 rows 再次扫描为零。

## 6.3 正式 snapshot

- [ ] 三个 split 都非空。
- [ ] assistant_only_loss=true。
- [ ] session 和 near-duplicate 不跨 split。
- [ ] snapshot manifest 包含 dataset/split/consent/review digest。
- [ ] snapshot 目录为新目录。
- [ ] 授权人签署 FORMAL_DATASET_READY。

## 6.4 训练

- [ ] immutable Release 已构建。
- [ ] Galatea readiness plan 已通过。
- [ ] execution authorization 已获得。
- [ ] 2-sample/2-step preflight 已通过。
- [ ] 10-step smoke 走固定 Ray Driver。
- [ ] MLflow Run 由 Driver 创建和结束。
- [ ] Artifact API round-trip 通过。
- [ ] Trial 只用 train/validation 选参。
- [ ] test 只在 candidate freeze 后使用一次。
- [ ] promotion 是单独、明确授权的动作。

# 7. 推荐产物目录

~~~text
/data/ai/chenzhangyue/code/data/wechat-deal/
├── wechat_2234f547cf224b33730f/          # 旧版本，只读历史
├── rebuild-v2/                           # 新导入父目录
│   └── <dataset-id>/
│       ├── redacted/messages.jsonl
│       ├── sessions/sessions.jsonl
│       ├── review/candidates.jsonl
│       ├── manifests/source_manifest.json
│       ├── manifests/split_manifest.json
│       ├── manifests/lineage.jsonl
│       └── reports/privacy_report.json

/srv/galatea-private/wechat-persona/
├── consent/consent-c-001.json
├── raw/export.json
├── speaker-map.json
├── governance/history/
├── review-v2/
│   ├── events.jsonl
│   ├── reviewed-rows.jsonl
│   └── reviewed.jsonl
└── formal-snapshot/<new-version>/
    ├── train.jsonl
    ├── validation.jsonl
    ├── test.jsonl
    └── manifest.json
~~~

checkpoint、adapter、报告和 MLflow Artifact 应写入配置的 Artifact 服务，不能把临时 notebook
目录当成持久接口。

# 8. 重试、撤回和异常

## 8.1 重试

每次训练重试使用新的 attempt_id、Ray submission/job ID 和 MLflow Run。旧 Run、checkpoint
和失败日志保留，不能覆盖成功路径。

## 8.2 consent 撤回

1. 记录撤回范围；
2. 通过 lineage 找到 message、session、candidate、snapshot、Run、checkpoint 和 adapter；
3. 写入删除/失效账本；
4. 标记受影响模型不可用；
5. 清理后重新建立数据和训练证据。

只删 JSONL 而保留已经学到秘密的 adapter，不算完成删除。

## 8.3 发现隐私泄漏

~~~text
停止审核和训练
保留 ID/hash-only 诊断
不把命中原文写入报告
定位受影响 session 和上下游 candidate
从原始导出重建新版本
重新跑全链路扫描
~~~

# 9. 最小可执行顺序

~~~text
1. 查看旧版本状态：review_only，不能改。
2. 修复 redact、pipeline、manifest、review、dataset 门禁。
3. 运行 wechat-persona 单元测试。
4. 只读验证源文件 SHA、权限和 consent digest。
5. 只读运行项目 check/plan 和平台健康检查。
6. 从原始 export.json 输出到全新的 rebuild-v2。
7. 核对 raw/filtered/retained 计数。
8. 核对 split、lineage、symlink 和全链路隐私扫描。
9. 决定审核全部候选还是建立 selected subset manifest。
10. 对明确范围做 100% keep/redact_keep/reject 审核。
11. 确认 candidate/event 一一对应，uncertain=0。
12. 编译 reviewed.jsonl，不能覆盖已有文件。
13. 生成 formal snapshot，三个 split 都非空。
14. 由授权人签署 FORMAL_DATASET_READY。
15. 构建 immutable Release。
16. 通过 Galatea plan/readiness/authorization。
17. 用固定 Ray Driver 做 2-sample/2-step preflight。
18. 用固定 Ray Driver 做 10-step smoke。
19. 用 train/validation 做 baseline/Trial 和候选冻结。
20. 取得 test-once claim 后只评估 test 一次。
21. 通过质量、安全、隐私和人工审核后，才考虑 promotion。
~~~

任何一步失败，都停在当前阶段修复，不能跳到下一阶段。

# 10. 相关代码和治理文档

项目入口：

- train-model/wechat-persona/README.md
- train-model/wechat-persona/galatea.project.yaml
- train-model/wechat-persona/scripts/import_chat.py
- train-model/wechat-persona/scripts/compile_reviewed.py
- train-model/wechat-persona/scripts/build_dataset.py
- train-model/wechat-persona/scripts/review_app.py
- train-model/wechat-persona/scripts/submit_train.py
- train-model/wechat-persona/scripts/evaluate.py

核心实现：

- train-model/wechat-persona/src/wechat_persona/redact.py
- train-model/wechat-persona/src/wechat_persona/pipeline.py
- train-model/wechat-persona/src/wechat_persona/datasets.py
- train-model/wechat-persona/src/wechat_persona/review.py
- train-model/wechat-persona/src/wechat_persona/training.py

治理规则：

- .codex/skills/governed-training-workflow/SKILL.md
- .codex/skills/governed-training-workflow/references/execution-classification.md
- .codex/skills/governed-training-workflow/references/evidence-contract.md
- doc/dsh-galatea-operations.md
- doc/train-llm/fine-tuning-evaluation-protocol.md

# 11. 文档完成时的状态

本计划写完不代表数据已通过，也不代表可以训练。实际状态仍是：

~~~text
旧版本：review_only
正式训练：未授权
MLflow Run：未创建
模型训练：未开始
下一步：先完成阶段 B 的代码修复和测试
~~~
