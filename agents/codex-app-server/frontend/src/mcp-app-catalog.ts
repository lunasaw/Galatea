import type { Obj } from "./native";

export type McpApp = { id: string; server: string; toolName: string; tool: Obj; uri: string };

/** Only public entry tools belong in the launcher; App-only tools use its bridge. */
export function mcpApps(servers: Array<{ name: string; tools?: Record<string, Obj> }>): McpApp[] {
  return servers.flatMap((server) => Object.entries(server.tools || {}).flatMap(([name, tool]) => {
    const ui = tool._meta?.ui;
    if (Array.isArray(ui?.visibility) && !ui.visibility.includes("model")) return [];
    const uri = ui?.resourceUri ?? tool._meta?.["ui/resourceUri"];
    return typeof uri === "string" && uri.startsWith("ui://")
      ? [{ id: `${server.name}:${name}:${uri}`, server: server.name, toolName: name, tool, uri }] : [];
  }));
}
