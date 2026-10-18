import { useState } from "react";
import { Button } from "./components/ui/button";
import { Field } from "./capability-controls";
import type { Obj } from "./native";
import { defaultForSchema, matchesSchema, resolveSchema, variantsOf } from "./schema-data";
import { isRedacted } from "./configuration-data";
import { schemaLabel, schemaChoiceLabel } from "./schema-labels";

export const schemaDefault = (schema: Obj, root = schema) => defaultForSchema(schema, root);
export function SchemaInput({ schema, value, change, label = "参数", root = schema, depth = 0 }: {
  schema: Obj; value: any; change: (v: any) => void; label?: string; root?: Obj; depth?: number;
}) {
  const [key, setKey] = useState(""); const [json, setJson] = useState<string>(); const [error, setError] = useState("");
  if (depth > 32) return <p role="alert">嵌套超过 32 层。</p>;
  schema = resolveSchema(schema, root);
  const variants = variantsOf(schema);
  if (variants) {
    const branch = Math.max(0, variants.findIndex((s) => matchesSchema(s, value, root)));
    return <fieldset className="schema-fields schema-variant"><legend>{schemaLabel(label)}</legend><Field label={`${label}类型`} displayLabel={label === "登录账户" ? "登录方式" : `${schemaLabel(label)}类型`}><select aria-label={`${label}类型`} value={branch} onChange={(e) => change(schemaDefault(variants[Number(e.target.value)], root))}>{variants.map((s: Obj, i: number) => {
      const resolved = resolveSchema(s, root); const discriminator = resolved.properties?.type;
      return <option value={i} key={i}>{schemaChoiceLabel(discriminator?.enum?.[0] || resolved.title || resolved.type || `选项 ${i + 1}`)}</option>;
    })}</select></Field><SchemaInput key={branch} {...{ value, change, root }} label={label} schema={variants[branch]} depth={depth + 1} /></fieldset>;
  }
  if (schema.const !== undefined) return <p>{label}：{String(schema.const)}</p>;
  if (schema.enum) return <Field label={label} displayLabel={schemaLabel(label)}><select aria-label={label} value={JSON.stringify(value)} onChange={(e) => change(JSON.parse(e.target.value))}><option value="" disabled>选择</option>{schema.enum.map((v: any, i: number) => <option key={i} value={JSON.stringify(v)}>{String(v)}</option>)}</select></Field>;
  if (schema.type === "object" || schema.properties) {
    const fields = schema.properties || {}; const object = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    return <fieldset className="schema-fields"><legend>{label}</legend>{Object.entries(fields).map(([name, child]) => {
      const required = schema.required?.includes(name); const included = Object.hasOwn(object, name);
      if (name === "type" && required && resolveSchema(child as Obj, root).enum?.length === 1 && included) return null;
      return <div className="schema-property" data-included={required || included} key={name}>{!required && <label className="native-checkbox schema-optional"><input type="checkbox" role="switch" aria-label={`包含 ${name}`} checked={included} onChange={(e) => { const next = { ...object }; if (e.target.checked) next[name] = schemaDefault(child as Obj, root); else delete next[name]; change(next); }} /><span><span>{schemaLabel(name)}</span><small>{included ? "自定义" : "使用默认值"}{schemaLabel(name) !== name ? ` · ${name}` : ""}</small></span></label>}
        {(required || included) && <SchemaInput schema={child as Obj} value={object[name]} change={(v) => change({ ...object, [name]: v })} label={name} root={root} depth={depth + 1} />}</div>;
    })}{Object.keys(object).filter((name) => !Object.hasOwn(fields, name)).map((name) => <div className="schema-entry" key={name}><SchemaInput schema={typeof schema.additionalProperties === "object" ? schema.additionalProperties : {}} value={object[name]} change={(v) => change({ ...object, [name]: v })} label={name} root={root} depth={depth + 1} /><Button type="button" variant="ghost" onClick={() => { const next = { ...object }; delete next[name]; change(next); }}>移除 {name}</Button></div>)}
      {schema.additionalProperties !== false && <div className="native-options"><input aria-label={`${label}新增字段名`} value={key} onChange={(e) => setKey(e.target.value)} /><Button type="button" variant="outline" disabled={!key || Object.hasOwn(object, key) || ["__proto__", "prototype", "constructor"].includes(key)} onClick={() => { change({ ...object, [key]: schemaDefault(schema.additionalProperties || {}, root) }); setKey(""); }}>添加字段</Button></div>}
    </fieldset>;
  }
  if (schema.type === "array") return <fieldset className="schema-fields"><legend>{label}</legend>{(Array.isArray(value) ? value : []).map((v, i, rows) => <div className="schema-entry" key={i}><SchemaInput schema={schema.items || {}} value={v} change={(n) => change(rows.map((x, j) => j === i ? n : x))} label={`${label} ${i + 1}`} root={root} depth={depth + 1} /><Button type="button" variant="ghost" onClick={() => change(rows.filter((_, j) => j !== i))}>移除此项</Button></div>)}<Button type="button" variant="outline" onClick={() => change([...(Array.isArray(value) ? value : []), schemaDefault(schema.items || {}, root)])}>添加一项</Button></fieldset>;
  if (schema.type === "boolean") return <label className="native-checkbox"><input type="checkbox" checked={!!value} onChange={(e) => change(e.target.checked)} />{label}</label>;
  if (schema.type === "null") return <p>{label}：空值</p>;
  if (schema.type === "string" || ["integer", "number"].includes(schema.type)) return <Field label={label} displayLabel={schemaLabel(label)} description={schema.description}><input aria-label={label} autoComplete="off" type={schema.type === "string" ? /secret|password|api[_-]?key|access[_-]?token|bearer|session[_-]?token|authorization|cookie|private[_-]?key/i.test(label) ? "password" : "text" : "number"} placeholder={isRedacted(value) ? "已配置（隐藏）；输入以替换" : undefined} value={isRedacted(value) ? "" : value ?? ""} min={schema.minimum} max={schema.maximum} minLength={schema.minLength} maxLength={schema.maxLength} pattern={schema.pattern} step={schema.type === "integer" ? 1 : "any"} onChange={(e) => change(schema.type === "string" ? e.target.value : e.target.value === "" ? undefined : Number(e.target.value))} /></Field>;
  return <Field label={`${label}（任意结构）`} displayLabel={`${schemaLabel(label)}（JSON）`}><textarea aria-label={label} value={json ?? JSON.stringify(isRedacted(value) ? null : value ?? null, null, 2)} onChange={(e) => { setJson(e.target.value); try { change(JSON.parse(e.target.value)); setError(""); e.target.setCustomValidity(""); } catch { setError("请输入有效 JSON 值"); e.target.setCustomValidity("请输入有效 JSON 值"); } }} />{error && <small role="alert">{error}</small>}</Field>;
}
