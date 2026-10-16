import { api, consoleStore as store, requestKey } from "./store";

export type Obj = Record<string, any>;
export const json = (value: unknown) => JSON.stringify(value, null, 2);
export async function native<T = Obj>(
  method: string,
  params: Obj = {},
  key = requestKey(),
): Promise<T> {
  return api<T>("/api/native/call", { method, params }, key);
}
export const sandboxPolicy = (value: string) =>
  value === "workspace-write"
    ? {
        type: "workspaceWrite",
        writableRoots: [],
        networkAccess: false,
        excludeTmpdirEnvVar: false,
        excludeSlashTmp: false,
      }
    : { type: value === "read-only" ? "readOnly" : "dangerFullAccess" };
export async function openNativeThread(threadId: string) {
  const result = await native("thread/resume", { threadId });
  await store.refreshSessions();
  if (result.session) await store.select(result.session.id);
}
export function schemaValue(schema: Obj, definitions: Obj, depth = 0): unknown {
  if (depth > 8) return null;
  if (schema.$ref)
    return schemaValue(
      definitions[schema.$ref.split("/").at(-1)] || {},
      definitions,
      depth + 1,
    );
  if (schema.default !== undefined) return schema.default;
  if (schema.const !== undefined) return schema.const;
  if (schema.enum) return schema.enum[0];
  const branches = schema.oneOf || schema.anyOf || schema.allOf;
  if (branches)
    return schemaValue(
      branches.find((s: Obj) => s.type !== "null") || branches[0],
      definitions,
      depth + 1,
    );
  if (schema.type === "object" || schema.properties)
    return Object.fromEntries(
      (schema.required || []).map((key: string) => [
        key,
        schemaValue(schema.properties[key] || {}, definitions, depth + 1),
      ]),
    );
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (["integer", "number"].includes(schema.type)) return schema.minimum || 0;
  return "";
}
export const encode64 = (value: string) =>
  btoa(
    Array.from(new TextEncoder().encode(value), (b) =>
      String.fromCharCode(b),
    ).join(""),
  );
export const decode64 = (value: string) =>
  new TextDecoder().decode(
    Uint8Array.from(atob(value), (c) => c.charCodeAt(0)),
  );
