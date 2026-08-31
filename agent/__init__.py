"""
Galatea Agent System

Python-based agent orchestration for the Galatea ML Training Platform,
built on Claude Agent SDK.

## Architecture

```
User/Notebook/CLI
      ↓
GalateaRuntime (ClaudeSDKClient wrapper)
      ↓
MCP Server (galatea-platform)
      ↓
Tools → Platform Services
      ↓
Ray / MLflow / MinIO
```

## Key Components

### Runtime Layer
- `agent.core`: SDK runtime primitives, configuration, and result models
- `GalateaRuntime`: Async context manager wrapping ClaudeSDKClient
- `GalateaAgentClient`: High-level client for common operations

### Tool Layer
- MCP Server with platform-specific tools
- Tool categories: inspect, propose, submit, status, validate, log

### Schema Layer
- Common types: StageResult, ArtifactRef, StageEvidence
- Stage-specific: InspectionResult, DataStageResult (future), etc.

### State Management
- `AgentStateStore`: Galatea application state storage
- `ExperimentState`: Experiment workflow state tracking

### Hooks System
- SDK hook inputs/outputs and supported events
- Permission control, approval evidence, audit logging, validation

### Policies
- `BudgetPolicy`: Token/cost budget enforcement
- `PermissionPolicy`: Tool access control
- `QualityGatePolicy`: Stage validation gates

### Workflows
- `WorkflowStateMachine`: State management and transitions
- `WorkflowOrchestrator`: Deterministic state/evidence registry only

### Agent Definitions
- `AgentDefinition`: Agent configuration framework
- Predefined agents: Inspection, Data, Training, Inference

## Quick Start

### Basic Usage

```python
import asyncio
from pathlib import Path
from agent.runtime import GalateaRuntime

async def main():
    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    async with GalateaRuntime(project_root=project_root) as runtime:
        result = await runtime.inspect_platform()
        print(result["response"])

asyncio.run(main())
```

### CLI Usage

```bash
# Platform inspection
python agent/scripts/inspect_platform.py

# Direct tool testing (no API calls)
python agent/test/test_tools_direct.py

# Full agent demo
python agent/demo/demo_basic.py
```

## Configuration

Set up API configuration in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key",
    "ANTHROPIC_BASE_URL": "https://ai.vdian.net/api/"
  }
}
```

## Implementation Status

### ✅ Stage 1: Engineering Infrastructure (Current)

**Completed**:
- Runtime layer with ClaudeSDKClient integration
- Basic tool layer (5 inspection tools)
- Schema layer (common types, inspection results)
- Config loading from ~/.claude/settings.json
- Demo and test structure

**Current reusable components**:
- Galatea application state (`AgentStateStore`, `ExperimentState`)
- SDK hook manager and built-in safety hooks
- Budget, permission, and quality policies
- Workflow state machines and evidence helpers
- SDK `AgentDefinition` presets and registry
- Utility modules and CLI entry points

### 🚧 Future Stages

- **Stage 2**: DataAgent implementation with Ray Data
- **Stage 3**: TrainingAgent with Ray Train/Jobs
- **Stage 4**: InferenceAgent with model evaluation
- **Stage 5**: Business promotion approval integration
- **Stage 6**: Code maintenance agent

## Module Structure

```
agent/
├── core/                   # SDK runtime primitives
│   ├── __init__.py         # Core exports
│   └── sdk.py              # GalateaSDKRuntime implementation
├── runtime.py              # GalateaRuntime - Claude SDK wrapper
├── client.py               # High-level SDK client
├── tools/                  # MCP tool implementations
│   ├── server.py           # MCP server factory
│   └── inspection.py       # Read-only tools (✅ implemented)
├── schemas/                # Pydantic models
│   ├── common.py           # Shared types (✅)
│   └── inspection.py       # Inspection results (✅)
├── state/                  # State management
│   ├── store.py            # AgentStateStore interface
│   ├── experiment.py       # ExperimentState manager
│   └── persistence.py      # Persistence helpers
├── hooks/                  # Hook system
│   ├── types.py            # SDK types plus Galatea audit context
│   ├── registry.py         # Thin SDK HookMatcher registry
│   └── builtin.py          # Built-in safety hooks
├── policies/               # Policy framework
│   ├── budget.py           # Budget policy
│   ├── permission.py       # Permission policy
│   └── quality.py          # Quality gates
├── workflows/              # Workflow state/evidence tracking
│   ├── state_machine.py    # State machine
│   └── orchestrator.py     # Orchestrator
├── agents/                 # Agent definitions
│   ├── definitions.py      # SDK AgentDefinition presets
│   └── registry.py         # SDK-native Agent registry
├── utils/                  # Utilities
│   ├── errors.py           # Custom exceptions
│   ├── logging.py          # Structured logging
│   └── validation.py       # Input validation
├── config/                 # Configuration
│   └── loader.py           # Config loading (✅)
├── demo/                   # Demo scripts
├── test/                   # Test scripts
├── scripts/                # CLI entry points; planned stages return unsupported status
└── doc/                    # Documentation
```

## Design Principles

1. **Platform API Only**: Use MLflow/Ray/MinIO APIs, never direct database access
2. **Structured Output**: All stage results use Pydantic schemas
3. **Idempotent Operations**: Ray jobs and data operations are retry-safe
4. **Minimal Permissions**: Read-only by default, explicit approval for destructive actions
5. **Audit Trail**: All tool calls, permissions, and approvals are logged

## References

- [Agent README](agent/README.md) - Detailed documentation
- [Agent Architecture](agent/doc/current-agent-architecture.md) - Design document
- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
- [Platform README](../README.md) - Galatea platform overview
"""

