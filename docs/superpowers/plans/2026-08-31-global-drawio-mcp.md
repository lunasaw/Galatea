# Global Draw.io MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register the pinned Next AI Draw.io server globally in Codex and prove that it initializes and advertises the expected MCP tools.

**Architecture:** Codex will launch a pinned npm package as a local STDIO MCP child process from the global `~/.codex/config.toml`. Verification will inspect the stored configuration and run an isolated JSON-RPC client against the same command without opening or editing a diagram.

**Tech Stack:** Codex CLI 0.151.0, Node.js 22, npm/npx, MCP JSON-RPC over STDIO

**Spec:** `docs/superpowers/specs/2026-08-31-global-drawio-mcp-design.md`

## Global Constraints

- Register the server globally under the exact name `drawio`.
- Pin the package to `@next-ai-drawio/mcp-server@0.2.3`; do not use the moving `latest` tag.
- Start it with `npx -y` so first use is non-interactive.
- Preserve all existing entries in `~/.codex/config.toml`.
- Do not add credentials, change `PORT`, or change `DRAWIO_BASE_URL`.
- Do not invoke diagram tools during verification; only initialize the server and list its tools.
- Do not modify Galatea runtime code.

---

### Task 1: Register and Inspect the Global MCP Entry

**Files:**
- Modify: `~/.codex/config.toml` through the Codex CLI
- Test: Codex CLI configuration lookup

**Interfaces:**
- Consumes: Codex CLI `mcp add`, `mcp get`, and `mcp list` commands
- Produces: global MCP server `drawio` with command `npx` and arguments `-y`, `@next-ai-drawio/mcp-server@0.2.3`

- [ ] **Step 1: Recheck that the target name is free**

Run:

```bash
codex mcp get drawio --json
```

Expected: nonzero exit status with `Error: No MCP server named 'drawio' found.` Stop and inspect the existing entry instead of overwriting it if this expectation is false.

- [ ] **Step 2: Register the pinned STDIO server**

Run:

```bash
codex mcp add drawio -- npx -y @next-ai-drawio/mcp-server@0.2.3
```

Expected: `Added global MCP server 'drawio'.`

- [ ] **Step 3: Inspect the exact stored entry**

Run:

```bash
codex mcp get drawio --json
```

Expected JSON fields:

```json
{
  "name": "drawio",
  "enabled": true,
  "transport": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@next-ai-drawio/mcp-server@0.2.3"]
  }
}
```

Additional default-valued fields are acceptable. The command, arguments, enabled state, and STDIO transport must match exactly.

- [ ] **Step 4: Confirm the global server list includes the new entry**

Run:

```bash
codex mcp list
```

Expected: one enabled row named `drawio` with command `npx` and the pinned package argument.

There is no repository commit for this task because `~/.codex/config.toml` is host configuration outside the Galatea repository.

---

### Task 2: Prove MCP Initialization and Tool Discovery

**Files:**
- Modify: none
- Test: isolated Node.js MCP JSON-RPC probe

**Interfaces:**
- Consumes: `npx -y @next-ai-drawio/mcp-server@0.2.3`
- Produces: verified MCP initialization response and a validated set of Draw.io tool names

- [ ] **Step 1: Run an isolated MCP handshake and tool-list assertion**

Run from the Galatea repository root:

```bash
node --input-type=module <<'NODE'
import { spawn } from "node:child_process";
import { once } from "node:events";

const expectedTools = [
    "start_session",
    "create_new_diagram",
    "load_diagram",
    "edit_diagram",
    "get_diagram",
    "export_diagram",
    "list_pages",
    "add_page",
    "rename_page",
    "delete_page",
];

const child = spawn(
    "npx",
    ["-y", "@next-ai-drawio/mcp-server@0.2.3"],
    { stdio: ["pipe", "pipe", "pipe"] },
);

let buffer = "";
let stderr = "";

child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
});

const send = (message) => {
    child.stdin.write(`${JSON.stringify(message)}\n`);
};

const response = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
        reject(new Error(`MCP verification timed out. stderr: ${stderr}`));
    }, 45000);

    child.on("error", reject);
    child.on("exit", (code, signal) => {
        reject(new Error(`MCP server exited early (${code ?? signal}). stderr: ${stderr}`));
    });

    child.stdout.on("data", (chunk) => {
        buffer += chunk.toString();
        let newlineIndex;
        while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, newlineIndex).trim();
            buffer = buffer.slice(newlineIndex + 1);
            if (!line) {
                continue;
            }

            const message = JSON.parse(line);
            if (message.id === 1) {
                if (!message.result?.serverInfo?.name) {
                    reject(new Error(`Invalid initialize response: ${line}`));
                    return;
                }
                send({ jsonrpc: "2.0", method: "notifications/initialized" });
                send({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
            }

            if (message.id === 2) {
                const names = (message.result?.tools ?? []).map((tool) => tool.name);
                const missing = expectedTools.filter((name) => !names.includes(name));
                if (missing.length > 0) {
                    reject(new Error(`Missing tools: ${missing.join(", ")}`));
                    return;
                }
                clearTimeout(timer);
                resolve({ serverInfo: message.result, names });
            }
        }
    });
});

send({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "codex-drawio-verifier", version: "1.0.0" },
    },
});

try {
    const result = await response;
    console.log(`Verified ${result.names.length} tools: ${result.names.join(", ")}`);
} finally {
    child.kill("SIGTERM");
    await Promise.race([
        once(child, "exit"),
        new Promise((resolve) => setTimeout(resolve, 5000)),
    ]);
    if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
        await once(child, "exit");
    }
}
NODE
```

Expected: exit status 0 and output beginning with `Verified 10 tools:` followed by all expected tool names.

- [ ] **Step 2: Confirm the verification process left no preview listener**

Run:

```bash
lsof -nP -iTCP:6002-6020 -sTCP:LISTEN
```

Expected: no listener belonging to `next-ai-drawio-mcp`. An unrelated existing listener must be identified and left untouched.

- [ ] **Step 3: Record the refresh boundary and rollback command in the handoff**

Report both facts:

```text
The current Codex task cannot acquire a newly configured MCP tool inventory dynamically; restart the Codex client or start a new task before using drawio.
Rollback command: codex mcp remove drawio
```

There is no repository commit for this task because the probe is ephemeral and does not create files.
