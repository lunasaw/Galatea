# 项目 2–4 验收清单

> 本清单对应 [`design.md`](design.md) 和 [`runbook.md`](runbook.md)。实施时复制到受控的
> `platform-data/llm-baselines/<project>/<run_id>/acceptance.md`，填写证据路径或 MLflow Artifact 名称。
> 不粘贴聊天正文、完整模型输出、密钥或真实个人信息。

## A. 共用前置条件

- [ ] 项目 0+1 Qwen3.5-0.8B 模型/template/environment smoke 有可回读证据。
- [ ] 训练环境独立、`pip check` 通过，Transformers 支持 `qwen3_5`。
- [ ] model ID、immutable revision、tokenizer revision、BF16、`cuda:0` 已记录。
- [ ] MLflow Tracking URI 和 Experiment 名称来自显式配置/环境变量。
- [ ] MLflow、MinIO 健康；客户端未读取 `mlflow.db` 或 MinIO 服务端文件系统。
- [ ] 资源声明为 1 GPU、4 CPU、8 GiB memory；实际资源不足时保持 blocked。
- [ ] 项目 2–4 未读取真实微信数据，未改变 consent ledger 或人工审核状态。
- [ ] 数据、adapter、checkpoint、缓存和 secrets 未进入 Git。

## B. 项目 2：合成数据与配置

- [ ] 生成数据约 300–500 条，或明确记录 smoke 子集大小。
- [ ] 所有样本符合 schema；`sample_id` 唯一且可稳定排序。
- [ ] 最后一条消息为非空目标 assistant，role 仅为 system/user/assistant。
- [ ] 每条样本有 `scenario_id`、style label、generator/preprocessing version 和 seed。
- [ ] 数据只含虚构/公开内容；无真实伴侣对白、姓名、地址、电话、账号、秘密或 `text_original_ref`。
- [ ] 固定 seed 可重算相同数据文件和 dataset digest。
- [ ] 数据 manifest 记录 source（synthetic 或公开 URI）、许可证（如适用）、文件 SHA-256、schema version。
- [ ] `toy-lora-smoke.yaml` 的 `max_steps=10`、`run_kind=smoke`；baseline 为 `epochs=1`。
- [ ] LoRA rank/alpha/dropout/target modules、学习率、batch、scheduler、seed 全在 YAML 中。
- [ ] 配置显式声明 `assistant_only_loss=true`、`packing=false`、objective metric 和 mode。

## C. 项目 2：SFT、LoRA 与 checkpoint

- [ ] tokenizer `apply_chat_template` 被调用；没有手写 Qwen 特殊 token。
- [ ] `enable_thinking=false` 生效并进入 manifest。
- [ ] system/user/history/padding labels 全为 `-100`。
- [ ] assistant target 至少有一个有效 label，且 input/label 长度对齐。
- [ ] 多轮样本只监督目标 assistant；截断不产生越界或错误标签。
- [ ] 无法识别 assistant span 时直接失败，不退化为全序列 loss。
- [ ] 目标 LoRA modules 全部存在；不存在时打印候选列表并阻断。
- [ ] 2-sample/2-step 预检查通过，才启动 10-step smoke。
- [ ] 10-step smoke（不含首次下载）≤10 分钟。
- [ ] 1 epoch baseline（不含首次下载）≤30 分钟。
- [ ] train/validation loss、learning rate、gradient norm、step/epoch 记录完整。
- [ ] adapter 工件不包含完整 base model；新进程可加载 base+adapter。
- [ ] checkpoint 目录包含 adapter config/weights、必要 optimizer/scheduler/RNG state 和 metadata。
- [ ] checkpoint 文件 SHA-256 与 manifest 一致，写全后才标记 `complete`。
- [ ] 失败 checkpoint 标记 incomplete，不成为默认恢复点。
- [ ] 失败训练未覆盖任何成功 adapter、checkpoint、Run 或固定目录。
- [ ] 固定风格测试显示 adapter 与 base 存在可解释差异；结论不夸大为泛化最优。

## D. 项目 3：数据、split 和对照

