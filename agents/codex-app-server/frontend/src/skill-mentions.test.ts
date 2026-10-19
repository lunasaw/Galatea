import assert from "node:assert/strict";
import test from "node:test";
import { filterSkills, skillMentionAt, skillReferences } from "./skill-mentions";

test("skill completion follows the caret and preserves the full token range", () => {
  assert.deepEqual(skillMentionAt("$", 1), { start: 0, end: 1, query: "" });
  assert.deepEqual(skillMentionAt("请使用 $plugin:skill 完成任务", 11), { start: 4, end: 17, query: "plugin" });
  assert.deepEqual(skillMentionAt("第一行\n$训练", 7), { start: 4, end: 7, query: "训练" });
  assert.deepEqual(skillMentionAt("($2-review)", 10), { start: 1, end: 10, query: "2-review" });
  assert.equal(skillMentionAt("$review ", 8), null);
  assert.equal(skillMentionAt("$review", 1, 7), null);
  assert.equal(skillMentionAt("$review then", 11), null);
});

test("escaped dollars, shell expressions, paths and inline math are ordinary text", () => {
  for (const text of ["\\$review", "$$review", "${HOME}", "$(pwd)", "price$10", "$HOME/bin", "$review$", "a$review"]) {
    assert.equal(skillMentionAt(text, text.length), null, text);
  }
  assert.equal(skillMentionAt("$HOME/bin", 5), null);
});

test("native skill discovery filters disabled entries, keeps paths distinct and supports display names", () => {
  const skill = { name: "review", path: "/project/review/SKILL.md", enabled: true, description: "Review changes" };
  const rows = skillReferences({ data: [
    { skills: [skill, { ...skill, path: "/disabled/SKILL.md", enabled: false }] },
    { skills: [skill, { ...skill, path: "/plugin/review/SKILL.md", interface: { displayName: "代码审查", shortDescription: "检查修改" } }] },
  ] });
  assert.equal(rows.length, 2);
  assert.equal(filterSkills(rows, "REVIEW").length, 2);
  assert.equal(filterSkills(rows, "代码")[0].path, "/plugin/review/SKILL.md");
  assert.equal(filterSkills(rows, "检查")[0].name, "review");
  assert.deepEqual(filterSkills(rows, "missing"), []);
});
