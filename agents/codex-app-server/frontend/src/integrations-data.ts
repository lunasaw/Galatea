// Shapes verified against the pinned codex-cli 0.154.0 schema.
export type Skill = {
  name: string;
  path: string;
  description: string;
  enabled: boolean;
  scope?: string;
  pluginId?: string | null;
  interface?: { displayName?: string | null; shortDescription?: string | null } | null;
};
export type SkillsResponse = {
  data: Array<{ cwd: string; skills: Skill[]; errors?: Array<{ path: string; message: string }> }>;
};
export type McpServer = {
  name: string;
  authStatus: string;
  runtimeStatus?: string | null;
  toolsError?: string | null;
  pluginId?: string | null;
  tools: Record<string, { name?: string; description?: string; inputSchema?: unknown }>;
  resources?: Array<{ name: string; uri: string }>;
  resourceTemplates?: Array<{ name: string; uriTemplate: string }>;
};
export type PluginInterface = {
  displayName?: string | null;
  shortDescription?: string | null;
  longDescription?: string | null;
  developerName?: string | null;
  category?: string | null;
  capabilities?: string[];
  websiteUrl?: string | null;
  privacyPolicyUrl?: string | null;
  termsOfServiceUrl?: string | null;
};
export type Plugin = {
  id: string;
  name: string;
  installed: boolean;
  enabled: boolean;
  installPolicy: string;
  authPolicy: string;
  availability?: string;
  disabledReason?: string | null;
  interface?: PluginInterface | null;
};
export type Marketplace = {
  name: string;
  path?: string | null;
  interface?: { displayName?: string | null } | null;
  plugins: Plugin[];
};
export type PluginsResponse = {
  marketplaces: Marketplace[];
  marketplaceLoadErrors?: Array<{ marketplacePath: string; message: string }>;
  remoteSyncError?: string | null;
  featuredPluginIds?: string[];
};
export type PluginApp = { id: string; name: string; description?: string | null; installUrl?: string | null };
export type PluginDetail = {
  marketplaceName: string;
  summary: Plugin;
  description?: string | null;
  skills: Skill[];
  apps: PluginApp[];
  mcpServers: string[];
};
export type ReconcileResult = {
  changedPlugins: Array<{ id: string; hasSkills: boolean; hasMcps: boolean; hasApps: boolean; hasHooks: boolean }>;
  failedRemotePluginIds: string[];
  failedMaterializationRemotePluginIds: string[];
};
export type IntegrationRpc = <T>(method: string, params: Record<string, unknown> | null) => Promise<T>;

export function pluginTarget(marketplace: Marketplace, plugin: Plugin) {
  return {
    pluginName: plugin.name,
    ...(marketplace.path
      ? { marketplacePath: marketplace.path }
      : { remoteMarketplaceName: marketplace.name }),
  };
}

export function canInstall(plugin: Plugin) {
  return !plugin.installed && plugin.installPolicy === "AVAILABLE" &&
    (!plugin.availability || plugin.availability === "AVAILABLE");
}

export function httpUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  try {
    const url = new URL(value);
    if (["https:", "http:"].includes(url.protocol) && !url.username && !url.password)
      return url.href;
  } catch { /* Untrusted catalog and OAuth URLs are display-only when invalid. */ }
}

export class IntegrationsClient {
  constructor(private rpc: IntegrationRpc) {}
  skills(cwd: string, forceReload = false) {
    return this.rpc<SkillsResponse>("skills/list", { cwds: cwd ? [cwd] : [], forceReload });
  }
  setSkill(skill: Skill, enabled: boolean) {
    return this.rpc<{ effectiveEnabled: boolean }>("skills/config/write", { path: skill.path, enabled });
  }
  async mcps(threadId?: string): Promise<McpServer[]> {
    const rows = new Map<string, McpServer>();
    const cursors = new Set<string>();
    let cursor: string | undefined;
    do {
      const page = await this.rpc<{ data: McpServer[]; nextCursor?: string | null }>("mcpServerStatus/list", {
        detail: "full", limit: 100, ...(threadId ? { threadId } : {}), ...(cursor ? { cursor } : {}),
      });
      for (const server of page.data) rows.set(server.name, server);
      cursor = page.nextCursor || undefined;
      if (cursor && cursors.has(cursor)) throw new Error("MCP 列表返回重复分页游标，请刷新重试。");
      if (cursor) cursors.add(cursor);
    } while (cursor);
    return [...rows.values()].sort((a, b) => a.name.localeCompare(b.name));
  }
  reloadMcps() {
    return this.rpc("config/mcpServer/reload", null);
  }
  loginMcp(name: string, threadId?: string) {
    return this.rpc<{ authorizationUrl: string }>("mcpServer/oauth/login", {
      name, ...(threadId ? { threadId } : {}),
    });
  }
  plugins(cwd: string, forceRefetch = false) {
    return this.rpc<PluginsResponse>("plugin/list", { cwds: cwd ? [cwd] : [], forceRefetch });
  }
  detail(marketplace: Marketplace, plugin: Plugin) {
    return this.rpc<{ plugin: PluginDetail }>("plugin/read", pluginTarget(marketplace, plugin));
  }
  install(marketplace: Marketplace, plugin: Plugin) {
    return this.rpc<{ authPolicy: string; appsNeedingAuth: PluginApp[] }>("plugin/install", pluginTarget(marketplace, plugin));
  }
  uninstall(plugin: Plugin) {
    return this.rpc("plugin/uninstall", { pluginId: plugin.id });
  }
  reconcile() {
    return this.rpc<ReconcileResult>("plugin/reconcile", { reason: "user" });
  }
}
