# GPT 预标注策略

这是一轮机器预标注，不是人工审核，也不会使草案获得 `formal_training_eligible`。脚本直接使用审核服务现有的 `/api/dataset` 和 `/api/decision` 接口，因此结果进入当前审核工作区的 `latest-decisions.json` 与 `review-events.audit.jsonl`，不生成第二份机器标注文件。

## 判定策略

- 只读取服务提供的 `train` 和 `validation`；`test` 永远不请求。
- 每批默认发送 12 条候选，只发送批次内序号和 `messages`，不发送 `sample_id`、`session_id`、来源 ID 或 digest。
- 使用 Responses API 的严格 JSON Schema，输出 `keep`、`reject` 或 `uncertain`，以及用途标签、置信度、受控原因码和风险标记。
- `relationship_context` 只在人物关系/关系边界本身显著解释回复，并且对助手行为学习有帮助时使用；单纯的称呼或亲密词不足以打标。
- 不允许 GPT 产出 `redact_keep` 或编辑文本。隐私、第三方、身份、操纵和辱骂风险不会自动获准；keep 只在置信度不低于 `0.78` 且无这些风险时写入，否则降为 `uncertain`。
- 写入审核服务时固定使用 `reviewer_id=gpt-prelabel-v1`，notes 记录模型、置信度和不引用原文的简短理由。人工保存同一候选后会以更高 revision 覆盖该机器事件。

## 执行

先跑一条接口/Schema 冒烟：

```bash
PYTHONPATH=train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/prelabel_review_with_gpt.py \
  --max-samples 1 --workers 1
```

通过后跑当前精选草案的一轮：

```bash
PYTHONPATH=train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/prelabel_review_with_gpt.py \
  --workers 8 --batch-size 12
```

脚本可重复执行：已存在的事件会跳过，失败项会在下一次运行时重试。它只通过 loopback 审核服务写入，服务仍返回 `training_eligible=false`。人工复核时可在页面按状态筛选，并以自己的审核人标识重新保存；机器事件不能代替 `human_review_completed`。

第一遍为低置信度和硬风险保留 `uncertain`。若需要先让页面达到 100% 再逐条人工复核，可进行第二次保守裁决：

```bash
PYTHONPATH=train-model/wechat-persona/src \
/data/conda/envs/attend-ray-py312/bin/python \
train-model/wechat-persona/scripts/prelabel_review_with_gpt.py \
  --resolve-machine-uncertain --workers 8 --batch-size 12
```

第二遍只重审 `reviewer_id=gpt-prelabel-v1` 的机器待定项，要求模型在 `keep/reject` 中二选一；低置信度、空标签或硬风险统一失败关闭为 `reject`。它会增加同一审核日志中的 revision，但不会覆盖人工审核人的既有决定。

## 实验基线（2026-09-12）

本轮 `7,734` 条 GPT 预标结果保留 `3,377` 条、排除 `4,357` 条。保留项按父数据集的冻结 split
生成实验快照 `wechat-gpt-prelabel-v1-d413908f703d967d6a0e`：train `2,652` 条、validation
`725` 条；父 test `8,225` 条仅绑定身份，不下发给 baseline Driver。

- 数据 manifest：`90674f0926e60a98ad6a6e93a92708c3bf63110accbbcc03d2b7f910d6204f2a`
- split digest：`e859c4f43cf9ae74fb2e685b918d4dfdfbec612725483861851ad66e7d736289`
- 配置：`gpt-prelabel-v1-baseline`，Qwen3.5-0.8B LoRA，1 epoch，batch size 4，learning rate `1e-4`，max length 1024，seed 42
- Release：`09ea2cdc786ad5b97f25`
- Galatea operation：`op-4fd7c1498fb653b5e7ce774f70500882`
- MLflow Run：`52f43f68fb6e421da68f7a8550d29990`
- 结果：664 steps，`train_loss=4.094654262335592`，`val_loss=3.8475794792175293`，`val_perplexity=46.87945309870853`
- 证据 digest：`de6625b0595c04bbac64cbcb4651b587778e0057b967c61a216c7ed9d4e8b069`
- Adapter SHA-256：`8cf611a1f0dbc26decb3eeddd6ac80adacc117146fb11dfca5857bd193f84e1f`

Ray 终态为 `succeeded`，MLflow 为 `FINISHED`，且 Artifact API 回读、adapter 独立加载验证均通过。
这是 machine-reviewed、validation-only 的实验基线，不是人工审核证据；`human_review_completed=false`、
`formal_training_eligible=false`、`promotable=false`、`test.access=untouched`，未冻结候选、未执行 final
test、未注册模型或修改 Registry alias。

## 成本和停止条件

默认模型是当前代理可用且已通过 Responses/JSON Schema 冒烟的 `gpt-5.6-sol`，可用 `--model` 显式切换。遇到 API 认证、限流、schema 不兼容或审核服务错误时，脚本只记录 sample ID 和错误并继续，退出码为 `2`；不把失败项写成 `uncertain`，避免把未调用 GPT 的样本误认为已审核。
