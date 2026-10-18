import { test } from "node:test";
import assert from "node:assert/strict";
import { exportFilename, exportMarkdown, readExportSnapshot, threadLink } from "./thread-export.ts";
import type { Snapshot } from "./types.ts";

const snapshot = (extra: Partial<Snapshot> = {}): Snapshot => ({
  epoch: "e", watermark: "e:900", next_page: null, coverage: { status: "complete" },
  messages: [], steps: [], turns: [], ...extra,
});

test("exports all 450 messages in chronological order at a fixed watermark", async () => {
  const rows = Array.from({ length: 450 }, (_, i) => ({
    id: String(i), role: "user" as const, text: `message ${i}`, first_seq: String(i), last_seq: String(i),
  }));
  const pages = [snapshot({ messages: rows.slice(250), next_page: "older +/=1" }),
    snapshot({ messages: rows.slice(50, 250), next_page: "oldest" }), snapshot({ messages: rows.slice(0, 50) })];
  const requests: string[] = [];
  const result = await readExportSnapshot(async path => { requests.push(path); return pages[requests.length - 1]; }, "session/a");
  assert.deepEqual(requests, [
    "/api/sessions/session%2Fa/snapshot?limit=200&view=all",
    "/api/sessions/session%2Fa/snapshot?limit=200&view=all&page=older%20%2B%2F%3D1",
    "/api/sessions/session%2Fa/snapshot?limit=200&view=all&page=oldest",
  ]);
  assert.equal(pages[0].messages!.length, 200, "must not mutate the loaded page");
  const markdown = exportMarkdown(result, { id: "s", status: "ready" }, "历史");
  assert.deepEqual([...markdown.matchAll(/^message (\d+)$/gm)].map(m => Number(m[1])), Array.from({ length: 450 }, (_, i) => i));
});

test("preserves Markdown, same-event replay order, visible summaries, tools, and attachment references", () => {
  const reply = "## 完整回答\n\n```python\nprint('hello')\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n最后一行。";
  const result = snapshot({
    messages: [
      { id: "a", role: "assistant", text: reply, first_seq: "1", last_seq: "1", replay_order: 4 },
      { id: "u", role: "user", text: "用户原文", first_seq: "1", last_seq: "1", replay_order: 1,
        content: [{ type: "text" }, { type: "localImage", path: "/tmp/image.png" }, { type: "file", name: "report.csv", url: "https://example.test/report.csv" }] },
    ],
    steps: [
      { id: "r", type: "reasoning", first_seq: "1", last_seq: "1", replay_order: 2, status: "completed", detail: { summary: ["公开摘要"], content: ["PRIVATE_REASONING"] } },
      { id: "t", type: "tool", tool: "fixture", first_seq: "1", last_seq: "1", replay_order: 3, arguments: { cmd: "example" }, result: "```\n工具结果\n```" },
    ],
  });
  const markdown = exportMarkdown(result, { id: "s", thread_id: "thread", status: "ready", cwd: "/workspace" }, "标题\n第二行");
  assert.ok(markdown.includes(reply + "\n"));
  assert.ok(markdown.indexOf("用户原文") < markdown.indexOf("公开摘要"));
  assert.ok(markdown.indexOf("公开摘要") < markdown.indexOf("工具结果"));
  assert.ok(markdown.indexOf("工具结果") < markdown.indexOf(reply));
  assert.ok(markdown.includes("````\n```\n工具结果\n```\n````"));
  assert.ok(markdown.includes("图片：/tmp/image.png"));
  assert.ok(markdown.includes("[report.csv](<https://example.test/report.csv>)"));
  assert.ok(!markdown.includes("[附件]"));
  assert.ok(!markdown.includes("PRIVATE_REASONING"));
  assert.ok(markdown.startsWith("# 标题 第二行\n"));
});

test("does not silently export mixed history or a failed page", async () => {
  for (const second of [snapshot({ epoch: "changed" }), snapshot({ watermark: "e:901" }), snapshot({ next_page: "again" })]) {
    let count = 0;
    await assert.rejects(readExportSnapshot(async () => count++ ? second : snapshot({ next_page: "again" }), "s"));
  }
  let count = 0;
  await assert.rejects(readExportSnapshot(async () => {
    if (count++) throw new Error("network failure");
    return snapshot({ next_page: "next" });
  }, "s"), /network failure/);
});

test("marks partial history and an in-progress response in the exported document", async () => {
  let count = 0;
  const result = await readExportSnapshot(async () => count++
    ? snapshot({ coverage: { status: "incomplete" } }) : snapshot({ next_page: "next" }), "s");
  const markdown = exportMarkdown(result, { id: "s", status: "running", active_turn_id: "turn" }, "测试");
  assert.match(markdown, /部分历史记录不可用/);
  assert.match(markdown, /回复仍在进行中/);
});

test("uses portable filenames and copies a link without unrelated URL parameters", () => {
  assert.equal(exportFilename('a/b:c?d*'), "a_b_c_d_.md");
  assert.equal(exportFilename(" ... "), "会话.md");
  assert.equal(exportFilename("CON"), "会话.md");
  assert.equal(exportFilename("中文标题"), "中文标题.md");
  assert.ok(exportFilename("长".repeat(100)).length <= 83);
  const link = new URL(threadLink("https://example.test/chat?token=secret#fragment", "session/a b"));
  assert.equal(link.pathname, "/chat");
  assert.equal(link.searchParams.get("session"), "session/a b");
  assert.deepEqual([...link.searchParams.keys()], ["session"]);
  assert.equal(link.hash, "");
});
