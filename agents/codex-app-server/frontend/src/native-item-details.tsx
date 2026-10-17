import { useEffect, useState } from "react";
import { ImageIcon, Users, Workflow } from "lucide-react";
import { native, type Obj } from "./native";
import { agentTitle, openAgentThread } from "./subagent-data";
import { WorkspaceLink } from "./workspace-file";
import { Button } from "./components/ui/button";
import { Feedback } from "./capability-controls";
import type { Step } from "./types";

export function NativeImage({ data, path, caption }: { data?: string; path?: string; caption: string }) {
  const [src, setSrc] = useState(""); const [error, setError] = useState("");
  useEffect(() => {
    let alive = true; setSrc(""); setError("");
    if (data) {
      setSrc(/^data:image\/(png|jpeg|webp|gif);base64,/.test(data) ? data : `data:image/png;base64,${data}`);
    } else if (path) {
      void native("fs/readFile", { path }).then((result) => {
        if (!alive) return;
        const mime = ({ jpg: "jpeg", jpeg: "jpeg", webp: "webp", gif: "gif" } as Obj)[path.split(".").at(-1)?.toLowerCase() || ""] || "png";
        setSrc(`data:image/${mime};base64,${result.dataBase64}`);
      }).catch((error) => { if (alive) setError(String(error)); });
    }
    return () => { alive = false; };
  }, [data, path]);
  return <figure className="native-image-result">{src && <img src={src} alt={caption} loading="lazy" onError={() => setError("图片无法显示")} />}
    {path && <figcaption><WorkspaceLink href={path}>{path.split("/").at(-1)}</WorkspaceLink></figcaption>}
    {!src && !error && <small>正在读取图片…</small>}<Feedback error={error} /></figure>;
}

export function NativeLocalAudio({ path }: { path: string }) {
  const [src, setSrc] = useState(""); const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false; setSrc(""); setError("");
    const mime = ({ wav: "wav", mp3: "mpeg", m4a: "mp4", webm: "webm", ogg: "ogg", flac: "flac" } as Obj)[path.split(".").at(-1)?.toLowerCase() || ""];
    if (!mime) { setError("不支持此音频格式"); return; }
    void native("fs/readFile", { path }).then((result) => { if (!disposed) setSrc(`data:audio/${mime};base64,${result.dataBase64}`); })
      .catch((error) => { if (!disposed) setError(String(error)); });
    return () => { disposed = true; };
  }, [path]);
  return <div>{src && <audio controls preload="none" src={src} aria-label={path.split("/").at(-1)} />}<Feedback error={error} /></div>;
}

export function NativeImageActivity({ step }: { step: Step }) {
  const detail = step.detail as Obj;
  const viewing = step.type === "imageView";
  const pending = !viewing && ["inProgress", "running"].includes(detail.status);
  return <section className="native-rich-item"><div className="native-rich-heading"><ImageIcon size={15} /><strong>{viewing ? "查看图片" : "生成图片"}</strong><span>{pending ? "生成中" : detail.status === "failed" ? "失败" : ""}</span></div>
    {detail.revisedPrompt && <p>{detail.revisedPrompt}</p>}
    {(detail.result || detail.path || detail.savedPath) && <NativeImage data={detail.result} path={detail.path || detail.savedPath} caption={detail.revisedPrompt || (viewing ? "查看的图片" : "生成的图片")} />}
    {detail.failure && <p role="alert">{detail.failure.type === "usageLimitExceeded" ? "图片生成额度已用尽" : "图片生成失败"}{detail.failure.resetsAt ? `，恢复时间：${new Date(detail.failure.resetsAt * 1000).toLocaleString()}` : ""}</p>}
  </section>;
}

export function NativeAgentActivity({ step }: { step: Step }) {
  const d = step.detail as Obj;
  const labels: Obj = { started: "已启动", interacted: "已交互", interrupted: "已中断", completed: "已完成", running: "运行中", pendingInit: "正在启动", errored: "失败", shutdown: "已关闭", notFound: "未找到" };
  const rows: Array<[string, Obj]> = step.type === "subAgentActivity" ? [[d.agentThreadId, { status: d.kind, path: d.agentPath }]]
    : [...new Set<string>([...(d.receiverThreadIds || []), ...Object.keys(d.agentsStates || {})])].map((id) => [id, d.agentsStates?.[id] || {}]);
  return <section className="native-agent-activity"><div className="native-rich-heading"><Users size={15} /><strong>子 Agent</strong></div>
    {d.prompt && <details><summary>任务</summary><p>{d.prompt}</p></details>}
    <ul>{rows.map(([id, agent]) => <li key={id}><Button type="button" variant="ghost" size="sm" onClick={() => openAgentThread(id)}>{agentTitle({ id, agentPath: agent.path })}</Button>
      <span>{labels[agent.status] || agent.status || "状态未知"}</span>{agent.message && <p>{agent.message}</p>}</li>)}</ul>
    {(d.model || d.reasoningEffort) && <small>{d.model} {d.reasoningEffort}</small>}
  </section>;
}

export function NativeHookActivity({ step }: { step: Step }) {
  const d = step.detail as Obj;
  const statuses: Obj = { running: "执行中", completed: "已完成", failed: "失败", blocked: "已阻止", stopped: "已停止" };
  const kinds: Obj = { warning: "提示", stop: "停止", feedback: "反馈", context: "上下文", error: "错误" };
  return <details className="native-rich-item"><summary className="native-rich-heading"><Workflow size={15} /><strong>{d.eventName || "Hook"}</strong><span>{statuses[d.status] || d.status}</span></summary>
    {d.sourcePath && <WorkspaceLink href={d.sourcePath}>{d.sourcePath}</WorkspaceLink>}
    <p>{d.handlerType} · {d.executionMode}{d.durationMs != null && ` · ${d.durationMs} ms`}</p>
    {(d.entries || []).map((entry: Obj, i: number) => <p key={i}><strong>{kinds[entry.kind] || entry.kind}：</strong>{entry.text}</p>)}
  </details>;
}
