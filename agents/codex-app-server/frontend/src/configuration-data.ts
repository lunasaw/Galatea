export type ConfigObject = Record<string, any>;
export type ConfigLayer = { name: ConfigObject; version: string; config: ConfigObject; disabledReason?: string };
// The Host may redact a source entry as well as its configuration value.
export type ConfigSnapshot = { config: ConfigObject; origins: Record<string, unknown>; layers?: ConfigLayer[] };
export type ConfigEdit = { keyPath: string; value: unknown; mergeStrategy: "replace" };
export function supportsLegacyUntrusted(version = "") {
  const match = version.match(/(?:codex-cli\s+)?0\.(\d+)\./);
  return !!match && Number(match[1]) < 163;
}

export function configPath(layer: Pick<ConfigLayer, "name">) {
  return layer.name.file || (layer.name.dotCodexFolder ? `${layer.name.dotCodexFolder.replace(/[\\/]$/, "")}/config.toml` : "");
}
export function layerLabel(layer: unknown) {
  if (isRedacted(layer)) return "来源信息已隐藏";
  const name = layer && typeof layer === "object" && "name" in layer ? layer.name : undefined;
  if (!name || typeof name !== "object" || !("type" in name) || typeof name.type !== "string") return "未知配置来源";
  const names: Record<string, string> = { user: "用户配置", project: "项目配置", system: "系统配置", sessionFlags: "启动参数", legacyManagedConfigTomlFromFile: "托管配置", mdm: "组织配置" };
  const profile = "profile" in name && typeof name.profile === "string" ? name.profile : "";
  return `${names[name.type] || name.type}${profile ? ` · ${profile}` : ""}`;
}
export function configValue(config: ConfigObject, key: string): any {
  return key.split(".").reduce((value, part) => value && typeof value === "object" ? value[part] : undefined, config);
}
export function isRedacted(value: unknown): boolean {
  if (typeof value === "string") return value.includes("[redacted:");
  return !!value && typeof value === "object" && Object.values(value).some(isRedacted);
}
export function configEdits(draft: ConfigObject, original: ConfigObject): ConfigEdit[] {
  return Object.entries(draft).filter(([key, value]) => JSON.stringify(value ?? null) !== JSON.stringify(configValue(original, key) ?? null)).map(([keyPath, value]) => {
    if (isRedacted(value)) throw new Error("不能把隐藏内容的占位符保存为配置，请重新输入该字段。");
    return { keyPath, value: value ?? null, mergeStrategy: "replace" };
  });
}
export function configWriteParams(layer: ConfigLayer, edits: ConfigEdit[], reloadUserConfig: boolean) {
  if (layer.name.type !== "user" || layer.disabledReason || !configPath(layer) || !layer.version)
    throw new Error("当前配置层不允许通过原生接口修改。");
  return { edits, filePath: configPath(layer), expectedVersion: layer.version, reloadUserConfig };
}
export function positiveInteger(value: string) {
  if (!value.trim()) return null;
  const number = Number(value);
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(number) || number <= 0) throw new Error("请输入正整数，或留空恢复默认。");
  return number;
}

// Native config paths use TOML keys, so dots and slashes in named entries must
// stay in a single quoted segment. Diff leaves to preserve untouched secrets.
export function configKeyPath(parts: string[]) {
  return parts.map((part) => {
    if (!part) throw new Error("原生配置路径不支持空字段名。");
    // The native key-path parser escapes punctuation only, not JSON escape
    // sequences. Preserve controls literally until the HTTP JSON encoder.
    return /^[A-Za-z0-9_-]+$/.test(part) ? part : `"${part.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
  }).join(".");
}
export function structuredConfigEdits(original: ConfigObject, updated: ConfigObject): ConfigEdit[] {
  const edits: ConfigEdit[] = [];
  const object = (value: any) => value !== null && typeof value === "object" && !Array.isArray(value);
  const visit = (before: any, after: any, path: string[]) => {
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    if (object(after) && (object(before) || before === undefined) && Object.keys(after).length) {
      for (const key of new Set([...Object.keys(before || {}), ...Object.keys(after)])) visit(before?.[key], after[key], [...path, key]);
      return;
    }
    if (isRedacted(after)) throw new Error("已修改的字段含隐藏内容，请重新输入该字段。未修改的隐藏字段会保留。");
    edits.push({ keyPath: configKeyPath(path), value: after ?? null, mergeStrategy: "replace" });
  };
  for (const key of new Set([...Object.keys(original), ...Object.keys(updated)])) visit(original[key], updated[key], [key]);
  return edits;
}
export function visibleEfforts(supported: string[], hidden: string[], current?: string) {
  const visible = supported.filter((effort) => !hidden.includes(effort) || effort === current);
  return visible.length ? visible : supported;
}
