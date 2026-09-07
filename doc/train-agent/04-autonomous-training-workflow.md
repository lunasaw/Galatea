# 从需求到模型交付的 Python Codex 工作流

## 1. 入口与批准范围

用户给出任务、数据引用、验收标准和预算，Runner 形成需求草案。
[Campaign 样例](examples/campaign.example.yaml)表达后续 LoRA 场景，数值不是当前执行授权。
`Campaign` 只是一次有界训练任务的名称，不要求新增业务数据库或用户管理系统。

首次执行前，由受信入口把已有用户授权登记到 MCP 的任务清单：项目、数据/Release、可变参数、
objective/direction、Trial/重试上限、资源/时长、最终评价规则和交付形式。
Agent 可以建议这些字段，但不能给自己的草案授予权限。已明确批准的范围内连续推进，
不要求用户逐 Trial 再确认；缺少验收口径或超出预算才请求补充。

首版使用已注册项目、既有数据和预建配置。全新数据/模板入口是
[方案 06](implementation/06-data-and-releases.md)的扩展，不以改造旧插件开路。

## 2. 阶段、判断与执行

| 阶段 | Codex + Skill 的工作 | Python MCP / workload 的工作 |
| --- | --- | --- |
| 检查 | 理解任务、判断输入是否完整 | 校验数据、split、固定入口、Release、环境与批准范围 |
| 基线 | 选择批准目录中的简单基线和评价方法 | Ray 执行基线，MLflow 记录训练/验证证据 |
| Trial | 提出允许的配置和种子 | plan → 持久提交记录 → Ray Job，绑定 MLflow Run |
| 等待 | 输出 wait_external，结束 Turn | Runner 轮询真实状态，不调用模型 |
| 分析 | 比较兼容 Run、曲线、失败原因与剩余额度 | 返回有限指标/Artifact，拒绝跨 cohort 混排 |
| 优化 | 在冻结空间内选择下一配置或停止 | 检查 Trial 数、重试次数和最终阶段预算保留 |
| 冻结候选 | 根据验证证据提出唯一候选 | 固定 config/Release/seed/选择依据，拒绝后续换候选 |
| 干净重训 | 解释选定配置和重训方式 | 从批准初始模型重建训练状态，执行 Champion |
| 最终测试 | 解读已冻结协议的最终结果 | 独立评价入口访问 holdout，一次评价并记录使用标记 |
| 交付 | 说明效果、限制和推理方法 | 校验 Artifact、质量门禁、谱系，生成可核验报告引用 |

大规模数据处理、基线推理、重训和评价同样按有界 Job 管理，不把它们排除在资源预算之外。
如果项目契约损坏，返回 not-ready；不得改成本地直接运行正式训练。

## 3. 数据与评价规则

冻结原始数据 manifest、对象版本/摘要、split、预处理版本和评价协议。
按任务选择确定性切分，对话按用户/会话组，时序按时间；检测跨集重复。
需要拟合的预处理仅在 train 拟合，再应用于冻结的 validation/test。

模型搜索只使用训练/验证证据；Agent、Trial 进程及其凭据不能读取 holdout。
先冻结候选再干净重训，最后一次测试，质量阈值不能在失败后降低。
现有 workload 若把测试内联到训练，需单独评审如何满足隔离，不能将路径中的 role 分支当作权限。

最终测试使用标记保存在 MCP 受保护状态卷，以登记的 holdout 身份唯一创建；
不因为更换 Campaign/Thread 或协议名字重置。该标记是少量不可覆盖文件，不建设通用评估账本服务。
详见 [方案 07](implementation/07-workload-and-evaluation.md)。
测试失败交付 best-effort 或 blocked，不把同一 holdout 再用于自动搜索。
计算是否已经开始/披露不明时不自动重跑最终评价；已有结果只重新读取。

## 4. 方法与 LoRA 分期

平台和 Skill 保持任务中立：表格任务可比较简单模型与树模型，感知任务可用迁移学习，
生成任务先验证 Base/Prompt 等基线，再判断是否需要 SFT/LoRA。
选型只在批准模型、环境、数据用途和预算范围内进行，LLM loss 不能替代业务指标。

真实 SFT/LoRA 接入必须明确：

1. base model revision/摘要、tokenizer、chat template、许可证和硬件兼容性。
2. 对话分组切分、assistant-only loss mask、截断/packing、多轮边界与脱敏。
3. 固定业务评价器、rubric、解码参数；若用外部 judge，将调用成本和数据外发计入授权。
4. 在允许范围比较学习率、步数、rank/alpha、batch 等；所有字段写入不可变配置。
5. 从批准 base 干净初始化 Adapter/optimizer 重训；交付 Adapter 时同时给出 base、模板和加载方法。

当前 [llm-lora-playground](../../train-model/llm-lora-playground/README.md) 是接入起点。
一个 Toy smoke 不证明真实客服微调可用；硬件、业务评价和 Artifact 恢复分别验收。

## 5. 有限搜索与预算

首版单 Runner、单活跃 Campaign、串行 Job，使用批准实验槽位和配置列表。
调参逻辑属于 Skill/经过批准的训练入口；Runner 不实现另一套搜索算法。
首版不启用内嵌自动 Tune 搜索，避免一个父 Job 暗中运行未登记子 Trial。

MCP 按每次计算的最大批准资源时长保守累计占用；重试也占额度，网络重传不新增计算。
搜索先扣除最终重训/评价保留额。未知是否执行时不释放额度；真实计量不足时明确报告未知。
Ray 资源声明不等于费用硬上限，workload deadline 和 MCP 后台超时检查共同限时。

停止条件：达到冻结目标、Trial/重试/时长/模型调用上限、连续兼容 Trial 改善不足，
或数据、资源和授权不足。指标改善按 max/min 方向计算，不能混排不同 split 或评价协议。
OOM 等参数调整仅在批准配置中选择；越界调整先补充配置和授权。

## 6. 交付与结果

| 结果 | 条件 |
| --- | --- |
| accepted | 冻结质量门禁、最终评价、Artifact 恢复与交付清单全部通过 |
| best-effort | 有可下载且完整性合格的候选，但质量未达标或必需评价未完成；明确缺项 |
| blocked | 契约无效、证据无法恢复或无可用产物，当前不能继续 |
| cancelled | 用户取消，活动执行已结束或按明确保留策略完成处理 |

交付包含模型/Adapter URI 与摘要、加载示例、环境、输入输出和预处理、实验比较、
单独列示的最终测试、实际/保守预算说明、data/split/code/config/seed/Release/Run IDs。
模型自报 accepted 不能生效，MCP 从真实证据生成可核验结果；模型额度耗尽也可用确定性模板交付已有证据。
没有训练的方案只有在用户已批准对应交付合同且同等评价通过时才可 accepted。
生产推广单独处理，训练闭环不自动更新 Alias。
