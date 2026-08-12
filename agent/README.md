# Galatea Agent System

Python-based agent orchestration for the Galatea ML Training Platform, built on Claude Agent SDK.

## Architecture Overview

```
User/Notebook/CLI
       ↓
GalateaRuntime (ClaudeSDKClient wrapper)
       ↓
MCP Server (galatea-platform)
       ↓
Inspection Tools → Platform Services
       ↓
Ray / MLflow / MinIO
```

## Components

### Runtime Layer

- **`GalateaRuntime`**: Async context manager wrapping ClaudeSDKClient
- **Configuration**: Model selection, MCP server registration, permission mode
- **Session Management**: Query execution and response streaming

### Tool Layer

- **MCP Server**: In-process SDK MCP server with platform-specific tools
- **Inspection Tools**: Read-only operations for platform state
- **Tool Categories**: inspect, validate, submit, status (future stages)

### Schema Layer

- **Common Types**: `StageResult`, `ArtifactRef`, `StageEvidence`, `ApprovalRequest`
- **Stage-Specific**: `InspectionResult`, `DataStageResult`, `TrainingStageResult` (future)
- **Validation**: Pydantic models with strict typing

## Implementation Status

### ✅ Stage 1: Read-only Runtime POC (Complete)

**Implemented**:
- Claude SDK integration with ClaudeSDKClient
- In-process MCP server with 5 inspection tools
- Streaming query/response handling
- Permission mode enforcement
- Basic schemas and types

**Tools**:
- `list_training_projects` - List all projects in train-model/
- `inspect_project_structure` - Inspect configs, scripts, tests
- `check_service_health` - Query systemd service status
- `inspect_mlflow_experiment` - Get experiment metadata
- `inspect_ray_status` - Check Ray cluster availability

**Validation**: ✅ Demo runs successfully, tools functional, permission boundaries enforced

### 🚧 Stage 2: DataAgent with Ray Data POC (Next)

**Planned Tools**:
- `inspect_dataset_source`
- `compute_source_manifest`
- `propose_ray_data_plan`
- `submit_ray_data_job`
- `validate_dataset_output`
- `log_dataset_manifest`

**Target**: Complete data preparation workflow with structured `DataStageResult`

### 📋 Future Stages

- **Stage 3**: TrainingAgent (check-config/plan/smoke)
- **Stage 4**: InferenceAgent (smoke inference, serve plans)
- **Stage 5**: Approval workflow integration
- **Stage 6**: Code maintenance agent

## Quick Start

### Installation

```bash
# Activate conda environment
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

# Verify Claude SDK
python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
```

### Direct Tool Usage

```python
from agent.tools.inspection import inspect_project_structure

result = inspect_project_structure(
    project_root="/data/ai/chenzhangyue/code/galatea",
    project_name="ray-cats-and-dogs"
)
print(f"Config files: {result['config_files']}")
```

### Runtime Usage

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

### Run Demo

```bash
# Full agent demo with Claude API
python agent/demo_basic.py

# Direct tool test (no API calls)
python agent/test_tools_direct.py
```

## Project Structure

```
agent/
├── README.md                    # This file
├── STAGE1_COMPLETE.md           # Stage 1 completion report
├── __init__.py                  # Package exports
├── runtime.py                   # GalateaRuntime implementation
├── client.py                    # High-level client (future)
├── demo_basic.py                # Stage 1 demo script
├── test_tools_direct.py         # Direct tool testing
├── doc/                         # Architecture documentation
│   ├── current-agent-architecture.md
│   ├── implementation-roadmap.md
│   ├── stage-contracts-and-tools.md
│   └── ...
├── schemas/                     # Pydantic data models
│   ├── __init__.py
│   ├── common.py                # Shared types
│   └── inspection.py            # Inspection results
├── tools/                       # MCP tools
│   ├── __init__.py
│   ├── server.py                # MCP server factory
│   └── inspection.py            # Read-only tools
├── agents/                      # Agent definitions (future)
│   └── __init__.py
├── config/                      # Configuration (future)
│   └── __init__.py
├── state/                       # Session management (future)
│   └── __init__.py
├── workflows/                   # Multi-stage workflows (future)
│   └── __init__.py
└── hooks/                       # Permission/policy hooks (future)
    └── __init__.py
```

## Design Principles

### 1. Minimal Permissions by Default

- Read-only tools in Stage 1
- Explicit approval for destructive actions
- Permission mode controls tool access
- No automatic production changes

### 2. Platform API Only

- Never read `mlflow.db` directly
- Use MLflow Tracking/Artifact APIs
- Ray Jobs API for submission
- MinIO S3 API for artifacts

### 3. Structured Output

- All stage results use Pydantic schemas
- JSON-serializable for storage/audit
- Versioned artifact references
- Evidence-based decisions

