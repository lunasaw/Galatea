# Stage 1 Missing Capabilities Analysis

**Date**: 2026-08-12  
**Context**: Stage 1 should build ALL skeletal structures, not just implement one complete feature

## Current State

Stage 1 has implemented:
- ✅ Runtime layer (`GalateaRuntime` - ClaudeSDKClient wrapper)
- ✅ 5 inspection tools (read-only MCP tools)
- ✅ Basic schemas (`common.py`, `inspection.py`)
- ✅ Config loading from `~/.claude/settings.json`
- ✅ Demo and test structure
- ✅ Empty placeholder directories

**Problem**: Stage 1 only built one vertical slice (inspection) but is missing skeletal structures for most agent capabilities.

## Missing Skeletal Structures

### 1. Agent Definitions (`agent/agents/`)

**Currently**: Only `__init__.py`

**Should have skeleton files**:
```python
# coordinator.py - PlatformCoordinator
# data_agent.py - DataAgent
# training_agent.py - TrainingAgent  
# inference_agent.py - InferenceAgent
```

Each should define:
- Agent prompt/system message
- Allowed tool list
- Output schema
- Stage boundaries
- Error handling patterns

**Reference**: Claude SDK `AgentDefinition` pattern

---

### 2. Workflow Orchestration (`agent/workflows/`)

**Currently**: Only `__init__.py`

**Should have**:
```python
# orchestrator.py - Multi-agent coordination
# pipeline.py - Stage sequencing (data → training → inference)
# resumption.py - Resume from checkpoint/failure
# parallel.py - Parallel agent execution patterns
```

Each should define:
- Workflow state machine
- Inter-agent communication
- Checkpoint/resume logic
- Result aggregation

**Reference**: Claude SDK multi-agent patterns, workflow composition

---

### 3. State Management (`agent/state/`)

**Currently**: Only `__init__.py`

**Should have**:
```python
# session.py - Session storage and retrieval
# experiment.py - Experiment state tracking
# persistence.py - State serialization/deserialization
# cache.py - Tool result caching
```

Key capabilities:
- `session_id` management
- Transcript storage
- Resume/fork support
- Experiment context tracking

**Reference**: Claude SDK `session_store`, `ResultMessage.session_id`

---

### 4. Permission Hooks (`agent/hooks/`)

**Currently**: Only `__init__.py`

**Should have**:
```python
# permissions.py - can_use_tool hook implementation
# validation.py - Output validation hooks
# logging.py - Audit trail hooks
# budget.py - Cost/token limit hooks
```

Key patterns:
- Tool permission checks
- Structured output validation
- Audit logging
- Budget enforcement

**Reference**: Claude SDK hooks system, `can_use_tool` pattern

---

### 5. Policy Layer (`agent/policies/`) **← MISSING DIRECTORY**

**Currently**: Does not exist

**Should have**:
```python
# budgets.py - Token/cost limit policies
# approvals.py - Human approval flow definitions
# quality_gates.py - Validation gate policies
# permissions.py - Tool access control policies
```

Key concepts:
- Budget thresholds (low/medium/high risk)
- Approval workflows
- Quality gate definitions
- Permission rules

**Reference**: Platform governance requirements from architecture doc

---

### 6. Tool Layer Expansion (`agent/tools/`)

**Currently**: Only `inspection.py` (5 tools) and `server.py`

**Should have skeleton files**:
```python
# ray_jobs.py - submit_ray_job, get_ray_job_status
# ray_data.py - propose_ray_data_plan, submit_ray_data_job, validate_dataset_output
# mlflow_tracking.py - inspect_mlflow_runs, log_metrics, log_params
# artifacts.py - log_artifact, download_artifact, list_artifacts
# registry.py - register_model, get_model_version, update_model_alias
# validation.py - validate_dataset, verify_checkpoint, run_smoke_inference
# approval.py - request_approval, check_approval_status
```

Tool categories:
- **inspect** - Read-only platform queries
- **propose** - Plan generation (no side effects)
- **submit** - Job submission (idempotent)
- **status** - Status polling
- **validate** - Quality checks
- **log** - Artifact/metric logging
- **approval** - Human-in-the-loop

**Reference**: Architecture doc Section 3.3 Tool Layer

---

### 7. Schema Layer Completion (`agent/schemas/`)

**Currently**: `common.py`, `inspection.py`

