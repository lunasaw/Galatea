# 方案 06：数据与不可变 Release 实施计划

> 后续实现使用 superpowers:executing-plans；实际修改 workload 时遵守 model-project-structure。

**目标：**训练始终绑定可复现的数据、配置、代码和环境，按需扩展新数据入口。
**架构：**首版读取已批准 manifest/Release；扩展操作仍由 Python MCP 和受控 Python 执行入口处理。
**技术栈：**Python、S3 API、MLflow Artifact API、项目既有 Release 脚本。
**共同约束：**遵守 [实施索引](README.md)，不建设数据管理数据库，不修改插件。

## 1. 首版与扩展

首版仅已注册项目、已登记数据、预建配置/Release 列表。
检查 data/split/预处理/config/代码/环境摘要和固定入口，不需要先做上传、模板市场或构建服务。
如果参数嵌入 Release，改变参数必须新建不可变 Release；不能原地改共享配置就提交旧包。

全新数据上传、动态参数变体和受控代码构建分别开放，capability 明确实际范围。
不具备隔离构建时继续使用预建列表，不绕过失败检查。

## 2. 拟定文件

相对于 Galatea/services/galatea-mcp：

| 文件 | 职责 |
| --- | --- |
| src/galatea_mcp/datasets.py | 核验 dataset/split manifest、对象版本与身份 |
| src/galatea_mcp/releases.py | 核验批准 Release、固定入口、环境/config 摘要 |
| tests/test_inputs.py | 内容改变、split 变化、路径越界、缺失对象均拒绝 |
| 扩展：src/galatea_mcp/uploads.py、variants.py、builds.py | 上传收据、不可变配置、隔离构建 |
| 扩展：tests/test_uploads.py、test_variants.py、test_builds.py | 重传、摘要、允许字段和构建边界 |

项目专属打包脚本、模型代码和测试仍在 `train-model/<project>/src/`、`scripts/`、`tests/`，
服务只调用声明的固定入口。新建项目遵守项目结构，不复用另一个 workload 名称承载新任务。

## 3. 冻结合同

Dataset：dataset_id、对象版本、manifest_digest、用途/分类。
Split：split_id、分组策略、seed、assignment_digest、预处理版本和各角色可见视图。
Release：release_id、code revision/摘要、环境锁、entrypoint、config mode/digest、构建验证引用。
这些是文件/对象 manifest，不要求数据库中的同名表。

新数据由受信入口先预留独立项目 ID，再在该项目范围上传；draft 不可训练。
上传由数据所在机器执行，MCP 只传对象引用和受约束上传指引。
服务端 local_path 不能代指笔记本路径；ETag 不能替代完整 SHA-256。
大对象异步校验完成后才冻结版本，失败保留可诊断收据，不声称已登记成功。

配置变体只能修改允许字段，不能修改门禁、数据 split 或入口/依赖。
变体生成新 config_id/digest；嵌入式配置触发新 Release，外部配置模式绑定不可变摘要。
生成代码由隔离构建环境检查，凭据/网络/资源与正式训练分开；不在 MCP 进程执行任意脚本。

## 4. 任务与验收

- [ ] C1：实现既有数据/Release 验证；对内容变化、依赖/入口缺失、split 变化先写失败场景。
- [ ] C2：用预建两份配置验证不同摘要不同计划、旧 Plan 不能运行新内容。
- [ ] C3（扩展）：结构化 patch 只允许训练字段；门禁/入口/数据字段修改被拒绝。
- [ ] C4（扩展）：上传/finalize 相同请求返回原收据；摘要不符、越权前缀和未完成上传不可训练。
- [ ] C5（扩展）：隔离构建产生不可变 Release，输出测试证明；重复请求不重新构建已完成产物。

首版从服务根执行：`python -m unittest discover -s tests -p 'test_inputs.py' -v`。
扩展逐项增加对应 Python 测试；workload 测试在项目声明环境运行。
回退保留在跑 Job 引用的数据与 Release，不删除已冻结制品或用旧内容覆盖相同 ID。
