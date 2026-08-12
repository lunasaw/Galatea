# Stage 1 工程能力补全计划（基于 Claude SDK 源码分析）

## 从 Claude SDK 源码学到的工程能力

### 核心发现

Claude SDK 的工程能力主要在这几个方面：

1. **Session Management** (`_internal/sessions.py`, `_internal/session_store.py`)
   - Session 存储抽象接口
   - Session 恢复、导入、变更
   - 多种存储后端（Redis、S3、Postgres）

2. **Hooks System** (`examples/hooks.py`)
   - PreToolUse, PostToolUse, UserPromptSubmit, SessionStart
   - Permission control (allow/deny)
   - Stop/continue control
   - System message injection

3. **Budget Control** (`examples/max_budget_usd.py`)
   - max_budget_usd 参数
   - Cost tracking in ResultMessage
   - Budget exceeded handling

4. **Agent Definitions** (`types.py`, `examples/agents.py`)
   - AgentDefinition dataclass
   - Tools/disallowedTools
   - Model override
   - Memory scope (user/project/local)
   - Permission mode

5. **Permission System** (`types.py`)
   - PermissionUpdate dataclass
   - Permission rules (add/replace/remove)
   - Permission behavior (allow/deny/ask)
   - Permission mode (default/acceptEdits/plan/bypassPermissions/dontAsk/auto)

6. **Types & Protocols** (`types.py`)
   - SystemPromptPreset, SystemPromptFile
   - TaskBudget
   - HookContext, HookInput, HookJSONOutput
   - PermissionRuleValue

## Stage 1 应该补全的骨架

### 重点：工程能力，不是业务逻辑

| 模块 | 当前状态 | 应该补充的骨架 |
|------|----------|---------------|
| **state/** | 空 | SessionStore 接口、存储抽象 |
| **hooks/** | 空 | Hook 注册系统、Hook 类型定义 |
| **policies/** | 不存在 | Budget、Permission、Quality Gate 策略 |
| **workflows/** | 空 | Workflow 状态机、编排器抽象 |
| **utils/** | 存在但空 | Logging、Errors、Validation 工具 |

### 不应该在 Stage 1 做的

- ❌ DataAgent/TrainingAgent/InferenceAgent 的具体实现
- ❌ 完整的 Ray Data/Ray Train 集成
- ❌ MLflow 工具的详细实现
- ❌ 具体业务流程的 Workflow

### 应该在 Stage 1 做的

- ✅ SessionStore 抽象接口（参考 SDK 的 session_store.py）
- ✅ Hook 系统骨架（参考 SDK 的 hooks）
- ✅ Budget/Permission 策略框架（参考 SDK 的 types.py）
- ✅ Agent 定义模式（参考 SDK 的 AgentDefinition）
- ✅ 工具注册和管理机制
- ✅ 错误处理和日志框架

## 具体要补充的文件

### 1. state/ - Session Management（参考 SDK）

```
state/
├── __init__.py
├── store.py           # SessionStore 抽象接口
├── memory.py          # 内存存储实现
└── persistence.py     # 持久化辅助函数
```

### 2. hooks/ - Hook System（参考 SDK hooks.py）

```
hooks/
├── __init__.py
├── registry.py        # Hook 注册和管理
├── types.py          # Hook 类型定义
└── builtin.py        # 内置 hooks（logging, cost tracking）
```

### 3. policies/ - Policy Framework（参考 SDK）

```
policies/
├── __init__.py
├── budget.py         # Budget policy（参考 max_budget_usd）
├── permission.py     # Permission policy（参考 PermissionUpdate）
└── quality.py        # Quality gate framework
```

### 4. workflows/ - Workflow Orchestration

```
workflows/
├── __init__.py
├── state_machine.py  # Workflow 状态机
└── orchestrator.py   # Workflow 编排抽象
```

### 5. utils/ - Utilities

```
utils/
├── __init__.py
├── errors.py         # 自定义异常
├── logging.py        # 结构化日志
└── validation.py     # 输入验证
```

### 6. agents/ - Agent Definition Framework（不是具体 agent）

```
agents/
├── __init__.py
├── definition.py     # AgentDefinition（参考 SDK）
└── registry.py       # Agent 注册和发现
```

## 成功标准

Stage 1 完成时应该能够：

1. ✅ 定义一个 Agent（使用 AgentDefinition）
2. ✅ 注册 Hooks（PreToolUse, PostToolUse）
3. ✅ 设置 Budget 限制
4. ✅ 配置 Permission 策略
5. ✅ 保存/恢复 Session 状态
6. ✅ 记录结构化日志
7. ✅ 抛出自定义异常

但**不需要**：
- ❌ 实际运行完整的 Data/Training/Inference 流程
- ❌ 调用真实的 Ray/MLflow API
- ❌ 实现复杂的业务逻辑

## 下一步行动

1. 删除之前创建的具体 Agent 文件（coordinator.py, data_agent.py 等）
2. 按照上述结构补全工程能力骨架
3. 每个文件只需要：
   - 接口定义（Protocol/ABC）
   - 类型定义（TypedDict/dataclass）
   - 占位符实现（NotImplementedError）
   - 清晰的文档字符串

确认这个理解是否正确？
