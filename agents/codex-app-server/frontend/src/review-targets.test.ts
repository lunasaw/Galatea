import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { readReviewChoices } from "./review-targets";

test("review targets query local branches and the latest 100 commits with their full identity", async () => {
  const cwd = mkdtempSync(join(tmpdir(), "review repository "));
  const git = (args: string[]) => {
    const result = spawnSync("git", args, { cwd, encoding: "utf8", env: { ...process.env,
      GIT_AUTHOR_NAME: "Fixture", GIT_AUTHOR_EMAIL: "fixture@example.test",
      GIT_COMMITTER_NAME: "Fixture", GIT_COMMITTER_EMAIL: "fixture@example.test" } });
    return { exitCode: result.status!, stdout: result.stdout, stderr: result.stderr };
  };
  const caller = { async nativeCall(method: string, params: Record<string, unknown>) {
    assert.equal(method, "command/exec");
    assert.equal(params.cwd, cwd);
    assert.deepEqual(params.env, { GIT_OPTIONAL_LOCKS: "0" });
    const command = params.command as string[];
    assert.equal(command[0], "git");
    return git(command.slice(1));
  } };
  try {
    assert.equal(git(["init", "--initial-branch=main"]).exitCode, 0);
    assert.deepEqual((await readReviewChoices(caller, cwd, "commit")).choices, []);
    for (let i = 0; i < 102; i++) assert.equal(git(["commit", "--allow-empty", "-m", `修复 ${i} | review`]).exitCode, 0);
    assert.equal(git(["checkout", "-b", "feature/review"]).exitCode, 0);
    const branches = await readReviewChoices(caller, cwd, "baseBranch");
    assert.equal(branches.currentBranch, "feature/review");
    assert.deepEqual(branches.choices.map((row) => row.target), [
      { type: "baseBranch", branch: "feature/review" }, { type: "baseBranch", branch: "main" },
    ]);
    const commits = (await readReviewChoices(caller, cwd, "commit")).choices;
    assert.equal(commits.length, 100);
    const sha = git(["rev-parse", "HEAD"]).stdout.trim();
    assert.deepEqual(commits[0].target, { type: "commit", sha, title: "修复 101 | review" });
    assert.ok(commits[0].search?.includes(sha));
    assert.equal(commits.at(-1)?.label, "修复 2 | review");
    assert.equal(git(["checkout", "--detach"]).exitCode, 0);
    assert.equal((await readReviewChoices(caller, cwd, "baseBranch")).currentBranch, "分离 HEAD");
  } finally { rmSync(cwd, { recursive: true, force: true }); }
});

test("Git failures and incomplete commit output cannot become selectable review targets", async () => {
  await assert.rejects(readReviewChoices({ nativeCall: async () => ({ exitCode: 128, stdout: "", stderr: "not a git repository" }) }, "/missing", "commit"), /not a git repository/);
  for (const sha of ["partial-sha", "a".repeat(48)]) {
    const caller = { nativeCall: async (_: string, params: Record<string, unknown>) => ({ exitCode: 0,
      stdout: (params.command as string[])[1] === "log" ? `${sha}\0title` : "a".repeat(40), stderr: "" }) };
    await assert.rejects(readReviewChoices(caller, "/fixture", "commit"), /提交列表不完整/);
  }
});