from agent.runtime import GalateaRuntime
from agent.client import GalateaAgentClient
from agent.core import (
    AgentSDKConfig,
    ContextCompressionConfig,
    GalateaSDKRuntime,
    SDKRunResult,
    SkillPreflightReport,
    SkillRegistry,
    SkillSpec,
)

# Schema exports
from agent.schemas.common import (
    StageResult,
    StageStatus,
    ArtifactRef,
    StageEvidence,
    ApprovalRequest,
)
from agent.schemas.inspection import InspectionResult

# State management (interfaces)
from agent.state import (
    AgentStateStore,
    InMemoryAgentStateStore,
    SessionStore,
    MemorySessionStore,
    SessionManager,
    ExperimentState,
    ExperimentStage,
)

# Hooks (interfaces)
from agent.hooks import (
    GalateaHookContext,
    HookEvent,
    HookContext,
    HookCallback,
    HookManager,
)

# Policies (interfaces)
from agent.policies import (
    BudgetPolicy,
    PermissionPolicy,
    QualityGatePolicy,
)

# Workflows (interfaces)
from agent.workflows import (
    WorkflowState,
    WorkflowDefinition,
    WorkflowOrchestrator,
)

# Agent definitions - pre-defined agents only
# Note: AgentDefinition comes from claude_agent_sdk, not from agent.agents
from agent.agents import (
    PLATFORM_INSPECTOR,
    DATA_PREPARER,
    TRAINING_ORCHESTRATOR,
    MODEL_EVALUATOR,
    EXPERIMENT_ANALYZER,
    DOCUMENTATION_GENERATOR,
)

__version__ = "0.1.0-stage1"

__all__ = [
    # Runtime
    "GalateaRuntime",
    "GalateaAgentClient",
    "AgentSDKConfig",
    "ContextCompressionConfig",
    "GalateaSDKRuntime",
    "SDKRunResult",
    "SkillPreflightReport",
    "SkillRegistry",
    "SkillSpec",
    # Schemas
    "StageResult",
    "StageStatus",
    "ArtifactRef",
    "StageEvidence",
    "ApprovalRequest",
    "InspectionResult",
    # State
    "AgentStateStore",
    "InMemoryAgentStateStore",
    "SessionStore",
    "MemorySessionStore",
    "SessionManager",
    "ExperimentState",
    "ExperimentStage",
    # Hooks
    "GalateaHookContext",
    "HookEvent",
    "HookContext",
    "HookCallback",
    "HookManager",
    # Policies
    "BudgetPolicy",
    "PermissionPolicy",
    "QualityGatePolicy",
    # Workflows
    "WorkflowState",
    "WorkflowDefinition",
    "WorkflowOrchestrator",
    # Agents - pre-defined
    "PLATFORM_INSPECTOR",
    "DATA_PREPARER",
    "TRAINING_ORCHESTRATOR",
    "MODEL_EVALUATOR",
    "EXPERIMENT_ANALYZER",
    "DOCUMENTATION_GENERATOR",
]
