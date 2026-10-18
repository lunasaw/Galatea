import { parseUnifiedDiff } from "./workbench-data";

// Run through the native command API so filesystem and sandbox enforcement stay
// with Codex. No shell interpolation, Git reset, checkout or conversation rollback.
export const undoPatchScript = String.raw`
import hashlib, json, os, pathlib, re, subprocess, sys

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

root = pathlib.Path.cwd().resolve()
patch = sys.argv[1]
blocks = re.split(r'(?=^diff --git )', patch, flags=re.M)
files = 0
for block in blocks:
    if not block.strip():
        continue
    header = re.search(r'^--- ([^\n]+)\n\+\+\+ ([^\n]+)', block, re.M)
    index = re.search(r'^index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: 100644)?$', block, re.M)
    if not header or not index:
        fail('缺少完整文件版本，无法安全撤销。')
    if re.search(r'^(?:rename |copy |GIT binary patch|Binary files)', block, re.M):
        fail('此变更暂不支持撤销。')
    if re.search(r'^(?:old mode|new mode|new file mode|deleted file mode) (?!100644$)', block, re.M):
        fail('此变更包含不支持撤销的文件类型。')
    paths = []
    for raw in header.groups():
        if raw.startswith('"'):
            # Decode Git C-style path quoting, including UTF-8 octal bytes.
            raw = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), raw[1:-1])
            raw = re.sub(r'\\([\\"tnr])', lambda m: {'t':'\t','n':'\n','r':'\r'}.get(m[1], m[1]), raw)
            raw = raw.encode('latin1').decode('utf-8')
        else:
            raw = raw.split('\t')[0]
        if raw == '/dev/null':
            paths.append(None)
            continue
        if raw[:2] not in ('a/', 'b/'):
            fail('无法识别文件路径。')
        relative = pathlib.PurePosixPath(raw[2:])
        if relative.is_absolute() or '..' in relative.parts or '.git' in relative.parts:
            fail('文件不在当前工作目录内，无法撤销。')
        path = root.joinpath(*relative.parts)
        if any(p.is_symlink() for p in (path, *path.parents)):
            fail('文件路径包含符号链接，无法撤销。')
        paths.append(path)
    git_header = block.splitlines()[0]
    if not git_header.startswith('diff --git '):
        fail('无法识别变更记录。')
    before, after = paths
    if before and after and before != after:
        fail('重命名变更暂不支持撤销。')
    path = after or before
    if path is None:
        fail('变更未包含文件路径。')
    if after is None:
        if path.exists():
            fail('文件已被重新创建，未撤销任何变更。')
    else:
        if not path.is_file():
            fail('文件已被移动或删除，未撤销任何变更。')
        content = path.read_bytes()
        oid = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        if oid != index[2]:
            fail('文件在本轮结束后又有修改，未撤销任何变更。')
    files += 1
if not files:
    fail('没有可撤销的文件变更。')
repository = subprocess.run(['git', 'rev-parse', '--show-toplevel'], text=True, capture_output=True)
command = ['git']
directory = []
if repository.returncode == 0:
    top = pathlib.Path(repository.stdout.strip()).resolve()
    prefix = root.relative_to(top).as_posix()
    command += ['-C', str(top)]
    if prefix != '.':
        directory = ['--directory=' + prefix]
command += ['apply', '--reverse', '--whitespace=nowarn', '-p1', *directory]
for args in (command + ['--check', '-'], command + ['-']):
    result = subprocess.run(args, input=patch, text=True, capture_output=True)
    if result.returncode:
        fail('文件变更无法完整撤销：' + result.stderr.strip())
print(json.dumps({'undone': True, 'files': files}))
`;

export function undoUnavailableReason(diff: string | undefined, cwd: string) {
  if (!cwd) return "没有当前工作目录";
  if (!diff?.startsWith("diff --git ")) return "此轮缺少完整变更记录，无法安全撤销";
  if (new TextEncoder().encode(diff).length > 96000) return "本轮变更较大，请在终端中检查并撤销";
  const files = parseUnifiedDiff(diff);
  if (!files.length) return "没有可撤销的文件变更";
  for (const file of files) {
    if (!/^index [0-9a-f]{40}\.\.[0-9a-f]{40}(?: 100644)?$/m.test(file.diff))
      return "此轮缺少完整文件版本，无法安全撤销";
    if (file.oldPath && file.oldPath !== "/dev/null" && file.path !== file.oldPath)
      return "重命名变更暂不支持撤销";
    if (/^(?:[a-z]:|\/)/i.test(file.path) || file.path.split("/").some((p) => p === ".." || p === ".git"))
      return "文件不在当前工作目录内，无法撤销";
  }
  return "";
}

export function undoPatchParams(cwd: string, diff: string) {
  return { command: ["python3", "-c", undoPatchScript, diff], cwd, timeoutMs: 10000, outputBytesCap: 16000 };
}
