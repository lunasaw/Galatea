import type { Message } from "./types";

export type QuestionReply = { questionItemId: string; question: string; answer: string };
const opening = "<send_user_message_question_reply>";
const closing = "</send_user_message_question_reply>";

/** Same envelope as Codex CLI async_question_reply.rs; never interpret partial text. */
export function questionReplies(text: string): QuestionReply[] {
  let value = text.trim();
  if (value.startsWith("# Context from my IDE setup:\n"))
    value = value.split("\n## My request for Codex:\n").at(-1)!.trim();
  if (!value.startsWith(opening) || !value.endsWith(closing)) return [];
  try {
    const data: unknown = JSON.parse(value.slice(opening.length, -closing.length));
    const rows = Array.isArray(data) ? data : [data];
    return rows.length && rows.every((r) => r && typeof r.questionItemId === "string" &&
      typeof r.question === "string" && typeof r.answer === "string") ? rows : [];
  } catch { return []; }
}

export function replyText(replies: QuestionReply[]) {
  return opening + JSON.stringify(replies) + closing;
}

export function visibleUserText(text: string) {
  const replies = questionReplies(text);
  return replies.length ? replies.map((r) => `> ${r.question}\n\n${r.answer}`).join("\n\n") : text;
}

export function answeredQuestions(messages: Message[]) {
  return new Set(messages.filter((m) => m.role === "user")
    .flatMap((m) => questionReplies(m.text).map((r) => r.questionItemId)));
}

export const questionIdentity = (itemId: string, index: number) =>
  JSON.stringify(["request_user_input_async", itemId, index]);

export function textInput(text: string) {
  if (!text.trim()) throw new Error("请输入内容");
  return [{ type: "text", text, text_elements: [] }];
}

export function tokenBudget(value: string): number | null {
  if (!value.trim()) return null;
  const budget = Number(value);
  if (!Number.isSafeInteger(budget) || budget <= 0) throw new Error("Token 预算必须为正整数");
  return budget;
}
