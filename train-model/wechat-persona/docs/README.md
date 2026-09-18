# 项目 5–9：wechat-persona 合并实施指南

> 本文是私有聊天 workload 的主导航，合并了项目 5–9 的总体设计、运行手册、验收清单和各子项目
> 入口。真实数据仍以 `formal_evidence_blocked` 为默认安全状态；没有授权、人工审核和 formal
> snapshot 时，下面的训练步骤只能停在 check/plan 或合成 fixture。
> 跨项目治理规则见
> [总指南](../../../doc/train-llm/2026-09-10-governed-llm-finetuning-complete-guide.md)。旧的阶段设计与
> 实施记录已经合并并删除，历史变化通过 Git 追溯。

## 1. 目标和边界

项目 5–9 将私有聊天 workload 拆成五个可审计能力：

```text
5 数据工程/授权/脱敏/审核
  -> 6 外部记忆 RAG
  -> 7 角色风格 LoRA
  -> 8 容量对照与 QLoRA
  -> 9 本地聊天与动画脚本原型
```

目标是让风格、关系事实、实时信息和叙事改编彼此分离：

- 风格由 Prompt/LoRA 学习；
- 关系事实默认保存在可删除的 owner-scoped memory/RAG；
- 实时信息通过受控工具获得；
- 动画脚本从已批准事件卡生成，默认改写而不是逐字复制私人对白。

不实现破解加密数据库、自动登录/发消息、语音克隆/换脸、公网多租户或自动生产推广。

## 2. 准入状态机

```text
DISCOVERED
  -> CONSENT_VERIFIED
  -> IMPORTED
  -> NORMALIZED_REDACTED
  -> SESSIONIZED_SPLIT_FROZEN
  -> REVIEWED
  -> FORMAL_DATASET_READY
  -> RAG_INDEX_VALIDATED
  -> LORA_TRIAL_VALIDATED
  -> CANDIDATE_FROZEN
  -> TEST_ONCE_COMPLETED
  -> HUMAN_SAFETY_APPROVED
  -> LOCAL_PROTOTYPE_ENABLED
```

任一阶段都可以进入 `BLOCKED` 或 `WITHDRAWN`，但不能跳跃。撤回会让下游 dataset、index、Run、
checkpoint、adapter、备份和原型入口失效或进入删除账本。

## 3. 阶段、输入和放行条件

| 阶段 | 输入 | 关键处理 | 放行条件 |
| --- | --- | --- | --- |
| 项目 5 数据工程 | 合法导出、consent ledger、speaker map | import、normalize、redact、session、group/time split、review | 全链路隐私为 0、审核结案、formal snapshot 非空、可撤回 |
| 项目 6 RAG | memory-only approved snapshot | memory card、BM25/embedding、owner scope、删除演练 | Recall/MRR、无证据拒答、跨 owner=0、删除残留=0 |
| 项目 7 角色 LoRA | approved target replies | assistant-only SFT、同一 Driver、Base/Prompt/RAG/LoRA 对照 | smoke/baseline、盲测和安全门、candidate freeze |
| 项目 8 容量/QLoRA | 同一 frozen protocol | 1.7B BF16 LoRA，必要时 4B QLoRA | 质量提升抵消资源成本；量化兼容和 fresh-load 通过 |
| 项目 9 原型 | accepted candidate/index/event cards | AI 身份、记忆开关、删除、脚本改写和来源追溯 | 人工/安全 review 通过且未撤回 |

### 当前硬阻断

以下任一项存在时，不得把真实数据交给训练 Driver：

- consent purpose、主体、期限或第三方范围无法核验；
- 原始/派生层隐私或 secret/canary 扫描未通过；
- review 仍有 `uncertain`、缺事件、缺原因或 edited 内容无 hash；
- formal `train/validation/test.jsonl` 缺失或为空；
- split、lineage、manifest digest 无法复算；
- 撤回无法定位并删除/失效下游 adapter、index 和备份；
- Release、readiness、后端 execution binding 或 Artifact 服务未就绪。

## 4. 项目 5：数据工程主流程

1. 将原始导出放入受控只读目录，记录 source SHA-256 和 consent 引用。
2. 通过 importer 统一生成 normalized/redacted messages；扫描原子消息和跨消息 secret/PII，不保留命中原文。
3. 按 session 和时间/场景分组切分，固定 train/validation/test manifest；禁止按单行随机打散。
4. 候选初始为 `uncertain`，审核事件追加写入；`keep/redact_keep/reject/uncertain` 必须完整覆盖。
5. 编译 reviewed snapshot，重新执行 schema、隐私、重复、跨 split、lineage 和 digest 检查。
6. 授权者单独签发 `FORMAL_DATASET_READY`，再把不可变 snapshot 交给训练/索引流程。

精确的全链路修复顺序和受控命令见 [wechat-persona governed execution plan](governed-execution-plan.md)。

## 5. 项目 6：RAG 与记忆

记忆与风格权重分离。每条 memory card 至少包含 owner、来源 message/session ID、摘要 hash、时间窗、
sensitivity、status、revision、人工确认状态和删除关联。

检索只允许当前 owner 的 `confirmed`、未过期记录；`candidate/superseded/deleted` 不得注入 prompt。
先做 BM25 baseline，再按 validation 证据决定是否加入本地 embedding/reranker。无证据、过期、冲突、
第三方敏感信息和 prompt injection 场景必须遵守不确定/拒答策略。

