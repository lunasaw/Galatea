# Claude Code v2.1.88 - Architecture Documentation

> Abstracted from `/data/ai/chenzhangyue/code/claude-code-source-code`
> 
> Source: Decompiled TypeScript from `@anthropic-ai/claude-code` npm package v2.1.88
> 
> Date: 2026-08-12

---

## Overview

Claude Code is an AI-powered development environment that implements a production-grade agent loop with progressive harness mechanisms. The architecture follows a **Query Engine → Tools/Services/State** pattern with extensive telemetry, permission management, and multi-agent coordination capabilities.

**Key Architecture Characteristics:**
- Single bundled CLI (~12MB `cli.js`) compiled from TypeScript source
- React/Ink-based terminal UI with component-driven rendering
- 40+ specialized tools with granular permission controls
- Multi-agent coordination with sub-agent spawning
- Feature-gated modules (108 modules exist only in Anthropic's internal monorepo)
- Hourly remote configuration polling with killswitches
- Dual analytics pipeline (Anthropic + Datadog)

---

## Top-Level Directory Structure

```text
claude-code-source-code/
├── docs/                      # Quadrilingual analysis reports (EN/JA/KO/ZH)
├── scripts/                   # Build and deployment utilities
├── src/                       # Main TypeScript source (37 subdirectories)
├── stubs/                     # Type stubs for external dependencies
├── tools/                     # Standalone tool implementations
├── types/                     # Global TypeScript type definitions
├── utils/                     # Shared utility functions
├── vendor/                    # Native addons (audio, image, modifiers, URL handler)
├── package.json               # npm package manifest
├── tsconfig.json              # TypeScript compiler configuration
└── README.md                  # Source code analysis documentation
```

---

## Core Architecture (`src/`)

### Entry Points and Main Loop

| Module | Purpose | Key Responsibilities |
|--------|---------|---------------------|
| `entrypoints/` | CLI and server entry points | Process initialization, argument parsing, environment setup |
| `main.tsx` | Primary REPL loop (804KB) | Query engine orchestration, tool dispatch, state management |
| `QueryEngine.ts` | Agent query processor | LLM interaction, streaming, prompt construction |
| `query.ts` | Query utilities | Request formatting, response parsing |
| `replLauncher.tsx` | REPL UI initialization | Terminal setup, session management |

**Architecture Flow:**
```
Entry Point → Main REPL → Query Engine → Tool Dispatcher → Services → State Persistence
                ↓
         Terminal UI (Ink/React)
                ↓
         Permission Dialogs
```

### Command System (88+ Commands)

Located in `src/commands/` with 88+ individual command implementations:

**Core Commands:**
- **Session Management**: `clear`, `exit`, `context`, `compact`, `export`, `history`
- **Development**: `diff`, `files`, `branch`, `issue`, `config`, `env`
- **Agent Control**: `agents`, `bridge`, `coordinator`, `tasks`, `workflows`
- **Debugging**: `debug-tool-call`, `doctor`, `ant-trace`, `heapdump`, `profile-cpu`
- **Configuration**: `color`, `effort`, `fast`, `keybindings`, `model`, `permission-mode`
- **Advanced**: `autofix-pr`, `bughunter`, `chrome`, `desktop`, `ide`

**Hidden/Internal Commands:**
- `/btw` - Casual conversation mode
- `/stickers` - UI customization
- `/good-claude` - Positive reinforcement telemetry

Each command is a self-contained module with:
- Command handler logic
- Permission requirements
- UI components
- Telemetry hooks

### Tool System (40+ Tools)

Located in `src/tools/` - implements the core agent capabilities:

**File Operations:**
- `FileReadTool` - Read files with line range support
- `FileWriteTool` - Create/overwrite files
- `FileEditTool` - Exact string replacement
- `NotebookEditTool` - Jupyter notebook cell editing

**Code Navigation:**
- `GlobTool` - File pattern matching
- `GrepTool` - Content search
- `LSPTool` - Language Server Protocol integration (definitions, references, hover)

**Execution:**
- `BashTool` - Shell command execution with sandboxing
- `PowerShellTool` - Windows PowerShell execution
- `REPLTool` - Interactive interpreter sessions

**Agent Orchestration:**
- `AgentTool` - Spawn sub-agents with isolated context
- `WorkflowTool` - Multi-agent workflow orchestration
- `SendMessageTool` - Inter-agent communication

**Planning & Interaction:**
- `EnterPlanModeTool` / `ExitPlanModeTool` - Plan mode transitions
- `EnterWorktreeTool` / `ExitWorktreeTool` - Git worktree isolation
- `AskUserQuestionTool` - Structured user prompts

**Integration:**
- `MCPTool` / `ListMcpResourcesTool` / `ReadMcpResourceTool` - Model Context Protocol
- `ScheduleCronTool` - Scheduled task execution
- `SkillTool` - Custom skill invocation

**Specialized:**
- `BriefTool` - Summary generation
- `ConfigTool` - Configuration management
- `RemoteTriggerTool` - Remote event triggers

**Tool Architecture:**
```typescript
abstract class Tool {
  name: string
  schema: JSONSchema
  execute(params: any, context: ToolContext): Promise<ToolResult>
  requiresPermission: PermissionLevel
  hooks: ToolHook[]
}
```

### Services Layer (20+ Services)

Located in `src/services/` - platform integrations and cross-cutting concerns:

**Core Services:**
- `analytics/` - Dual telemetry pipeline (Anthropic + Datadog)
- `api/` - API client for claude.ai backend
- `compact/` - Context window compaction
- `lsp/` - Language Server Protocol management
- `mcp/` - Model Context Protocol server management

**Feature Services:**
- `autoDream/` - Autonomous mode background processing
- `extractMemories/` - Memory extraction and persistence
- `MagicDocs/` - Documentation generation
- `PromptSuggestion/` - Prompt completion
- `SessionMemory/` - Cross-session state

**Integration Services:**
- `oauth/` - Authentication flows
- `plugins/` - Plugin system
- `remoteManagedSettings/` - Remote configuration polling
- `settingsSync/` - Settings synchronization

**Monitoring & Limits:**
- `claudeAiLimits.ts` - Rate limit enforcement
- `diagnosticTracking.ts` - Error tracking
- `policyLimits/` - Policy enforcement
- `rateLimitMessages.ts` - Rate limit UI

**Security & Control:**
- `mcpServerApproval.tsx` - MCP server permission dialogs
- `preventSleep.ts` - System sleep prevention during operations
- `notifier.ts` - System notifications

### State Management

| Module | Purpose |
|--------|---------|
| `state/` | Global state container |
| `context.ts` / `context/` | Context window management |
| `history.ts` | Conversation history |
| `projectOnboardingState.ts` | Per-project initialization state |
| `memdir/` | File-based memory persistence |
| `migrations/` | State schema migrations |

### UI Layer (React/Ink)

**Component System** (`src/components/`):
- 33+ React components for terminal UI
- Markdown rendering, syntax highlighting, progress indicators
- Permission dialogs, interactive prompts
- Multi-column layouts, tables, spinners

**Ink Framework** (`src/ink/`):
- Terminal rendering engine
- Component lifecycle management
- Custom hooks for terminal interactions

**Key UI Modules:**
- `interactiveHelpers.tsx` - Dialog utilities
- `dialogLaunchers.tsx` - Modal dialogs
- `outputStyles/` - ANSI color schemes
- `keybindings/` - Keyboard shortcut handlers

### Task & Agent System

**Task Management** (`src/tasks/`):
- Background task execution
- Task lifecycle (pending → in_progress → completed)
- Task dependencies and blocking
- Task output capture

**Agent Coordination:**
- `coordinator/` - Multi-agent orchestration
- `bridge/` - Cross-session agent communication
- `buddy/` - Pair programming mode
- `assistant/` - Kairos assistant mode (feature-gated)

### Platform Integration

**Native Modules** (`vendor/`):
- `audio-capture-src/` - Audio input for voice mode
- `image-processor-src/` - Image processing
- `modifiers-napi-src/` - Keyboard modifier detection
- `url-handler-src/` - Custom URL protocol handler

**TypeScript Support:**
- `native-ts/` - TypeScript bindings for native modules
- `stubs/` - Type stubs for external dependencies
- `types/` - Global type definitions

### Remote & Server

**Remote Execution:**
- `remote/` - Remote agent execution
- `server/` - Local HTTP server for IDE integration
- `upstreamproxy/` - Proxy for upstream API calls

**Plugin System:**
- `plugins/` - Plugin loader and registry
- `schemas/` - JSON schemas for validation

### Advanced Features

**Feature-Gated Modules** (incomplete in npm package):
- `assistant/` - Kairos autonomous assistant (1 file present, 5 missing)
- `coordinator/` - Worker agent system (empty directory)
- `bridge/` - Peer session management (partial implementation)
- `moreright/` - Proactive notifications (stub only)

**Voice & Accessibility:**
- `voice/` - Voice input/output (feature-gated)
- `vim/` - Vim keybindings

**Development Tools:**
- `screens/` - Screen recording for debugging
- `setup.ts` - First-run setup wizard

---

## Standalone Tools (`tools/`)

Advanced tool implementations requiring separate modules:

| Tool | Purpose |
|------|---------|
| `WorkflowTool/` | Multi-agent workflow orchestration with phase management |
| `TungstenTool/` | High-performance text processing |
| `VerifyPlanExecutionTool/` | Plan verification and validation |
| `TerminalCaptureTool/` | Terminal output capture |
| `OverflowTestTool/` | Context overflow testing |

---

## Utility Layer

**Core Utilities** (`utils/`):
- Path manipulation, file system operations
- String processing, formatting
- Async utilities, promise helpers
- Git operations, repository introspection

**Cost Tracking:**
- `cost-tracker.ts` - Token usage tracking
- `costHook.ts` - Cost calculation hooks

---

## Documentation (`docs/`)

Quadrilingual source code analysis (EN/JA/KO/ZH):

1. **Telemetry & Privacy** - Data collection, opt-out limitations
2. **Hidden Features & Codenames** - Feature flags, animal codenames (Capybara, Tengu, Numbat)
3. **Undercover Mode** - AI authorship hiding in public repos
4. **Remote Control** - Killswitches, managed settings, hourly polling
5. **Future Roadmap** - KAIROS autonomous mode, unreleased tools

---

## Key Architectural Patterns

### 1. Progressive Harness Mechanisms

Claude Code layers 12 progressive harness mechanisms on the base agent loop:

1. **Tool System** - Structured function calling with JSON schemas
2. **Permission Gates** - Multi-level approval (auto, ask, bypass)
3. **Context Management** - Automatic compaction, summarization
4. **Cost Tracking** - Token-level budget enforcement
5. **Rate Limiting** - API quota management
6. **State Persistence** - Cross-session memory
7. **Sub-agent Spawning** - Recursive agent instantiation
8. **Workflow Orchestration** - Parallel/pipeline execution
9. **Remote Control** - Managed settings, feature flags
10. **Telemetry** - Comprehensive event tracking
11. **Security Sandbox** - Isolated execution environments
12. **Error Recovery** - Automatic retry, fallback strategies

### 2. Permission Flow

```
Tool Invocation
    ↓
Permission Check (mode: auto|ask|bypass)
    ↓
[If ask] User Dialog → Approve/Deny/Modify
    ↓
Tool Execution Hook (pre-execution)
    ↓
Tool Implementation
    ↓
Tool Execution Hook (post-execution)
    ↓
Telemetry Event
    ↓
State Update
```

### 3. Multi-Agent Coordination

```
Main Agent (Opus/Sonnet)
    ↓
Spawn Sub-Agent (Workflow/Background)
    ├→ Isolated Context Window
    ├→ Separate Tool Allowlist
    ├→ Independent State
    └→ Message Passing (SendMessageTool)
```

### 4. Feature Gating

```typescript
if (feature('KAIROS')) {
  // Autonomous assistant mode
  await import('assistant/index.js')
}

if (feature('EXPERIMENTAL_SKILL_SEARCH')) {
  // Remote skill discovery
  await import('skillSearch/remoteSkillLoader.js')
}
```

### 5. Remote Configuration

```
Hourly Poll: GET /api/claude_code/settings
    ↓
Compare Version Hash
    ↓
[If changed] Show Blocking Dialog
    ↓
User Approve → Apply Changes
User Reject → Exit Application
```

---

## Build & Compilation

**Compilation Pipeline:**
1. TypeScript source files in `src/`
2. Webpack/esbuild bundling
3. Tree-shaking removes feature-gated code
4. Single `cli.js` artifact (~12MB)
5. Native addon compilation (vendor/)
6. npm package publication

**Missing from Source:**
- 108 feature-gated modules (dead-code eliminated)
- Build configuration files (webpack.config.js, etc.)
- Test suites
- Internal Anthropic tooling

---

## Security & Privacy Considerations

### Telemetry Data Collection

**Always Collected (no opt-out):**
- Environment fingerprint (OS, shell, git state)
- Process metrics (memory, CPU)
- Repository hash (unique identifier)
- Tool usage patterns
- Error traces

**Optional Collection:**
- Full tool inputs (`OTEL_LOG_TOOL_DETAILS=1`)
- Screen recordings (feature-gated)
- Voice transcripts (feature-gated)

### Remote Control Surface

**Managed Remotely:**
- Feature flags (GrowthBook)
- Permission mode overrides
- Model selection
- Tool allowlist
- Rate limits

**Killswitches:**
- Bypass permissions
- Fast mode
- Voice mode
- Analytics sink
- Skill search
- Workflow execution

### Undercover Mode

For Anthropic employees in public repositories:
- Strips AI attribution from commits
- Removes "Co-Authored-By: Claude" trailers
- Instructs agent: "Do not blow your cover"
- No force-disable mechanism

---

## Integration Points

### IDE Extensions
- VS Code extension
- JetBrains plugin
- HTTP server at `http://localhost:PORT`

### Web App
- Claude.ai/code
- Browser-based REPL
- Shared session state

### Desktop App
- Electron wrapper
- Native menu integration
- System tray support

### CLI
- Direct terminal invocation
- Shell completion
- Piped input support

---

## Performance Characteristics

**Context Window:**
- Automatic compaction at threshold
- Summarization of old messages
- Selective memory retention

**Execution:**
- Parallel tool invocation where safe
- Background task execution
- Streaming LLM responses

**Storage:**
- SQLite for session history
- File-based memory (`.claude/`)
- Git-ignored runtime state

---

## Development Workflows

### Typical Execution Path

1. User launches CLI → `entrypoints/cli.ts`
2. Main REPL starts → `main.tsx`
3. User enters prompt
4. Query Engine processes → `QueryEngine.ts`
5. LLM generates tool calls
6. Permission checks → Tool execution
7. Results returned to LLM
8. LLM continues or completes
9. State persisted → `history.ts`, `memdir/`
10. Telemetry emitted → `services/analytics/`

### Sub-Agent Workflow

1. Main agent invokes `AgentTool`
2. Permission check (can spawn sub-agents?)
3. New QueryEngine instance created
4. Isolated context window
5. Sub-agent executes independently
6. Results passed back via `SendMessageTool`
7. Main agent continues with results

### Workflow Orchestration

1. User provides workflow script (JavaScript)
2. `WorkflowTool` parses script
3. Phase declarations extracted
4. Agent spawning scheduled (parallel/pipeline)
5. Progress tracked in UI
6. Results aggregated
7. Workflow completes or errors

---

## Comparison to This Repository (Galatea)

**Galatea Architecture:**
- Multi-project ML training platform
- JupyterLab + Ray + MLflow + MinIO integration
- Framework-agnostic (TensorFlow, PyTorch, scikit-learn)
- Platform contracts for reproducibility
- Systemd service management

**Claude Code Architecture:**
- Single-purpose AI development agent
- Extensive tool ecosystem (40+ tools)
- Multi-agent coordination
- Terminal-first UI with web/desktop variants
- Remote configuration and feature gating

**Shared Concepts:**
- Systemd service management (Galatea) ↔ Background tasks (Claude Code)
- MLflow API-only access (Galatea) ↔ Tool permission system (Claude Code)
- Ray job submission (Galatea) ↔ Workflow orchestration (Claude Code)
- Git hygiene requirements (both)
- Idempotent operations (both)

---

## References

- Source: `/data/ai/chenzhangyue/code/claude-code-source-code`
- Version: `@anthropic-ai/claude-code@2.1.88`
- Extraction Date: 2026-08-11
- Documentation Date: 2026-08-12
- Analysis Reports: `docs/{en,ja,ko,zh}/`

---

## Future Directions

**Confirmed Upcoming Features:**
- **Numbat**: Next model codename (successor to Tengu)
- **KAIROS**: Fully autonomous agent with `<tick>` heartbeats
- **Voice Mode**: Push-to-talk ready but gated
- **Proactive Notifications**: PR subscriptions, scheduled checks
- **Remote Skill Search**: Cloud-based skill discovery
- **Enhanced Workflows**: Nested workflows, conditional execution

**Architecture Evolution:**
- Daemon mode for persistent background operation
- Peer-to-peer session sharing (Bridge mode)
- Context collapse service (experimental)
- Enhanced LSP integration (more language servers)

---

*This document abstracts the directory architecture and key components of Claude Code v2.1.88 for reference in the Galatea ML training platform project.*
