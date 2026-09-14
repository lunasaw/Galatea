# wechat-persona Schema

这些 JSON Schema 是项目数据治理与训练证据使用的活动契约，覆盖 consent、message、candidate、
memory card、event card 和 experiment manifest。

Schema 变化必须同步 importer/pipeline、测试、[项目文档](../docs/README.md)和新的 immutable Release。
不得放宽 owner isolation、撤回、隐私扫描、formal snapshot 或 final-test 边界来兼容旧数据。
