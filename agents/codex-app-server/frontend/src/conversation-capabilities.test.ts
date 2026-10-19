import { test } from "node:test";
import assert from "node:assert/strict";
import { questionReplies, questionIdentity, replyText, answeredQuestions, visibleUserText, tokenBudget } from "./conversation-capabilities";

test("async replies preserve native identity, quoted title and arbitrary Unicode answer", () => {
  const rows = [{ questionItemId: questionIdentity("tool-中文", 1), question: "选哪个？", answer: "第二个\n\"quoted\"" }];
  assert.deepEqual(questionReplies(replyText(rows)), rows);
  assert.equal(visibleUserText(replyText(rows)), '> 选哪个？\n\n第二个\n"quoted"');
  const messages = [{ id: 'user', role: 'user' as const, text: replyText(rows), first_seq: '1', last_seq: '1' }];
  assert.deepEqual([...answeredQuestions(messages)], [rows[0].questionItemId]);
  assert.deepEqual(questionReplies('Example: ' + replyText(rows)), []);
  assert.deepEqual(questionReplies(replyText(rows) + ' trailing prose'), []);
  assert.deepEqual(questionReplies('<send_user_message_question_reply>{"answer":true}</send_user_message_question_reply>'), []);
});
test("token budgets reject fractions, negatives and precision loss", () => {
  assert.equal(tokenBudget(""), null); assert.equal(tokenBudget("50000"), 50000);
  for (const value of ["0", "-1", "1.5", "NaN", "Infinity", "9007199254740993"]) assert.throws(() => tokenBudget(value));
});