删除测试需要覆盖单卡、session 和 consent scope：tombstone/重建索引、查询残留为 0、写 deletion
receipt，并让依赖该数据的原型和模型工件进入失效/重训流程。

详见 [memory-grounded-evaluation-protocol](../../../doc/train-llm/memory-grounded-evaluation-protocol.md)。

## 6. 项目 7：角色 LoRA

训练只使用已审核、已脱敏、已授权的目标角色回复；关系事实优先进入 RAG。复用项目 2–4 的固定
Driver、MLflow、Artifact、checkpoint 和 test-once 边界，不建立本地全数据 baseline。

推荐顺序：2-sample/2-step 兼容 preflight → 10-step governed smoke → 1 epoch baseline/Trial →
同一 frozen protocol 下的 Base/Prompt-only/RAG/LoRA/RAG+LoRA → 至少 100 个匿名配对盲测 →
candidate freeze → test-once → 人工/安全审查。`LoRA_vs_PromptOnly_win_rate >= 0.60` 和 RAG 增益阈值是
起始门槛，不是绕过安全硬门的理由。

## 7. 项目 8：容量和 QLoRA

先在相同数据、split、prompt、generation 和指标下比较 1.7B BF16 LoRA 与较小基线；只有确认容量
瓶颈且质量提升能够抵消延迟/显存/成本，才启动 4B QLoRA。QLoRA 必须单独验证量化加载、backward、
保存、Artifact round-trip、fresh-process load 以及 bitsandbytes/Transformers/PyTorch/CUDA 版本。

## 8. 项目 9：原型启用

原型只加载 `quality_evidence_status=accepted`、人工审核通过且未撤回的 candidate/index。聊天入口
显示 AI 身份，支持关闭记忆、清除日志、owner 隔离和安全降级；脚本事实必须可追溯至 event card 和
获准 session，双方可编辑/删除，未经单独授权不生成声音或肖像。

## 9. 失败、撤回和恢复

- 训练/评估失败：保留 Run 和 Attempt，修复后使用新 Release/Plan/Attempt/Run。
- test 提前访问：作废旧 test evidence，重新冻结 candidate。
- Artifact hash 或新进程加载失败：禁止 candidate freeze 和原型启用。
- consent 撤回：立即禁用原型，执行 deletion ledger，删除/失效受影响 index、Run、checkpoint、adapter，必要时清理数据后重训。
- Ray/环境/权限不兼容：停在 preflight，不将本地结果标为 governed evidence。

## 10. 验收和相关文档

项目 5–9 的统一验收必须同时覆盖数据、RAG、LoRA、容量、原型和撤回。本文件定义阶段验收；精确的
执行前置条件、证据查询和停止条件以 governed execution plan 为准。

当前文档：

- [女友语气 AI 助手端到端优化方案](girlfriend-assistant-optimization-plan.md)
- [本地数据用途筛选页](review.html)
- [当前数据预处理设计](data-preprocessing.md)
- [按天上下文的事实与记忆预处理方案](daily-memory-extraction-plan.md)
- [当前 governed execution plan](governed-execution-plan.md)
- [跨项目外部记忆评测协议](../../../doc/train-llm/memory-grounded-evaluation-protocol.md)
- [跨项目微调评测协议](../../../doc/train-llm/fine-tuning-evaluation-protocol.md)

### 审核页访问

审核页不需要手动选择 JSONL。服务端固定读取当前精选 draft 的 `train.jsonl` 和
`validation.jsonl`，浏览器打开页面后自动请求同源 `/api/bootstrap` 和分页 `/api/dataset`；每次只传输
当前页（默认 32 行），筛选/搜索也在服务端完成。决策保存到受控审核目录，`test` 永远不会通过接口提供。

在服务器上启动 loopback 服务（只读精选数据、不会启动训练）：

```bash
PYTHONPATH=train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/serve_review.py \
  --dataset-dir /srv/galatea-private/wechat-persona/curated-drafts/<curation-id> \
  --review-dir /srv/galatea-private/wechat-persona/curated-reviews/<dataset-id> \
  --host 127.0.0.1 --port 51644
```

外部 SSH 使用本地端口转发到服务器的 loopback 端口：

```bash
ssh -N -L 56961:127.0.0.1:51644 <user>@<host>
```

然后打开 `http://localhost:56961/review.html`。在已登录 Coder 的会话中，也可使用
`https://coder.vdian.net/<workspace>/proxy/51644/review.html`；若显示 `401`，先完成 Coder 登录，
不要改成公网绑定地址。

### 事实候选审核页

事实候选审核页读取一个不可变 daily-memory snapshot，并将人工判定单独写入受控 review workspace。
它不会修改源快照、构建 confirmed cards 或 RAG 索引，也不会授予训练资格。启动当前快照：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/serve_fact_review.py \
  --snapshot-dir \
    /srv/galatea-private/wechat-persona/memory-daily/daily-memory_20bb25fd1141dbe2cd86 \
  --review-dir \
    /srv/galatea-private/wechat-persona/memory-daily-reviews/daily-memory_20bb25fd1141dbe2cd86 \
  --host 127.0.0.1 --port 51645
```

打开 `http://127.0.0.1:51645/fact-review.html`。远程访问应使用 SSH 端口转发或已认证的
Coder proxy，禁止为了浏览器访问而改成公网监听。只读校验可在同一命令末尾增加 `--check`；该模式
不创建 review workspace。
