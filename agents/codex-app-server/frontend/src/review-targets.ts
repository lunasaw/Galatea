export type ReviewTarget =
  | { type: "uncommittedChanges" }
  | { type: "baseBranch"; branch: string }
  | { type: "commit"; sha: string; title: string }
  | { type: "custom"; instructions: string };

export type ReviewChoice = { id: string; label: string; detail?: string; search?: string; target: ReviewTarget };
type CommandResult = { exitCode: number; stdout: string; stderr: string };
type Caller = { nativeCall(method: string, params: Record<string, unknown>): Promise<CommandResult> };

// Query the same local Git data as the CLI, through the Host's native command API.
export async function readReviewChoices(caller: Caller, cwd: string, kind: "baseBranch" | "commit") {
  const execute = (args: string[]) => caller.nativeCall("command/exec", {
    command: ["git", ...args], cwd, timeoutMs: 10000, disableOutputCap: true,
    env: { GIT_OPTIONAL_LOCKS: "0" },
  });
  const checked = async (args: string[]) => {
    const result = await execute(args);
    if (result.exitCode !== 0) throw new Error(result.stderr.trim() || "无法读取 Git 仓库");
    return result.stdout;
  };
  if (kind === "baseBranch") {
    const [branches, current] = await Promise.all([
      checked(["for-each-ref", "--format=%(refname:short)", "refs/heads/"]),
      checked(["branch", "--show-current"]),
    ]);
    return { currentBranch: current.trim() || "分离 HEAD", choices: branches.split("\n").filter(Boolean).map((branch): ReviewChoice => ({
      id: branch, label: branch, target: { type: "baseBranch", branch },
    })) };
  }
  const head = await execute(["rev-parse", "--verify", "--quiet", "HEAD"]);
  if (head.exitCode === 1) return { choices: [] as ReviewChoice[] };
  if (head.exitCode !== 0) throw new Error(head.stderr.trim() || "无法读取 Git 仓库");
  const log = await checked(["log", "-n", "100", "--format=%H%x00%s"]);
  return { choices: log.split("\n").filter(Boolean).map((line): ReviewChoice => {
    const separator = line.indexOf("\0");
    const sha = line.slice(0, separator), title = line.slice(separator + 1);
    if (separator < 0 || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(sha)) throw new Error("提交列表不完整，请重试");
    return { id: sha, label: title || sha.slice(0, 7), detail: sha.slice(0, 7), search: `${title} ${sha}`,
      target: { type: "commit", sha, title } };
  }) };
}
