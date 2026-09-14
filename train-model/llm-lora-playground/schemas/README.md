# llm-lora-playground Schema

这些 JSON Schema 是项目代码、测试和训练证据使用的活动契约：

- `inference-record.schema.json`：推理结果记录；
- `inference-run-manifest.schema.json`：推理基线 Run manifest；
- `sample.schema.json`：Toy SFT 样本；
- `job-metadata.schema.json`：Ray Job、Release、readiness 和 attempt 绑定；
- `training-run-manifest.schema.json`：governed LoRA Training Run manifest。

字段语义变化必须同步项目代码、测试、[项目文档](../docs/README.md)和新的 immutable Release；不要通过
修改历史文档来改变已存在 Run 的含义。