### 4. Idempotent Operations

- Ray jobs with deterministic submission IDs
- Data manifests with content digests
- Create-only artifact writes
- Retry-safe MLflow runs

### 5. Audit Trail

- Stage run IDs for tracking
- Tool call logging
- Permission denials recorded
- Cost and usage metrics

## Configuration

### Anthropic API Configuration

**Recommended: Use `~/.claude/settings.json`**

The runtime automatically loads API configuration from `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key",
    "ANTHROPIC_BASE_URL": "https://ai.vdian.net/api/"
  }
}
```

**Alternative: Environment Variables**

```bash
# Claude API authentication (required)
export ANTHROPIC_API_KEY="your-api-key"

# Custom API endpoint (optional, for proxies like OpenRouter)
export ANTHROPIC_BASE_URL="https://your-custom-endpoint.com/api/"
```

**Priority**: Environment variables > settings.json

See [Configuration Guide](doc/configuration.md) for more details.

### Platform Endpoints

```bash
# Optional overrides (defaults shown)
export MLFLOW_TRACKING_URI="http://127.0.0.1:5000"
export RAY_ADDRESS="auto"  # or "ray://127.0.0.1:10001"
export MINIO_ENDPOINT="http://127.0.0.1:9000"
```

### Runtime Options

```python
GalateaRuntime(
    project_root=Path("/data/ai/chenzhangyue/code/galatea"),
    mlflow_tracking_uri="http://127.0.0.1:5000",
    model="claude-opus-4-20250514",  # or "claude-sonnet-4-20250514"
    auto_load_config=True,  # Default: auto-load from settings.json
)
```

## Testing

### Unit Tests (Future)

```bash
python -m unittest discover -s agent/tests
```

### Integration Tests

```bash
# Test tools directly
python agent/test_tools_direct.py

# Test with live Claude API (costs $)
python agent/demo_basic.py
```

## Monitoring

### Model Request/Response Logging

**All model requests and responses are automatically logged in single-line JSON format without truncation.**

Enable logging in your code:
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

Log format:
```
MODEL_REQUEST: {"type":"request","model":"claude-opus-5","timestamp":"2026-08-12T10:00:00","prompt":"...","output_schema":null}
MODEL_RESPONSE: {"type":"response","timestamp":"2026-08-12T10:00:01","message":{...}}
```

Features:
- **Complete serialization**: Full prompts and responses (no truncation)
- **Single-line format**: Easy to parse with grep/jq
- **Structured data**: JSON with timestamps, model info, and content
- **Audit trail**: Track all agent interactions

See [Logging Documentation](doc/logging.md) for details on log format, parsing, and security considerations.

### Cost Tracking

Each query returns usage metrics:
```python
result = await runtime.inspect_platform()
# ResultMessage includes:
# - total_cost_usd: 0.469
# - usage: {input_tokens, output_tokens, cache_read_input_tokens, ...}
# - num_turns: 12
# - duration_ms: 73723
```

### Permission Denials

Check `permission_denials` field in ResultMessage for blocked tool calls.

## Troubleshooting

### MCP Tools Not Available

**Symptom**: Tools not showing in agent's tool list

**Fix**: Verify MCP server registration:
```python
async with GalateaRuntime(...) as runtime:
    # Check runtime._client options
    print(runtime.mcp_server)  # Should show McpSdkServerConfig
```

### Permission Denied Errors

**Symptom**: All tool calls blocked in `dontAsk` mode

**Fix**: This is expected in Stage 1. Future stages will use `acceptEdits` or custom `can_use_tool` hooks.

### Import Errors

**Symptom**: `ModuleNotFoundError: No module named 'agent'`

**Fix**: Run from galatea root directory or adjust sys.path:
```python
import sys
sys.path.insert(0, "/data/ai/chenzhangyue/code/galatea")
```

## Contributing

### Adding New Tools

1. Implement function in `agent/tools/your_module.py`
2. Define with `@tool` decorator:
   ```python
   @tool("tool_name", "description", {"param": str})
   async def your_tool(args: Dict[str, Any]) -> Dict[str, Any]:
       result = your_implementation(args["param"])
       return {"content": [{"type": "text", "text": json.dumps(result)}]}
   ```
3. Add to MCP server in `tools/server.py`
4. Test directly before adding to agent

### Adding New Schemas

1. Create Pydantic model in `agent/schemas/`
2. Add to `__init__.py` exports
3. Use for `output_schema` in runtime.query()

## References

- [Current Agent Architecture](doc/current-agent-architecture.md)
- [Implementation Roadmap](doc/implementation-roadmap.md)
- [Stage Contracts and Tools](doc/stage-contracts-and-tools.md)
- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
- [Galatea Platform README](../README.md)

## License

Internal project - Galatea ML Training Platform
