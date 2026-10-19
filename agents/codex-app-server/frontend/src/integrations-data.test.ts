import assert from "node:assert/strict";
import { test } from "node:test";
import { IntegrationsClient, canInstall, httpUrl, pluginTarget, type IntegrationRpc, type Plugin } from "./integrations-data";

function fixture(reply: (method: string, params: any) => unknown) {
  const calls: Array<{ method: string; params: any }> = [];
  const rpc: IntegrationRpc = async <T>(method: string, params: any) => {
    calls.push({ method, params });
    return reply(method, params) as T;
  };
  return { client: new IntegrationsClient(rpc), calls };
}
const plugin: Plugin = { id: "remote/tool", name: "tool", installed: false, enabled: false, installPolicy: "AVAILABLE", authPolicy: "ON_USE" };

test("MCP inventory reads every page in the selected thread and keeps unknown runtime state", async () => {
  const { client, calls } = fixture((_, params) => params.cursor
    ? { data: [{ name: "alpha", runtimeStatus: null }, { name: "zeta", runtimeStatus: "connected" }], nextCursor: null }
    : { data: [{ name: "zeta", runtimeStatus: "starting" }], nextCursor: "next" });
  const rows = await client.mcps("thread-1");
  assert.deepEqual(rows.map((row) => [row.name, row.runtimeStatus]), [["alpha", null], ["zeta", "connected"]]);
  assert.deepEqual(calls.map((call) => call.params), [
    { detail: "full", limit: 100, threadId: "thread-1" },
    { detail: "full", limit: 100, threadId: "thread-1", cursor: "next" },
  ]);
});

test("MCP inventory rejects repeated cursors instead of looping", async () => {
  const { client, calls } = fixture(() => ({ data: [], nextCursor: "repeat" }));
  await assert.rejects(client.mcps(), /重复分页游标/);
  assert.equal(calls.length, 2);
});

test("MCP reload preserves protocol null; OAuth includes thread context", async () => {
  const { client, calls } = fixture(() => ({}));
  await client.reloadMcps();
  await client.loginMcp("docs", "thread-2");
  assert.deepEqual(calls, [
    { method: "config/mcpServer/reload", params: null },
    { method: "mcpServer/oauth/login", params: { name: "docs", threadId: "thread-2" } },
  ]);
});

test("local and remote plugins use their native marketplace selectors", async () => {
  const remote = { name: "remote", path: null, plugins: [plugin] };
  const local = { name: "local", path: "/workspace/marketplace", plugins: [plugin] };
  assert.deepEqual(pluginTarget(local, plugin), { pluginName: "tool", marketplacePath: local.path });
  const { client, calls } = fixture(() => ({}));
  await client.detail(remote, plugin);
  await client.install(remote, plugin);
  await client.uninstall(plugin);
  await client.reconcile();
  assert.deepEqual(calls, [
    { method: "plugin/read", params: { pluginName: "tool", remoteMarketplaceName: "remote" } },
    { method: "plugin/install", params: { pluginName: "tool", remoteMarketplaceName: "remote" } },
    { method: "plugin/uninstall", params: { pluginId: "remote/tool" } },
    { method: "plugin/reconcile", params: { reason: "user" } },
  ]);
});

test("skills and plugins are discovered for the current workspace and explicit refresh bypasses caches", async () => {
  const { client, calls } = fixture(() => ({}));
  await client.skills("/workspace/a", true);
  await client.setSkill({ name: "same-name", path: "/workspace/a/SKILL.md", description: "", enabled: true }, false);
  await client.plugins("/workspace/b", true);
  assert.deepEqual(calls, [
    { method: "skills/list", params: { cwds: ["/workspace/a"], forceReload: true } },
    { method: "skills/config/write", params: { path: "/workspace/a/SKILL.md", enabled: false } },
    { method: "plugin/list", params: { cwds: ["/workspace/b"], forceRefetch: true } },
  ]);
});

test("plugin policy and catalog links are checked before rendering actions", () => {
  assert.equal(canInstall(plugin), true);
  for (const installPolicy of ["NOT_AVAILABLE", "INSTALLED_BY_DEFAULT", "unknown"])
    assert.equal(canInstall({ ...plugin, installPolicy }), false);
  assert.equal(canInstall({ ...plugin, installed: true }), false);
  assert.equal(canInstall({ ...plugin, availability: "DISABLED_BY_ADMIN" }), false);
  assert.equal(httpUrl("https://example.com/auth?state=keep"), "https://example.com/auth?state=keep");
  for (const url of ["javascript:alert(1)", "data:text/html,unsafe", "//example.com", "https://user:secret@example.com", null])
    assert.equal(httpUrl(url), undefined);
});
