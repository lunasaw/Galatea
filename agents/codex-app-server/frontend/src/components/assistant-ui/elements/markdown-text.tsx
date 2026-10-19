import { deferred } from "@/deferred";

export const MarkdownText = deferred(() => import("./markdown-text-content").then(m => ({ default: m.MarkdownText })));
export const StaticMarkdown = deferred(() => import("./markdown-text-content").then(m => ({ default: m.StaticMarkdown })));
