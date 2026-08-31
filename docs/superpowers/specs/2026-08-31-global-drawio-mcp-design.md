# Global Draw.io MCP Integration Design

## Goal

Make the Next AI Draw.io MCP server available to every local Codex project on this Mac. The integration
must be reproducible, must preserve the existing Codex configuration, and must be verified with an actual
MCP initialization and tool-list exchange.

## Scope

- Register one global Codex MCP server named `drawio` in `~/.codex/config.toml`.
- Start the server as a local STDIO child process through `npx`.
- Pin the package to the currently reviewed release, `@next-ai-drawio/mcp-server@0.2.3`.
- Allow `npx` to install the pinned package non-interactively with `-y`.
- Keep the server's default preview behavior: begin with local port 6002 and load the draw.io embed from
  `https://embed.diagrams.net`.

This change does not modify Galatea runtime code, deploy a self-hosted draw.io instance, or expose a remote
MCP endpoint.

## Configuration

Use the Codex CLI rather than editing TOML by hand:

```bash
codex mcp add drawio -- npx -y @next-ai-drawio/mcp-server@0.2.3
```

The expected global configuration is equivalent to:

```toml
[mcp_servers.drawio]
command = "npx"
args = ["-y", "@next-ai-drawio/mcp-server@0.2.3"]
```

Pinning the version prevents an upstream `latest` release from changing behavior without review. The
server can be upgraded later by replacing the registration with a newly reviewed version.

## Runtime Flow

1. A local Codex client starts the `drawio` command over STDIO when loading MCP servers.
2. `npx` resolves the pinned package from its local cache or the npm registry.
3. The MCP server exposes diagram session, create, edit, load, inspect, page-management, and export tools.
4. `start_session` starts the embedded local HTTP preview server and opens the browser.
5. The preview communicates with the local MCP process; the draw.io UI is loaded from the configured
   draw.io base URL.

## Security and Operational Boundaries

- The npm package executes locally with the same filesystem permissions as the Codex MCP process.
- `load_diagram` and `export_diagram` can read or write user-selected paths, so they should be used only
  with explicit task context.
- The default browser UI depends on `embed.diagrams.net`; diagrams requiring fully private rendering need
  a separately approved self-hosted `DRAWIO_BASE_URL`.
- The embedded server tries ports 6002 through 6020 if the default port is occupied.
- No credentials or secrets are added to the MCP configuration.

## Verification

After registration:

1. Run `codex mcp list` and confirm `drawio` is enabled with the pinned command and arguments.
2. Start the configured command in an isolated verification process.
3. Send MCP `initialize`, `notifications/initialized`, and `tools/list` messages over STDIO.
4. Confirm the expected Draw.io tools are returned, including `start_session`, `create_new_diagram`,
   `edit_diagram`, `get_diagram`, and `export_diagram`.
5. Terminate the verification process and confirm it did not leave a preview listener running.

The already-running Codex task will not dynamically gain the new MCP tool inventory. Restart the local
Codex client or begin a new task after verification.

## Failure Handling and Rollback

- If registration fails, leave the existing global configuration unchanged and report the CLI error.
- If the package cannot initialize, inspect the process output without changing the version or network
  settings automatically.
- Remove the integration with `codex mcp remove drawio` if rollback is requested.

## References

- [Codex Model Context Protocol documentation](https://developers.openai.com/codex/mcp/)
- [Next AI Draw.io MCP server](https://github.com/DayuanJiang/next-ai-draw-io/tree/main/packages/mcp-server)