**Should have**:
```python
# data.py - DataStageInput, DataStageResult, DataManifest
# training.py - TrainingStageInput, TrainingStageResult, TrainingConfig
# inference.py - InferenceStageInput, InferenceStageResult, ServingPlan
# workflows.py - WorkflowState, StageTransition
```

Key structures:
- Stage input/output schemas
- Manifest formats
- Config schemas
- Workflow state

**Reference**: Architecture doc Section 5 (execution flows)

---

### 8. CLI Entry Points (`agent/scripts/`)

**Currently**: Only `__init__.py`

**Should have**:
```python
# run_data_agent.py - CLI for data stage
# run_training_agent.py - CLI for training stage
# run_inference_agent.py - CLI for inference stage
# inspect_platform.py - Platform health check CLI
# analyze_experiment.py - MLflow experiment analysis CLI
```

Each should:
- Parse CLI arguments
- Load configuration
- Initialize runtime
- Execute agent workflow
- Save structured results

**Reference**: Platform integration requirements

---

### 9. Services Layer (`agent/services/`)

**Currently**: Only `__init__.py` (not in original architecture doc)

**Purpose unclear** - Should clarify:
- Service discovery?
- Health checking?
- Service client wrappers?

**Recommendation**: Define purpose or remove if redundant with tools/

---

### 10. Additional Missing Components

**Runtime enhancements**:
- `runtime/messages.py` - Message collection and filtering
- `runtime/sessions.py` - Session management helpers
- `runtime/streaming.py` - Response streaming utilities

**Client layer**:
- `client.py` - Currently skeleton only, should have:
  - High-level `train_model()` API
  - `optimize_experiment()` API
  - Workflow composition helpers

**Utils** (exists but empty):
- `utils/logging.py` - Structured logging
- `utils/errors.py` - Custom exceptions
- `utils/validation.py` - Input validation helpers

---

## Comparison with Claude Agent SDK Capabilities

| Capability | Claude SDK Feature | Galatea Status |
|-----------|-------------------|----------------|
| Agent definitions | `AgentDefinition` | ❌ Missing skeleton files |
| Multi-agent workflows | Agent composition | ❌ Missing orchestrator |
| Session management | `session_id`, `session_store` | ❌ Missing state layer |
| Permission hooks | `can_use_tool` | ❌ Missing hook implementations |
| Budget limits | Cost tracking | ❌ Missing budget policies |
| Approval flows | Human-in-the-loop | ❌ Missing approval tools |
| Structured output | `output_format` | ⚠️ Partial (only inspection schemas) |
| Tool registration | MCP server | ✅ Working (inspection only) |
| Streaming responses | `receive_response()` | ✅ Working |
| Resume/fork | Session continuation | ❌ Missing state management |

---

## Recommended Stage 1 Completion Plan

### Phase 1: Core Structures (High Priority)

1. **Agent definitions** - Create skeleton files with docstrings, empty methods, and type hints
2. **Schema completion** - Add data.py, training.py, inference.py with Pydantic models
3. **Tool expansion** - Add skeleton tool files with function signatures and docstrings
4. **Policies directory** - Create directory and skeleton files

### Phase 2: Integration Layers (Medium Priority)

5. **Workflow orchestrator** - Basic pipeline pattern and state machine
6. **State management** - Session storage interface
7. **Permission hooks** - Hook registration system
8. **CLI scripts** - Entry point templates

### Phase 3: Supporting Infrastructure (Low Priority)

9. **Runtime enhancements** - Message handling, session helpers
10. **Utils** - Logging, errors, validation utilities
11. **Client layer** - Complete high-level API skeleton
12. **Documentation** - Update all skeleton files with usage examples

---

## Success Criteria for Stage 1

Stage 1 should be considered complete when:

1. ✅ All directories exist with clear purpose
2. ✅ All major component files exist with:
   - Module docstrings explaining purpose
   - Class/function skeletons with type hints
   - NotImplementedError with "Future: Stage N" comments
   - Examples in docstrings showing intended usage
3. ✅ Import paths work (`from agent.agents import DataAgent`)
4. ✅ Type checking passes (mypy)
5. ✅ Documentation describes complete architecture
6. ✅ One end-to-end demo works (inspection, already done)

**Key principle**: Stage 1 = skeleton of ALL capabilities, not implementation of ONE capability.

---

## References

- Architecture doc: `agent/doc/current-agent-architecture.md`
- Claude SDK: `/data/ai/chenzhangyue/code/claude-agent-sdk-python`
- Platform contracts: `CLAUDE.md`, `README.md`
- Current README: `agent/README.md`
