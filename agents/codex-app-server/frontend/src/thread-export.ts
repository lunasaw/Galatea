import { toMessages } from "./messages";
import type { Session, Snapshot } from "./types";

type ReadSnapshot = (path: string, signal?: AbortSignal) => Promise<Snapshot>;

/** Read every page at one watermark without changing the visible conversation. */
export async function readExportSnapshot(read: ReadSnapshot, sessionId: string, signal?: AbortSignal): Promise<Snapshot> {
  const path = `/api/sessions/${encodeURIComponent(sessionId)}/snapshot?limit=200&view=all`;
  const first = await read(path, signal);
  const result = { ...first, messages: [...first.messages || []], steps: [...first.steps || []], turns: [...first.turns || []] };
  const seen = new Set<string>();
  let cursor = first.next_page;
  while (cursor) {
    if (seen.has(cursor)) throw new Error("会话历史分页异常，请重试导出");
    seen.add(cursor);
    const page = await read(`${path}&page=${encodeURIComponent(cursor)}`, signal);
    if (page.epoch !== first.epoch || page.watermark !== first.watermark)
      throw new Error("会话历史已更新，请重试导出");
    result.messages.push(...page.messages || []);
    result.steps.push(...page.steps || []);
    result.turns.push(...page.turns || []);
    if (page.coverage.status !== "complete") result.coverage = page.coverage;
    cursor = page.next_page;
  }
  result.next_page = null;
  return result;
}

function code(value: unknown, language = "") {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const fence = "`".repeat(Math.max(3, ...[...text.matchAll(/`+/g)].map(match => match[0].length + 1)));
  return `${fence}${language}\n${text}\n${fence}`;
}

function inline(value: string) {
  return value.replace(/[\r\n]+/g, " ").replace(/[\\`*_{}\[\]<>#]/g, "\\$&");
}

export function exportMarkdown(snapshot: Snapshot, session: Session, title: string) {
  const sections = [`# ${inline(title || "新会话")}`, `会话：${inline(session.thread_id || session.id)}`];
  const cwd = session.cwd || session.settings?.cwd;
  if (cwd) sections.push(`工作目录：${inline(cwd)}`);
  if (snapshot.coverage.status !== "complete")
    sections.push("> 部分历史记录不可用，以下为当前可读取的内容。");
  if (session.active_turn_id || snapshot.turns?.some(turn => turn.status === "inProgress"))
    sections.push("> 回复仍在进行中，以下内容截至本次导出时。");

  const sources = new Map(snapshot.messages?.map(row => [row.id, row]));
  for (const message of toMessages(snapshot)) {
    const parts = typeof message.content === "string" ? [{ type: "text" as const, text: message.content }] : message.content;
    const text: string[] = [];
    for (const part of parts) {
      if (part.type === "text" && part.text) text.push(part.text);
      else if (part.type === "reasoning" && part.text) text.push(`### 思考摘要\n\n${part.text}`);
      else if (part.type === "tool-call") {
        text.push(`### 工具：${inline(part.toolName)}`);
        if (part.argsText) text.push(code(part.argsText, "json"));
        if (part.result !== undefined) text.push(code(part.result));
      }
    }
    // Keep attachment references, including local files that have no image URL.
    const source = message.id ? sources.get(message.id) : undefined;
    for (const part of source?.content || []) {
      if (!["image", "localImage", "audio", "inputAudio", "file"].includes(part.type)) continue;
      const label = part.name || (["image", "localImage"].includes(part.type) ? "图片" : ["audio", "inputAudio"].includes(part.type) ? "音频" : "附件");
      if (part.url && /^https?:\/\//i.test(part.url))
        text.push(`[${inline(label)}](<${part.url.replace(/[<>\s]/g, c => encodeURIComponent(c))}>)`);
      else if (part.path) text.push(`${inline(label)}：${inline(part.path)}`);
      else text.push(`[${inline(label)}]`);
    }
    for (const question of source?.questions || []) {
      text.push(question.title);
      if (question.options?.length) text.push(question.options.map(option => `- ${inline(option)}`).join("\n"));
    }
    if (text.length) sections.push(`## ${message.role === "user" ? "用户" : "Codex"}\n\n${text.join("\n\n")}`);
  }
  return sections.join("\n\n") + "\n";
}

export function exportFilename(title: string) {
  const name = Array.from(title.replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, "_").replace(/[.\s]+$/g, "").trim()).slice(0, 80).join("");
  return `${!name || /^(con|prn|aux|nul|com\d|lpt\d)(\.|$)/i.test(name) ? "会话" : name}.md`;
}

export function threadLink(href: string, sessionId: string) {
  const url = new URL(href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("session", sessionId);
  return url.toString();
}
