import { useState } from "react";
import { Button } from "./components/ui/button";
import { Field } from "./capability-controls";
import type { Obj } from "./native";

export function schemaDefault(schema: Obj): any {
  if (schema.const !== undefined) return schema.const;
  if (schema.default !== undefined) return schema.default;
  if (schema.enum) return schema.enum[0];
  if (schema.type === "object" || schema.properties) return Object.fromEntries((schema.required || []).map((key: string) => [key, schemaDefault(schema.properties?.[key] || {})]));
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "null") return null;
  if (["integer", "number"].includes(schema.type)) return schema.minimum ?? 0;
  return "";
}
export function SchemaInput({ schema, value, change, label = "参数", root = schema, depth = 0 }: {
  schema: Obj; value: any; change: (v: any) => void; label?: string; root?: Obj; depth?: number;
}) {
  const [branch, setBranch] = useState(0); const [key, setKey] = useState(""); const [json, setJson] = useState(""); const [error, setError] = useState("");
  if (depth > 12) return <p role="alert">参数嵌套过深，请使用工具提供方的客户端。</p>;
  if (schema.$ref?.startsWith("#/")) {
    const resolved = schema.$ref.slice(2).split("/").reduce((o: Obj | undefined, k: string) => o?.[k.replaceAll("~1", "/").replaceAll("~0", "~")], root);
    if (resolved) return <SchemaInput {...{ value, change, label, root }} schema={resolved} depth={depth + 1} />;
  }
  const variants = schema.oneOf || schema.anyOf || (Array.isArray(schema.type) ? schema.type.map((type: string) => ({ ...schema, type })) : null);
  if (variants) return <fieldset className="schema-fields"><legend>{label}</legend><select aria-label={`${label}类型`} value={branch} onChange={(e) => { const i = Number(e.target.value); setBranch(i); change(schemaDefault(variants[i])); }}>{variants.map((s: Obj, i: number) => <option value={i} key={i}>{s.title || s.type || `选项 ${i + 1}`}</option>)}</select><SchemaInput {...{ value, change, root }} label={label} schema={variants[branch]} depth={depth + 1} /></fieldset>;
  if (schema.const !== undefined) return <p>{label}：{String(schema.const)}</p>;
  if (schema.enum) return <Field label={label}><select value={JSON.stringify(value)} onChange={(e) => change(JSON.parse(e.target.value))}><option value="" disabled>选择</option>{schema.enum.map((v: any, i: number) => <option key={i} value={JSON.stringify(v)}>{String(v)}</option>)}</select></Field>;
  if (schema.type === "object" || schema.properties) {
    const fields = schema.properties || {}; const object = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    return <fieldset className="schema-fields"><legend>{label}</legend>{Object.entries(fields).map(([name, child]) => {
      const required = schema.required?.includes(name); const included = Object.hasOwn(object, name);
      return <div key={name}>{!required && <label className="native-checkbox"><input type="checkbox" checked={included} onChange={(e) => { const next = { ...object }; if (e.target.checked) next[name] = schemaDefault(child as Obj); else delete next[name]; change(next); }} />包含 {name}</label>}
        {(required || included) && <SchemaInput schema={child as Obj} value={object[name]} change={(v) => change({ ...object, [name]: v })} label={(child as Obj).title || name} root={root} depth={depth + 1} />}</div>;
    })}{Object.keys(object).filter((name) => !Object.hasOwn(fields, name)).map((name) => <div key={name}><SchemaInput schema={typeof schema.additionalProperties === "object" ? schema.additionalProperties : {}} value={object[name]} change={(v) => change({ ...object, [name]: v })} label={name} root={root} depth={depth + 1} /><Button type="button" variant="ghost" onClick={() => { const next = { ...object }; delete next[name]; change(next); }}>移除 {name}</Button></div>)}
      {schema.additionalProperties !== false && <div className="native-options"><input aria-label={`${label}新增字段名`} value={key} onChange={(e) => setKey(e.target.value)} /><Button type="button" variant="outline" disabled={!key || ["__proto__", "prototype", "constructor"].includes(key)} onClick={() => { change({ ...object, [key]: schemaDefault(schema.additionalProperties || {}) }); setKey(""); }}>添加字段</Button></div>}
    </fieldset>;
  }
  if (schema.type === "array") return <fieldset className="schema-fields"><legend>{label}</legend>{(Array.isArray(value) ? value : []).map((v, i, rows) => <div key={i}><SchemaInput schema={schema.items || {}} value={v} change={(n) => change(rows.map((x, j) => j === i ? n : x))} label={`${label} ${i + 1}`} root={root} depth={depth + 1} /><Button type="button" variant="ghost" onClick={() => change(rows.filter((_, j) => j !== i))}>移除此项</Button></div>)}<Button type="button" variant="outline" onClick={() => change([...(Array.isArray(value) ? value : []), schemaDefault(schema.items || {})])}>添加一项</Button></fieldset>;
  if (schema.type === "boolean") return <label className="native-checkbox"><input type="checkbox" checked={!!value} onChange={(e) => change(e.target.checked)} />{label}</label>;
  if (schema.type === "null") return <p>{label}：空值</p>;
  if (schema.type === "string" || ["integer", "number"].includes(schema.type)) return <Field label={label}><input aria-label={label} type={schema.type === "string" ? "text" : "number"} value={value ?? ""} min={schema.minimum} max={schema.maximum} minLength={schema.minLength} maxLength={schema.maxLength} pattern={schema.pattern} step={schema.type === "integer" ? 1 : "any"} onChange={(e) => change(schema.type === "string" ? e.target.value : e.target.value === "" ? undefined : Number(e.target.value))} />{schema.description && <small>{schema.description}</small>}</Field>;
  return <Field label={`${label}（工具未提供字段定义）`}><textarea value={json || JSON.stringify(value ?? null, null, 2)} onChange={(e) => { setJson(e.target.value); try { change(JSON.parse(e.target.value)); setError(""); e.target.setCustomValidity(""); } catch { setError("请输入有效 JSON 值"); e.target.setCustomValidity("请输入有效 JSON 值"); } }} />{error && <small role="alert">{error}</small>}</Field>;
}
