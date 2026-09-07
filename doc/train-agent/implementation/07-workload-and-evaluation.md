# 方案 07：Workload、最终评价与交付实施计划

> 后续实现使用 superpowers:executing-plans；模型代码与测试遵守 model-project-structure。

**目标：**先完成一个契约完整 workload 的训练/评价闭环，再接真实 LoRA。
**架构：**Python MCP 检查阶段和证据，workload 执行计算，MLflow 保存 Run 与 Artifact。
**技术栈：**项目 Python、Ray、MLflow；少量候选字段和不可覆盖测试使用文件。
**共同约束：**遵守 [实施索引](README.md)，验证集选优、干净重训、一次最终测试，不改旧插件。

## 1. 文件与接入边界

| 拟定位置 | 职责 |
| --- | --- |
| services/galatea-mcp/src/galatea_mcp/stages.py | 阶段、固定槽位、候选冻结 |
| services/galatea-mcp/src/galatea_mcp/evaluation.py | holdout 使用标记、评价计划 |
| services/galatea-mcp/src/galatea_mcp/delivery.py | 基于真实 API 证据判定结果和报告 |
| services/galatea-mcp/tests/test_evaluation.py、test_delivery.py | 重复评价、伪造证据、交付完整性 |
| 新接入项目的 galatea.project.yaml、scripts/evaluate.py、项目测试 | 固定评价入口和角色/数据边界 |
| 后续 llm-lora-playground 的项目内配置/评价/跟踪/测试 | 真实 LoRA 合同，独立评审 |

先参考成熟 digits/表格项目的 manifest 与 Python 入口，不修改它们的旧执行行为来配合插件迁移。
若需要新增评价边界，用新的明确配置/Release，或独立接入 workload，保持旧插件所用 Release 和契约可用。
缺少独立评价能力的项目先不开放 accepted 终态，不能暗中改写原测试语义。

## 2. 阶段与候选

baseline → search → candidate_frozen → champion_training → final_evaluation → delivery。
MCP 根据当前阶段给出批准的 step_id；同一步的 re-plan/resume 沿用原身份。
候选冻结绑定唯一配置/Release、seed、数据/验证协议和真实 selection_evidence_digest。
同候选重复冻结返回原结果，最终评价开始后不能换候选或改阈值。

Champion 从批准初始模型和干净 optimizer 开始；如预先允许 train+validation 重训，
记录训练人口变化，不能把其训练指标与 Trial 验证指标混排。
Checkpoint 只用于同一次计算经批准的基础设施恢复，不用 Trial 权重替代干净重训。

## 3. 一次测试，不引入评估数据库

管理员登记不可变 holdout_identity，绑定真实测试人口/摘要，不能通过换别名、Campaign 或协议名重置。
最终评价前在 `<state-root>/evaluation-uses/<holdout-identity>.json` 独占创建标记，
内容包含 campaign_id、candidate_id、protocol_digest、operation_id 与 submission_id。
标记写入并 fsync 成功后，才保存 Campaign 的 evaluate 操作并允许外部执行。

顺序采用保守恢复：标记先于执行记录。若创建后崩溃，已有标记仍阻止另一个候选占用；
同身份可对账原 operation，损坏/半写标记阻止自动执行并保留给受信恢复入口处理。
不依赖跨文件原子事务；宁可保留未使用的测试资格，也不在不明状态下重复披露。
已有结果只读回，不再计算。标记不删除、不随着 Campaign 归档清理，需独立备份。
首版不自动重新运行已进入 submitting/unknown 的最终评价；先对账，无法确认就 blocked。

该保证仅覆盖此 MCP 的受控评价入口和登记身份，不能证明同一数据从未被旧流程或人读取。
接入前盘点历史使用，来源不明不得宣称 untouched holdout；共享外部评价要单独约定边界。

## 4. 数据与 Artifact 隔离

| 身份 | 可见内容 |
| --- | --- |
| Trial / Champion trainer | 批准 train/validation 视图、自己的 Artifact 写权限 |
| evaluator | 冻结 holdout、只读 Champion 产物、自己的 evaluation Run |
| Agent / Runner | 脱敏状态和阶段允许的证据，不含测试样本和后端凭据 |

训练和评价分别记录 Run，通过 manifest 关联；不要求无 test 凭据的 trainer 生成 test 指标。
真实隔离包括文件挂载、凭据、网络、Run/日志/Artifact 通道，不是路径 role 判断。
共享 Ray Job/namespace 不能单独承担不受信代码隔离；首版只运行受信 Release，
隔离 evaluator 数据，不受信生成代码另做执行环境设计。

模型下载/加载在独立受限环境，经 MLflow Artifact API 校验摘要、环境与固定公开/合成推理样例。
MCP 不执行任意模型反序列化代码。完整性不合格的产物不能作为 best-effort 交付。

## 5. 交付判定

accepted：所有必需质量门禁和最终评价通过，Artifact 完整可下载，加载/推理与谱系齐备。
best-effort：存在完整可用候选，但质量未达标或必需评价未完成；报告明确 final_test_status 和缺项。
blocked：没有可交付产物或契约/完整性无效。

MCP 生成确定性证据清单与报告引用，Agent 可补解释；模型额度耗尽不阻止已有证据交付。
生产推广不在首版工具清单，用户后续明确授权再设计/执行 Alias 更新。

## 6. 任务与验收

- [ ] E1：fake Run/Artifact 测试验证选优、同候选重放和阶段不允许换候选。
- [ ] E2：接入一个受信项目及独立评价 Release；项目测试验证数据视图和 train/test 指标语义。
- [ ] E3：测试独占 marker、跨 Campaign 冲突、标记后崩溃、损坏文件、丢提交响应，不重复最终评价。
- [ ] E4：通过 Artifact API 下载并核验模型，在独立环境加载固定合成样例，形成报告。
- [ ] E5：测试 Ray success 但 gate/Artifact 缺失不 accepted；损坏产物不可 best-effort。
- [ ] E6（LoRA 扩展）：补齐固定模型/环境、mask、业务 evaluator、硬件单步与 Adapter 恢复。

服务根执行：`python -m unittest discover -s tests -p 'test_evaluation.py' -v`，再 test_delivery.py。
项目测试按自己的 README/环境执行；真实训练只用明确小预算，不用最终测试调试搜索算法。
回退保留候选、使用标记和原 Run/Artifact，旧插件始终使用其现有配置与 Release。
