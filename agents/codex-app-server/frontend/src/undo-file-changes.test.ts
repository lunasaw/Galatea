import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync, symlinkSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { undoPatchParams, undoUnavailableReason } from "./undo-file-changes";
const oid = (s: string | null) => s === null ? "0".repeat(40) : createHash("sha1").update(`blob ${Buffer.byteLength(s)}\0${s}`).digest("hex");
const patch = (path: string, before: string | null, after: string | null) => `diff --git a/${path} b/${path}\n${before === null ? "new file mode 100644\n" : after === null ? "deleted file mode 100644\n" : ""}index ${oid(before)}..${oid(after)}\n--- ${before === null ? "/dev/null" : "a/" + path}\n+++ ${after === null ? "/dev/null" : "b/" + path}\n@@ -${before === null ? "0,0" : "1"} +${after === null ? "0,0" : "1"} @@\n${before === null ? "" : "-" + before}${after === null ? "" : "+" + after}`;
function run(cwd: string, diff: string) {
  const {command} = undoPatchParams(cwd, diff);
  return spawnSync(command[0], command.slice(1), {cwd, encoding: "utf8"});
}
function fixture(t: any) {
  const dir = mkdtempSync(join(tmpdir(), "codex-undo-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(spawnSync("git", ["init", "-q", dir]).status, 0);
  return dir;
}

test("undo restores update/delete and removes only the exact created file", (t) => {
  const dir = fixture(t);
  writeFileSync(join(dir, "hello world.ts"), "new\n");
  writeFileSync(join(dir, "added.ts"), "created\n");
  writeFileSync(join(dir, "unrelated"), "keep me\n");
  const diff = patch("hello world.ts", "old\n", "new\n") + patch("added.ts", null, "created\n") + patch("deleted.ts", "deleted\n", null);
  assert.equal(undoUnavailableReason(diff, dir), "");
  const result = run(dir, diff);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(join(dir, "hello world.ts"), "utf8"), "old\n");
  assert.equal(readFileSync(join(dir, "deleted.ts"), "utf8"), "deleted\n");
  assert.equal(readFileSync(join(dir, "unrelated"), "utf8"), "keep me\n");
  assert.equal(existsSync(join(dir, "added.ts")), false);
  assert.notEqual(run(dir, diff).status, 0, "a second undo cannot reapply the patch");
});

test("later edits block the entire undo, even when outside the changed hunk", (t) => {
  const dir = fixture(t);
  writeFileSync(join(dir, "a"), "new\n");
  writeFileSync(join(dir, "b"), "new\nuser edit\n");
  const result = run(dir, patch("a", "old\n", "new\n") + patch("b", "old\n", "new\n"));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /后来|又有修改/);
  assert.equal(readFileSync(join(dir, "a"), "utf8"), "new\n");
});

test("rejects path traversal, symlinks, and missing version evidence", (t) => {
  const dir = fixture(t);
  writeFileSync(join(dir, "real"), "new\n");
  symlinkSync("real", join(dir, "link"));
  for (const diff of [patch("../outside", "old\n", "new\n"), patch("link", "old\n", "new\n"), patch("real", "old\n", "new\n").replace(/^index .*\n/m, "")]) {
    assert.notEqual(run(dir, diff).status, 0);
  }
  assert.equal(readFileSync(join(dir, "real"), "utf8"), "new\n");
});

test("supports a workspace nested inside a repository", (t) => {
  const dir = fixture(t); const cwd = join(dir, "nested"); mkdirSync(cwd);
  writeFileSync(join(cwd, "a"), "new\n");
  const result = run(cwd, patch("a", "old\n", "new\n"));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(join(cwd, "a"), "utf8"), "old\n");
});
