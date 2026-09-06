# 项目 5–9 运行手册

## 0. 运行规则

以下命令中的路径、dataset ID、Release、Run ID 和资源均为示意。真实路径必须由受控配置提供；不得
把示意命令改成直接 `python train.py` 或通用 `ray job submit`。训练、正式评估和 checkpoint 生成只
能通过 `wechat-persona` 的 immutable Release + Galatea plan/authorization + fixed Ray Driver。

## 1. 只读前置检查

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate <wechat-persona-env>
systemctl is-active mlflow.service minio.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
python scripts/import_chat.py --config configs/import.yaml --check
python scripts/build_dataset.py --config configs/import.yaml --plan
```

检查输出必须显示 dataset/source/config digest、consent 状态、split 计数、预估资源和阻断原因；不
产生正式数据、MLflow Run 或模型工件。

## 2. 项目 5：导入、审核和正式导出

1. 将合法导出文件放入受控 raw 目录并设为只读；登记源 SHA-256 和 consent ledger 引用。
2. 运行 importer check，再运行正式的本地数据工程 Driver（不训练）生成 normalized/redacted/session、
   reports 和 review-only candidates。任何原文外泄或 symlink escape 立即停止。
3. 启动审核工具，只处理脱敏候选；审核者逐条提交 keep/redact_keep/reject/uncertain，事件追加写入。
4. 审核完成后运行 `build_dataset.py --check-approved`，确认三份 split 非空、session 不跨 split、
   二次隐私扫描通过、source/manifest/split digest 稳定。
5. 由授权者确认 `FORMAL_DATASET_READY`，再把正式数据快照发布到受控 artifact prefix。当前仓库
   状态下应停在第 3 步，因为授权未核验且候选全为 uncertain。

## 3. 项目 6：RAG baseline

1. 从已批准 memory-only 快照生成 memory cards；人工确认高敏和冲突处理。
2. 先构建 BM25 index，保存 index manifest 和完整 card digest；用 validation query 报告 Recall@K/MRR。
3. 在同一 query/generation 协议下运行 Prompt-only vs RAG；无证据场景必须返回不确定策略。
4. 若 BM25 不足，再在独立环境构建本地 embedding index；只有 validation 证据支持时尝试 reranker。
5. 执行删除单卡、session、consent scope 演练：tombstone/重建、查询验证、artifact digest、deletion receipt。
6. 通过 owner、PII/canary、prompt injection 和删除门后，才可标记 `RAG_INDEX_VALIDATED`。

## 4. 项目 7：真实角色 LoRA

1. 只读确认项目 5 ready、项目 6（若组合）索引 frozen、模型 revision/环境兼容、授权和人工审核状态。
2. 用配置生成 immutable Release，执行 Galatea plan 和 2-sample/2-step preflight。失败不消耗正式训练预算。
3. 经明确启动确认后，以同一 Ray Driver 执行 10-step smoke；检查 loss mask、梯度、checkpoint、MLflow
   artifact round-trip 和恢复指针。
4. Smoke 全部通过后运行 1 epoch baseline/Trial；只读 train/validation 选择 checkpoint 和 prompt。
5. 冻结五组 variant、generation config、检索快照、protocol 和 candidate manifest；生成盲测队列。
6. 完成至少 100 个匿名配对判断，先过安全硬门，再判断 `LoRA_vs_PromptOnly_win_rate >= 0.60`。
7. 由独立步骤绑定 `test_evaluation_id` 执行 test 一次；生成 final-test report 和 Artifact round-trip receipt。
8. 人工/安全 review 通过后，才允许项目 9 读取该 candidate。训练命令不执行 Registry alias 变更。

## 5. 项目 8：容量对照和 QLoRA

1. 在相同 frozen data/protocol 下先提交 1.7B BF16 LoRA；与 0.8B 分组比较质量、延迟、显存和吞吐。
2. 若提升不足 5 个百分点或资源成本不合算，记录 STOP，并不启动 4B。
3. 若确认容量瓶颈，单独执行 4B QLoRA preflight：量化加载、2-step backward、保存/新进程加载、
   bitsandbytes/GPU 诊断。兼容性失败只记为环境 blocked。
4. QLoRA Trial 沿用同一 Ray/MLflow/Artifact/test-once 流程；最后由书面决策记录 adopt/stop。

## 6. 项目 9：本地原型启用

1. 启动前检查 candidate、index、protocol 的 digest 和 `quality_evidence_status=accepted`、
   `human_review_completed=true`、未撤回。
2. 运行本地 CLI 的安全/身份/记忆关闭/删除 preflight；服务不接受任意 adapter 或全局 owner 查询。
3. 聊天日志默认关闭；若开启，保存期限和删除按钮必须可用。输出失败时返回安全降级，不回显秘密。
4. 事件卡生成只读取批准的 memory/session 摘要；脚本输出每个事实的 card/source ID 和待确认状态。
5. 双方审核后才导出脚本；撤回或上游 digest 变化时入口自动 disabled，重新走候选冻结。

## 7. 故障处理

- 训练失败：保留失败 Run/attempt 和诊断，生成新 attempt；使用 `retry_of`/`resumed_from`，不覆盖成功工件。
- artifact hash 不一致：Run 标记 failed，禁止 round-trip passed、candidate freeze 和原型启用。
- test 被提前访问：废弃该 split/test 证据，重新冻结并生成新的 test evaluation ID。
- consent 撤回：立即 disable 原型，执行删除账本和受影响 adapter/index 的失效，清理后重新训练。
- Ray/环境不兼容：停在 preflight，不能把本地结果标为 governed evidence。