- [ ] 数据扩展到约 1,000 条，source/许可证/manifest digest 已记录。
- [ ] split 按 `scenario_id`/对话组或明确 chronological session 完成。
- [ ] 同一 group、模板族和近重复样本没有跨 train/validation/test。
- [ ] split manifest 可稳定重算，包含 seed、计数、sample/group 清单摘要和 SHA-256。
- [ ] split 冻结后没有静默重排或追加 test 样本。
- [ ] Base、Prompt-only、LoRA 三组有清晰 variant ID/Run ID。
- [ ] 三组共享 tokenizer、prompt、generation 参数、seed、输入和 max tokens。
- [ ] Prompt-only 与 LoRA 的主要变量只有 adapter；没有为 LoRA 单独改 system prompt。
- [ ] objective metric 和 `min/max` 方向在配置中声明；结果含定义版本。
- [ ] 自动指标包括 validation loss、生成长度、格式/风格遵循率、重复率等。
- [ ] 固定规则测试覆盖简短、温和、角色一致和不编造未提供事实。

## E. 项目 3：候选、测试和 MLflow

- [ ] 候选选择只使用 train/validation；没有用 test 早停或调参。
- [ ] candidate freeze 记录 checkpoint、prompt、metric definition、split/config digest。
- [ ] 测试集在候选冻结后只评估一次，并有唯一 `test_evaluation_id`。
- [ ] 任何协议变更都会使旧 test 结果作废并重新冻结。
- [ ] 每个 Run 记录 dataset/source/split/preprocessing/model/code/environment/seed/resources 完整身份。
- [ ] 完整 LoRA、训练和生成超参数进入 params/config artifact。
- [ ] adapter、checkpoint metadata、manifest、metrics、evaluation report 均可由 Run ID 找到。
- [ ] 每个 artifact 有 Run 内记录的 SHA-256；下载后校验通过。
- [ ] 新进程通过 MLflow Artifact API 下载 adapter/manifest 并成功加载 base+adapter。
- [ ] round-trip 重新评估的指标、协议、计数和 digest 与原 Run 一致。
- [ ] round-trip 失败时 Run 明确标记 failed；没有把本地未追踪结果冒充 MLflow evidence。
- [ ] 未注册模型、未更新任何 production alias。

## F. 项目 4：Ray Job 与恢复

- [ ] Ray Job 与本地脚本调用同一 `train()` 函数和同一 canonical config。
- [ ] Job 显式声明 1 GPU、4 CPU、8 GiB memory、worker_count=1。
- [ ] Driver 是唯一创建/结束父 MLflow Run、发布共享 artifact 和最终状态的 owner。
- [ ] Worker 不重复创建/结束父 Run，不发布共享 alias；只计算、报告指标和写 checkpoint。
- [ ] Job metadata 含 Ray Job ID、MLflow Run ID、config/data/code/environment digest、attempt ID。
- [ ] metadata 含 checkpoint URI/digest、requested resources、status、failure reason 和 timestamps。
- [ ] metadata/checkpoint 指针采用原子写入；不会暴露半成品路径。
- [ ] 第 N step checkpoint 完成后可控中断 smoke Job。
- [ ] 中断后旧 Run/attempt 状态可由 API 找到，旧成功工件未被覆盖。
- [ ] 新 attempt/Run 通过 `resumed_from`/`retry_of` 关联旧状态。
- [ ] 恢复前后 data/config/model/code/environment identity 一致。
- [ ] 只从完整且 digest 匹配的 checkpoint 恢复；不匹配时安全干净重跑。
- [ ] 恢复报告记录起点、耗时、丢失 step 数、最终状态和工件 digest。
- [ ] Ray Job 成功只标记调度/恢复成功，未被误报为最终 test evidence。
- [ ] 未使用 `--force` 覆盖旧 Run 或旧 adapter 作为常规重试。

## G. 结论与放行

- [ ] 项目 2 的所有 B–C 项通过，才标记 `project_2_status=accepted`。
- [ ] 项目 3 的所有 D–E 项通过，才标记 `project_3_status=accepted`。
- [ ] 项目 4 的所有 F 项通过，才标记 `project_4_status=accepted`。
- [ ] 任一硬门禁失败时状态为 blocked/failed diagnostic，不进入下一项目。
- [ ] 交付包包含配置、数据/split manifest、Run ID、Artifact round-trip、Ray recovery 报告和风险清单。
- [ ] 明确记录：项目 2–4 使用合成/公开数据，不代表真实微信数据已获授权，也不代表最终产品质量。
- [ ] 进入项目 5 前仍需独立完成真实数据授权、脱敏、人工审核、撤回和隐私安全门禁。
