import { useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { Field, Facts, Feedback, useAction } from "./capability-controls";
import { native, type Obj } from "./native";
import { observeNative } from "./native-events";
import { requestKey, consoleStore as store } from "./store";
import { SchemaInput, schemaDefault } from "./schema-form";
import type { McpServer } from "./integrations-data";
import { deferred } from "./deferred";
const McpAppView = deferred(() => import("./mcp-app-view").then(m => ({ default: m.McpAppView })));

export function McpContent({ content }: { content: Obj[] }) {
  return <div className="mcp-content">{content.map((part, index) => {
    if (part.type === "text" || typeof part.text === "string") return <pre className="whitespace-pre-wrap" key={index}>{part.text}</pre>;
    if (part.type === "image" && /^image\/(png|jpeg|webp|gif)$/.test(part.mimeType)) return <img key={index} alt="MCP 图像结果" src={`data:${part.mimeType};base64,${part.data}`} />;
    if (part.type === "audio" && /^audio\//.test(part.mimeType)) return <audio key={index} controls src={`data:${part.mimeType};base64,${part.data}`} />;
    if (part.type === "resource") return <McpContent key={index} content={[part.resource]} />;
    if (part.blob && typeof part.mimeType === "string") return <a key={index} download href={`data:application/octet-stream;base64,${part.blob}`}>下载 {part.uri || "资源"}</a>;
    return <Facts key={index} value={part} />;
  })}</div>;
}
function ToolRunner({ server, name, tool, threadId }: { server: string; name: string; tool: Obj; threadId?: string }) {
  const schema = tool.inputSchema || { type: "object" }; const [args, setArgs] = useState(() => schemaDefault(schema));
  const [result, setResult] = useState<Obj>(); const action = useAction();
  const [invocation, setInvocation] = useState<{ id: string; args: Obj }>();
  const appUri = tool._meta?.ui?.resourceUri ?? tool._meta?.["ui/resourceUri"];
  return <details className="integration-tool"><summary>{tool.name || name}</summary><p>{tool.description}</p>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      const id = requestKey();
      const response = await native("mcpServer/tool/call", { threadId, server, tool: name, arguments: args }, id); setResult(response);
      setInvocation({ id, args });
      if (response.isError) throw new Error("工具返回执行错误，请查看结果");
    }, "调用完成"); }}><SchemaInput schema={schema} value={args} change={setArgs} /><Button disabled={action.busy || !threadId}>调用工具</Button>{!threadId && <p>请先选择一个会话。</p>}</form>
    {result && <><McpContent content={result.content || []} />{result.structuredContent && <Facts value={result.structuredContent} />}</>}
    {result && invocation && threadId && typeof appUri === "string" && appUri.startsWith("ui://") &&
      <McpAppView key={invocation.id} server={server} threadId={threadId} uri={appUri} instanceId={invocation.id} toolName={name} args={invocation.args} result={result} status="completed" />}
    <Feedback error={action.error} status={action.status} />
  </details>;
}
export function McpCapabilities({ server, threadId }: { server: McpServer; threadId?: string }) {
  const [uri, setUri] = useState(""); const [resource, setResource] = useState<Obj>(); const action = useAction();
  const [streamName, setStreamName] = useState(""); const [args, setArgs] = useState<Obj>({}); const [events, setEvents] = useState<Obj[]>([]);
  const [subscription, setSubscription] = useState(""); const cleanup = useRef<(() => void) | undefined>(undefined); const active = useRef(""); const [error, setError] = useState("");
  const generation = useRef(0);
  const [dropped, setDropped] = useState(0);
  const stop = async () => {
    const id = active.current;
    if (id) await native("mcpServer/event/stream/stop", { subscriptionId: id });
    cleanup.current?.(); active.current = ""; setSubscription("");
  };
  useEffect(() => {
    generation.current++;
    return () => { generation.current++; cleanup.current?.(); if (active.current) void native("mcpServer/event/stream/stop", { subscriptionId: active.current }).catch(store.fail); active.current = ""; };
  }, [server.name, threadId]);
  return <div className="mcp-capabilities">
    {Object.entries(server.tools || {}).map(([name, tool]) => <ToolRunner key={JSON.stringify([server.name, threadId, name])} {...{ name, tool, threadId }} server={server.name} />)}
    <h4>资源</h4><ul className="capability-list">{server.resources?.map((r) => <li key={r.uri}><button onClick={() => setUri(r.uri)}>{r.name} · {r.uri}</button></li>)}{server.resourceTemplates?.map((r) => <li key={r.uriTemplate}><button onClick={() => setUri(r.uriTemplate)}>{r.name} · {r.uriTemplate}</button></li>)}</ul>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => setResource(await native("mcpServer/resource/read", { server: server.name, uri, ...(threadId ? { threadId } : {}) })), "资源已读取"); }}><Field label="资源 URI"><input required value={uri} onChange={(e) => setUri(e.target.value)} /></Field><Button variant="outline" disabled={action.busy || !uri || /\{[^}]+\}/.test(uri)}>读取资源</Button></form>
    {resource && <McpContent content={resource.contents || []} />}
    <details><summary>事件订阅</summary><p>输入服务提供的订阅名称及参数。</p>
      <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
        const id = requestKey(); const version = generation.current;
        setEvents([]); setDropped(0); setError(""); let received = 0;
        const unobserve = await observeNative("mcpServer/event/stream/notification", (params) => {
          if (version === generation.current && params.subscriptionId === id) {
            received++; setDropped(Math.max(0, received - 200));
            setEvents((old) => [...old.slice(-199), params.notification]);
          }
        }, (e) => { if (version === generation.current) setError(String(e)); });
        if (version !== generation.current) { unobserve(); return; }
        cleanup.current = unobserve;
        try {
          await native("mcpServer/event/stream/start", { server: server.name, threadId, name: streamName, subscriptionId: id, arguments: args });
          if (version !== generation.current) { unobserve(); await native("mcpServer/event/stream/stop", { subscriptionId: id }); return; }
          active.current = id; setSubscription(id);
        } catch (error) { unobserve(); throw error; }
      }, "订阅已启动"); }}><Field label="订阅名称"><input required value={streamName} onChange={(e) => setStreamName(e.target.value)} /></Field><SchemaInput schema={{ type: "object", additionalProperties: true }} value={args} change={setArgs} />
        <Button disabled={action.busy || !!subscription || !threadId}>开始订阅</Button><Button type="button" variant="outline" disabled={action.busy || !subscription} onClick={() => void action.run(stop, "订阅已停止")}>停止订阅</Button></form>
      {dropped > 0 && <p className="muted">显示最近 200 条事件，较早的 {dropped} 条仍保留在原生事件记录中。</p>}
      <ol className="capability-list">{events.map((event, i) => <li key={i}><strong>{event.method}</strong><Facts value={event.params} /></li>)}</ol>
    </details><Feedback error={action.error || error} status={action.status} />
  </div>;
}
