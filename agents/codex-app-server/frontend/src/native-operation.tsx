import { useState } from "react";
import { Button } from "./components/ui/button";
import { Facts, Feedback, useAction } from "./capability-controls";
import { SchemaInput, schemaDefault } from "./schema-form";
import { resolveSchema } from "./schema-data";
import { sharedMutation } from "./native-mutation";
import { type Obj } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";

/** Product-scoped advanced controls; the native schema is the field authority. */
export function NativeOperation({ state, method, title, description, context = {}, initial, transform, onResult, showHeading = true }: {
  state: ConsoleState; method: string; title: string; description?: string; context?: Obj;
  initial?: Obj | null; transform?: (schema: Obj) => Obj; onResult?: (result: Obj) => void | Promise<void>; showHeading?: boolean;
}) {
  const root = { definitions: state.nativeProtocol?.definitions || {} };
  const raw = resolveSchema(state.nativeProtocol?.params[method] as Obj || { type: "null" }, root);
  const schema = transform ? transform(raw) : raw;
  const visible = { ...schema, ...(schema.properties ? { properties: Object.fromEntries(Object.entries(schema.properties).filter(([key]) => !(key in context))), required: (schema.required || []).filter((key: string) => !(key in context)) } : {}) };
  const [params, setParams] = useState<any>(() => initial === undefined ? schemaDefault(visible, root) : initial);
  const [result, setResult] = useState<Obj>(); const action = useAction();
  if (!state.nativeProtocol?.methods.includes(method)) return <p className="muted">当前原生版本未提供{title}。</p>;
  return <section className="native-operation">{showHeading && <h4>{title}</h4>}{description && <p className="muted">{description}</p>}
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      const output = await sharedMutation(`operation:${method}:${context.threadId || "global"}`).run(method, schema.type === "null" ? null : { ...params, ...context });
      setResult(output); await onResult?.(output);
      if (output.session?.id) { await store.refreshSessions(); await store.select(output.session.id); }
    }, "请求已完成"); }}><fieldset disabled={action.busy}><SchemaInput schema={visible} root={root} label={title} value={params} change={setParams} /></fieldset><div className="settings-actions"><Button disabled={action.busy}>{action.busy ? "处理中…" : title}</Button></div></form>
    {result && <details open><summary>结果</summary><Facts value={result} /></details>}<Feedback error={action.error} status={action.status} />
  </section>;
}
