import { useEffect, useRef, useState } from "react";
import { AppBridge, PostMessageTransport, type McpUiDisplayMode, type McpUiHostContext } from "@modelcontextprotocol/ext-apps/app-bridge";
import { z } from "zod/v4";
import { native, type Obj } from "./native";
import { api, requestKey, consoleStore as store } from "./store";
import { IntegrationsClient, httpUrl } from "./integrations-data";
import { Facts, Feedback } from "./capability-controls";
import { Button } from "./components/ui/button";
import { appCapabilities, appHostContext, appToolAllowed, downloadResource } from "./mcp-app-host";
import "./mcp-app.css";

type Approval = { title: string; detail: unknown; acceptLabel?: string; run?: () => void; settle: (accepted: boolean) => void };
export type McpAppProps = {
  server: string; threadId: string; uri: string; instanceId: string; toolName?: string;
  args: Obj; result?: Obj; status?: string; toolError?: string;
  placement?: "panel";
};

/** Every operation is bound to the server, thread and invocation that created this view. */
export function McpAppView(props: McpAppProps) {
  const { server, threadId, uri, instanceId, toolName } = props;
  const frame = useRef<HTMLIFrameElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const bridge = useRef<AppBridge | undefined>(undefined);
  const hostContext = useRef<McpUiHostContext>({});
  const updateHostContext = (patch: McpUiHostContext) => {
    hostContext.current = { ...hostContext.current, ...patch };
    bridge.current?.setHostContext(hostContext.current);
  };
  const latest = useRef(props); latest.current = props;
  const flush = useRef<(() => Promise<void>) | undefined>(undefined);
  const close = useRef<(() => Promise<void>) | undefined>(undefined);
  const modeRef = useRef<McpUiDisplayMode>("inline");
  const [mode, setMode] = useState<McpUiDisplayMode>("inline");
  const [pinned, setPinned] = useState(false);
  const [error, setError] = useState("");
  const [height, setHeight] = useState(360);
  const [ready, setReady] = useState(false);
  const [closed, setClosed] = useState(false);
  const [generation, setGeneration] = useState(0);
  const identity = JSON.stringify([server, threadId, uri, instanceId, toolName, generation]);
  const [src, setSrc] = useState<{ identity: string; url: string }>();
  const [request, setRequest] = useState<Approval>();
  const [logs, setLogs] = useState<Obj[]>([]);
  const cancelRequest = useRef<(() => void) | undefined>(undefined);
  const changeMode = (next: McpUiDisplayMode, pin = false) => {
    modeRef.current = next; setMode(next); setPinned(pin);
    if (next === "inline") surface.current?.hidePopover();
    else if (!surface.current?.matches(":popover-open")) surface.current?.showPopover();
    updateHostContext({ displayMode: next });
  };

  useEffect(() => {
    if (closed) return;
    let disposed = false, closing = false, initialized = false, inputSent = false, outcomeSent = false, sending = Promise.resolve();
    setError(""); setReady(false); setSrc(undefined); setLogs([]);
    hostContext.current = { ...appHostContext(), displayMode: modeRef.current };
    const app = new AppBridge(null, { name: "Codex Web", version: "0.1.0" }, appCapabilities,
      { hostContext: hostContext.current });
    bridge.current = app;
    const scope = { server, threadId, uri, instanceId };
    const assertOpen = () => { if (disposed || closing) throw new Error("应用已关闭"); };
    const report = (error: unknown) => { if (!disposed) setError(error instanceof Error ? error.message : String(error)); };
    const approve = (title: string, detail: unknown, run?: () => void, acceptLabel?: string) => {
      assertOpen();
      if (cancelRequest.current) throw new Error("请先处理当前应用请求");
      return new Promise<boolean>(resolve => {
        const settle = (accepted: boolean) => { cancelRequest.current = undefined; setRequest(undefined); resolve(accepted); };
        cancelRequest.current = () => settle(false); setRequest({ title, detail, run, acceptLabel, settle });
      });
    };
    const inventory = async () => {
      const row = (await new IntegrationsClient(native).mcps(threadId)).find(s => s.name === server);
      assertOpen(); if (!row) throw new Error("MCP 服务不可用"); return row;
    };
    const flushData = () => sending = sending.then(async () => {
      if (disposed || !initialized) return;
      const current = latest.current;
      if (!inputSent) {
        await app.sendToolInput({ arguments: current.args || {} }); inputSent = true;
      }
      if (outcomeSent) return;
      if (["cancelled", "interrupted"].includes(current.status || "")) {
        await app.sendToolCancelled({ reason: current.toolError || "操作已取消" }); outcomeSent = true;
      } else if (current.result) {
        await app.sendToolResult(current.result as Parameters<AppBridge["sendToolResult"]>[0]); outcomeSent = true;
      } else if (current.status === "failed" || current.toolError) {
        await app.sendToolResult({ content: [{ type: "text", text: current.toolError || "工具执行失败" }], isError: true }); outcomeSent = true;
      }
    }).catch(report);
    flush.current = flushData;
    const timeout = setTimeout(() => report(new Error("应用初始化超时，请重新加载")), 20000);
    app.onerror = report;
    app.oninitialized = () => {
      if (disposed || initialized) return;
      initialized = true; clearTimeout(timeout); setReady(true); setError("");
      void flushData();
      if (toolName) void inventory().then(row => {
        const tool = row.tools[toolName];
        if (tool && !disposed) updateHostContext({ toolInfo: { id: instanceId, tool: { ...tool, name: tool.name || toolName, inputSchema: tool.inputSchema || { type: "object" } } as any } });
      }).catch(report);
    };
    app.onsizechange = ({ height }) => { if (Number.isFinite(height)) setHeight(Math.min(900, Math.max(120, height!))); };
    app.onrequestdisplaymode = async ({ mode }) => { assertOpen(); changeMode(mode); return { mode }; };
    app.onmessage = async message => {
      assertOpen();
      if (!await approve("应用请求发送消息", message.content, undefined, "发送到此聊天")) return { isError: true };
      assertOpen();
      await contextWrites;
      assertOpen();
      await api("/api/mcp-app/messages", { ...scope, message }, requestKey());
      void store.refreshSessions().catch(store.fail); store.schedule();
      return {};
    };
    // Serialize updates so rapid selection changes cannot overwrite newer state.
    let contextWrites = Promise.resolve();
    app.onupdatemodelcontext = context => {
      const operation = contextWrites.then(async () => { assertOpen(); await api("/api/mcp-app/context", { ...scope, context }, requestKey()); return {}; });
      contextWrites = operation.then(() => {}, () => {}); return operation;
    };
    app.onopenlink = async ({ url }) => {
      const target = httpUrl(url); if (!target) throw new Error("仅支持 HTTP 或 HTTPS 链接");
      const accepted = await approve("应用请求打开链接", target, () => window.open(target, "_blank", "noopener,noreferrer"), "打开链接");
      return { isError: !accepted };
    };
    app.ondownloadfile = async ({ contents }) => {
      if (!contents.length || contents.length > 8) throw new Error("每次可下载 1 至 8 个文件");
      if (!await approve("应用请求下载文件", contents.map(p => p.type === "resource_link" ? p.name : p.resource.uri))) return { isError: true };
      const files = [];
      for (const part of contents) {
        assertOpen();
        if (part.type === "resource") files.push(downloadResource(part.resource));
        else {
          const result = await native("mcpServer/resource/read", { threadId, server, uri: part.uri });
          const resource = result.contents?.find((r: Obj) => r.uri === part.uri);
          if (!resource) throw new Error("服务未返回请求的下载资源");
          files.push(downloadResource(resource, part.name));
        }
      }
      assertOpen();
      if (files.reduce((size, f) => size + f.blob.size, 0) > 16 * 1024 * 1024) throw new Error("下载总量超过 16 MiB");
      for (const file of files) {
        const url = URL.createObjectURL(file.blob), a = document.createElement("a");
        a.href = url; a.download = file.name; document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      return {};
    };
    app.oncalltool = async ({ name, arguments: arguments_, _meta }) => {
      const row = await inventory(), tool = row.tools[name] as Obj | undefined;
      if (!appToolAllowed(tool)) throw new Error("此应用不能调用该工具");
      if (tool?._meta?.["io.codex.host/require-call-proof"] === true) {
        const challenge = await api("/api/mcp-app/challenges", { ...scope, name, arguments: arguments_ || {} }, requestKey()) as Obj;
        if (challenge.write && challenge.state === "pending") {
          const accepted = await approve(`应用请求执行 ${name}`, arguments_);
          await api("/api/mcp-app/confirmations", { call_id: challenge.call_id, challenge_id: challenge.challenge_id, decision: accepted ? "confirmed" : "denied" }, requestKey());
          if (!accepted) return { content: [{ type: "text", text: "用户未允许此操作" }], isError: true };
        }
        assertOpen();
        return await api("/api/mcp-app/tool-calls", { call_id: challenge.call_id }, requestKey());
      }
      if (tool?.annotations?.readOnlyHint !== true && !await approve(`应用请求执行 ${name}`, arguments_))
        return { content: [{ type: "text", text: "用户未允许此操作" }], isError: true };
      assertOpen();
      return await native("mcpServer/tool/call", { threadId, server, tool: name, arguments: arguments_, _meta });
    };
    app.setRequestHandler("tools/list", { params: z.object({ cursor: z.string().optional() }).optional() }, async () => ({ tools: Object.values((await inventory()).tools).filter(appToolAllowed) as any }));
    app.onreadresource = async ({ uri }) => { assertOpen(); return await native("mcpServer/resource/read", { threadId, server, uri }); };
    app.onlistresources = async () => ({ resources: (await inventory()).resources || [] }) as any;
    app.onlistresourcetemplates = async () => ({ resourceTemplates: (await inventory()).resourceTemplates || [] }) as any;
    app.onloggingmessage = log => { if (!disposed) setLogs(old => [...old.slice(-49), { level: log.level, logger: log.logger, data: String(typeof log.data === "string" ? log.data : JSON.stringify(log.data)).slice(0, 4000) }]); };
    const teardown = async () => {
      if (disposed || closing) return;
      closing = true;
      cancelRequest.current?.();
      try { if (initialized) await app.teardownResource({}, { timeout: 1500 }); } catch { /* Close even if the view does not acknowledge. */ }
      if (disposed) return;
      await contextWrites;
      if (disposed) return;
      try { await api("/api/mcp-app/context", { ...scope, context: {} }, requestKey()); }
      catch (error) { closing = false; report(error); return; }
      if (disposed) return;
      changeMode("inline"); setSrc(undefined); setReady(false); setClosed(true);
    };
    close.current = teardown;
    app.onrequestteardown = () => { void teardown().catch(report); };
    const theme = new MutationObserver(() => updateHostContext(appHostContext()));
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    const resize = new ResizeObserver(() => {
      const rect = frame.current?.getBoundingClientRect();
      if (rect?.width && rect.height) updateHostContext({ containerDimensions: { width: rect.width, height: rect.height } });
    });
    resize.observe(frame.current!);
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && modeRef.current !== "inline") changeMode("inline"); };
    window.addEventListener("keydown", escape);
    void app.connect(new PostMessageTransport(frame.current!.contentWindow!, frame.current!.contentWindow!)).then(() => {
      if (!disposed) setSrc({ identity, url: "/api/mcp-app?" + new URLSearchParams({ threadId, server, uri }) });
    }).catch(report);
    return () => {
      disposed = true; cancelRequest.current?.(); clearTimeout(timeout); theme.disconnect(); resize.disconnect(); window.removeEventListener("keydown", escape);
      bridge.current = undefined; flush.current = undefined; close.current = undefined;
      // Explicit close gives the view time to persist; unexpected unmount is bounded best effort.
      if (initialized && !closing) void app.teardownResource({}, { timeout: 500 }).catch(() => {}).finally(() => app.close());
      else void app.close();
    };
  }, [identity, closed]);
  useEffect(() => { void flush.current?.(); }, [props.args, props.result, props.status, props.toolError]);

  return <section className="mcp-app-view" data-placement={props.placement}>
    {closed ? <Button variant="outline" onClick={() => { setClosed(false); setGeneration(g => g + 1); }}>重新打开应用</Button> : <>
      {mode !== "inline" && <Button variant="outline" onClick={() => changeMode("inline")}>返回内联视图</Button>}
      <div ref={surface} popover="manual" className="mcp-app-surface" data-mode={mode} data-pinned={pinned}>
        <header className="mcp-app-toolbar" aria-label={`${server} 应用控制`}>
          <strong>{server}</strong>
          {mode !== "inline" && <Button variant="ghost" onClick={() => changeMode("inline")}>返回内联</Button>}
          <Button variant="ghost" onClick={() => changeMode("fullscreen")}>全屏</Button>
          <Button variant="ghost" onClick={() => changeMode("pip")}>画中画</Button>
          {props.placement !== "panel" && <Button variant="ghost" aria-pressed={pinned} onClick={() => changeMode(pinned ? "inline" : "pip", !pinned)}>固定到右侧</Button>}
          <Button variant="ghost" onClick={() => void close.current?.()}>关闭应用</Button>
        </header>
        {!ready && !error && <p role="status">正在连接应用…</p>}
        <iframe key={identity} ref={frame} src={src?.identity === identity ? src.url : undefined} title={`${server} 交互视图`} sandbox="allow-scripts" referrerPolicy="no-referrer" height={height} />
        {request && <section className="native-card" role="region" aria-label="应用请求"><strong>{request.title}</strong><Facts value={request.detail} />
          <Button type="button" onClick={() => { try { request.run?.(); request.settle(true); } catch (error) { setError(String(error)); request.settle(false); } }}>{request.acceptLabel || "允许本次"}</Button>
          <Button type="button" variant="outline" onClick={() => request.settle(false)}>拒绝</Button>
        </section>}
        <Feedback error={error} />
        {error && <Button variant="outline" onClick={() => { setSrc(undefined); setGeneration(g => g + 1); }}>重新加载应用</Button>}
        {!!logs.length && <details className="mcp-app-logs"><summary>应用日志（最近 50 条）</summary>{logs.map((log, i) => <pre key={i}>{log.level} {log.logger} {log.data}</pre>)}</details>}
      </div>
    </>}
  </section>;
}
