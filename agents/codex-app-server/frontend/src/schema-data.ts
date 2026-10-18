import type { Obj } from "./native";

export function resolveSchema(input: Obj | boolean, root: Obj, seen = new Set<string>()): Obj {
  if (typeof input === "boolean") return input ? {} : { not: {} };
  let schema = { ...input };
  if (schema.$ref?.startsWith("#/") && !seen.has(schema.$ref)) {
    seen.add(schema.$ref);
    const target = schema.$ref.slice(2).split("/").reduce((o: any, k: string) => o?.[k.replaceAll("~1", "/").replaceAll("~0", "~")], root);
    delete schema.$ref;
    schema = { ...resolveSchema(target || {}, root, seen), ...schema };
  }
  if (schema.allOf) {
    const branches = schema.allOf; delete schema.allOf;
    for (const branch of branches) {
      const resolved = resolveSchema(branch, root, new Set(seen));
      schema = { ...resolved, ...schema, ...(resolved.properties || schema.properties ? { properties: { ...resolved.properties, ...schema.properties } } : {}),
        required: [...new Set([...(resolved.required || []), ...(schema.required || [])])] };
    }
  }
  return schema;
}

export function variantsOf(schema: Obj): Obj[] | undefined {
  return schema.oneOf || schema.anyOf || (Array.isArray(schema.type) ? schema.type.map((type: string) => ({ ...schema, type })) : undefined);
}
export function matchesSchema(input: Obj, value: any, root: Obj): boolean {
  const s = resolveSchema(input, root);
  const branches = variantsOf(s);
  if (branches) return branches.some((b) => matchesSchema(b, value, root));
  if (s.const !== undefined && s.const !== value || s.enum && !s.enum.includes(value)) return false;
  if (s.type === "null") return value === null;
  if (s.type === "object" || s.properties) return !!value && typeof value === "object" && !Array.isArray(value)
    && (s.required || []).every((k: string) => Object.hasOwn(value, k))
    && Object.entries(s.properties || {}).every(([k, child]) => !Object.hasOwn(value, k) || matchesSchema(child as Obj, value[k], root));
  if (s.type === "array") return Array.isArray(value);
  if (s.type === "integer") return Number.isSafeInteger(value);
  return !s.type || typeof value === s.type;
}
export function defaultForSchema(input: Obj | boolean, root: Obj, depth = 0): any {
  if (depth > 24) return null;
  const s = resolveSchema(input, root);
  if (s.const !== undefined) return s.const;
  if (s.default !== undefined && s.default !== null) return structuredClone(s.default);
  if (s.enum) return s.enum[0];
  const branches = variantsOf(s);
  if (branches) return defaultForSchema(branches.find((b) => b.type !== "null") || branches[0], root, depth + 1);
  if (s.type === "object" || s.properties) return Object.fromEntries((s.required || []).map((k: string) => [k, defaultForSchema(s.properties?.[k] || {}, root, depth + 1)]));
  if (s.type === "array") return [];
  if (s.type === "boolean") return false;
  if (s.type === "null") return null;
  if (["integer", "number"].includes(s.type)) return s.minimum ?? 0;
  return "";
}
