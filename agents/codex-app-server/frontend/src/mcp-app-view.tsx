import { useEffect, useRef, useState } from "react";
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { native, type Obj } from "./native";
import { IntegrationsClient, httpUrl } from "./integrations-data";
import { Facts, Feedback } from "./capability-controls";
import { Button } from "./components/ui/button";

/** An App can only call the MCP server and thread that produced this view. */
export function McpAppView({ server, threadId, uri, args, result }: {
  server: string; threadId: string; uri: string; args: Obj; result?: Obj;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const bridge = useRef<AppBridge | undefined>(undefined);
  const latest = useRef(result); latest.current = result;
  const [error, setError] = useState("");
  const [height, setHeight] = useState(360);
  const [ready, setReady] = useState(false);
  const [link, setLink] = useState("");
  const [src, setSrc] = useState<string>();
  const [request, setRequest] = useState<{ name: string; args: unknown; settle: (accepted: boolean) => void }>();
  const cancelRequest = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    let disposed = false; setError(""); setReady(false); setSrc(undefined);
    const app = new AppBridge(null, { name: "Codex Web", version: "0.1.0" }, {
      openLinks: {}, serverTools: {}, serverResources: {}, logging: {}, sandbox: { permissions: {} },
    }, { hostContext: { theme: document.documentElement.classList.contains("dark") ? "dark" : "light", displayMode: "inline", locale: navigator.language } });
    bridge.current = app;
    app.onerror = (error) => { if (!disposed) setError(error.message); };
    app.oninitialized = async () => {
      if (disposed) return;
      await app.sendToolInput({ arguments: args });
      if (latest.current) await app.sendToolResult(latest.current as Parameters<AppBridge["sendToolResult"]>[0]);
      setReady(true);
    };
    app.onsizechange = ({ height }) => { if (Number.isFinite(height)) setHeight(Math.min(900, Math.max(120, height!))); };
    app.onopenlink = async ({ url }) => {
      const target = httpUrl(url); if (!target) throw new Error("仅支持 HTTP 或 HTTPS 链接");
      setLink(target); return {};
    };
    const inventory = () => new IntegrationsClient(native).mcps(threadId);
    app.oncalltool = async ({ name, arguments: arguments_, _meta }) => {
      const row = (await inventory()).find((s) => s.name === server);
      if (!row?.tools?.[name]) throw new Error("此应用不能调用该工具");
      if (disposed) throw new Error("应用已关闭");
      if ((row.tools[name] as Obj).annotations?.readOnlyHint !== true) {
        if (cancelRequest.current) throw new Error("请先处理当前应用请求");
        const accepted = await new Promise<boolean>((resolve) => {
          const settle = (accepted: boolean) => { cancelRequest.current = undefined; setRequest(undefined); resolve(accepted); };
          cancelRequest.current = () => settle(false); setRequest({ name, args: arguments_, settle });
        });
        if (!accepted || disposed) return { content: [{ type: "text", text: "用户未允许此操作" }], isError: true };
      }
      return await native("mcpServer/tool/call", { threadId, server, tool: name, arguments: arguments_, _meta });
    };
    app.onreadresource = async ({ uri }) => await native("mcpServer/resource/read", { threadId, server, uri });
    app.onlistresources = async () => ({ resources: (await inventory()).find((s) => s.name === server)?.resources || [] }) as any;
    app.onlistresourcetemplates = async () => ({ resourceTemplates: (await inventory()).find((s) => s.name === server)?.resourceTemplates || [] }) as any;
    const theme = new MutationObserver(() => app.setHostContext({ theme: document.documentElement.classList.contains("dark") ? "dark" : "light" }));
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    void app.connect(new PostMessageTransport(frame.current!.contentWindow!, frame.current!.contentWindow!)).then(() => {
      if (!disposed) setSrc("/api/mcp-app?" + new URLSearchParams({ threadId, server, uri }));
    }).catch((error) => { if (!disposed) setError(String(error)); });
    return () => { disposed = true; cancelRequest.current?.(); theme.disconnect(); bridge.current = undefined; void app.close(); };
  }, [server, threadId, uri]);
  useEffect(() => {
    if (ready && result) void bridge.current?.sendToolResult(result as Parameters<AppBridge["sendToolResult"]>[0]).catch((error) => setError(String(error)));
  }, [result]);
  return <section className="mcp-app-view">
    <iframe ref={frame} src={src} title={`${server} 交互视图`} sandbox="allow-scripts" referrerPolicy="no-referrer" height={height} />
    {request && <section className="native-card"><strong>应用请求执行 {request.name}</strong><Facts value={request.args} />
      <Button type="button" onClick={() => request.settle(true)}>允许本次</Button><Button type="button" variant="outline" onClick={() => request.settle(false)}>拒绝</Button>
    </section>}
    {link && <a href={link} target="_blank" rel="noopener noreferrer">打开应用链接</a>}
    <Feedback error={error} />
  </section>;
}
