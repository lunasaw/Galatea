# 项目 0+1 验收清单

> 运行前先复制本文件到受控的 `platform-data/llm-baselines/<run_id>/acceptance.md`，在完成每项后记录证据路径或 MLflow Artifact 名称。不要把聊天正文或生成正文粘贴到本文件。

## A. 数据身份与授权

- [ ] 用户数据已交付到 `/data/ai/chenzhangyue/code/data`，或通过 `WECHAT_DATA_ROOT` 指定了唯一数据集根。
- [ ] 数据交付预检通过，解析布局已记录，未发现多个候选数据集根。
- [ ] 数据文件为只读普通文件，未通过符号链接逃逸，运行前后 staging digest 一致。
- [ ] `dataset_id=wechat_aa807aaad90dc4463964` 与配置一致。
- [ ] `source_sha256`、`config_sha256`、`pipeline_version=wechat-preprocess-v1.2` 核对通过。
- [ ] split manifest 的策略为 `chronological_session`，validation session/candidate 数为 137/759。
- [ ] `privacy_report.json` 二次扫描字段全部为 0。
- [ ] `leakage_report.json` 的候选级检查通过。
- [ ] 已读取受控 consent ledger 引用；不把授权正文写入仓库。
- [ ] consent ledger 明确处理范围和保留期限；缺失时状态保持 blocked。
- [ ] 外部数据目录在运行前后 digest 一致，未创建或覆盖文件。

## B. 环境与 GPU

- [ ] 项目环境独立创建，`pip check` 通过。
- [ ] Python、PyTorch、PyTorch CUDA runtime、支持 `qwen3_5` 的 Transformers 构建、Ray、MLflow 版本已记录。
- [ ] GPU 型号、数量、驱动可见版本、总/空闲显存已记录。
- [ ] BF16 张量矩阵乘通过。
- [ ] 现有 GPU 进程只读记录，未被终止或重置。
- [ ] 资源声明为 1 GPU、4 CPU、8 GiB 内存（或有书面变更及新配置 digest）。

## C. 模型与模板

- [ ] `Qwen/Qwen3.5-0.8B` 加载成功。
- [ ] Transformers 版本/构建支持 `qwen3_5` 与 `Qwen3_5ForConditionalGeneration`；不支持时保持 blocked。
- [ ] 模型 revision 已解析为不可变 commit，而不是 `main`。
- [ ] dtype 为 BF16，device 为 `cuda:0`。
- [ ] 使用 tokenizer 的 `apply_chat_template`，无手写特殊 token。
- [ ] `enable_thinking=false` 已生效并进入 Run Manifest。
- [ ] 2 条通用 prompt smoke 全部成功。

## D. 20 条固定推理

- [ ] fixture 仅来自 validation session，按 `sample_id` 稳定选择 20 条。
- [ ] 每条 fixture 的最后 assistant 目标未进入模型输入。
- [ ] fixture digest、prompt policy version 和输入 token 统计已记录。
- [ ] 20/20 条生成成功，或失败类别已记录且 Run 未标记成功。
- [ ] 记录首 token 延迟、总延迟、tokens/s、峰值显存和 decode 状态。
- [ ] 输出正文未进入 Git；MLflow 默认只保存哈希和长度摘要。

## E. MLflow、Artifact 与幂等

- [ ] Tracking URI 来自显式配置或 `MLFLOW_TRACKING_URI`。
- [ ] Experiment 为 `llm-lora-playground`，Run tag 含 `inference_baseline_only=true`。
- [ ] Run Manifest、fixture manifest、环境报告、指标报告和记录 JSONL 已上传。
- [ ] Artifact 通过 MLflow Artifact API 回读并核对 SHA-256。
- [ ] 幂等键由 dataset/split/fixture/model/config/code 身份计算。
- [ ] 重试使用新 Run ID，并记录 `retry_of`；未覆盖旧 Run 或旧 Artifact。
- [ ] 未读取 `mlflow.db` 或 MLflow 服务端 MinIO 文件系统。

## F. 阻断与结论

- [ ] `status=completed` 仅在 A–E 全部通过后设置。
- [ ] `--check-data` 在模型下载和 MLflow Run 创建之前完成。
- [ ] 报告明确写出 `inference_baseline_only=true`，不宣称训练或角色质量提升。
- [ ] `datasets/*.jsonl` 为空、人工审核未完成和 `authorization_status=not_verified_in_pipeline` 仍记录为“正式 SFT 阻断项”。
- [ ] 未注册模型、未更新任何生产 alias。
- [ ] 下一阶段开始条件已写明：人工审核完成、SFT 导出非空、正式质量门禁重跑通过。
