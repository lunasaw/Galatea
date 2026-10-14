import { spawn } from 'node:child_process'
import { realpath } from 'node:fs/promises'

export interface ProjectProcessConfig {
  readonly timeoutMs?: number
  readonly maxOutputBytes?: number
  readonly inheritedEnv?: readonly string[]
}

export interface ProjectProcessResult {
  readonly exitCode: number | null
  readonly signal: NodeJS.Signals | null
  readonly stdout: string
  readonly stderr: string
}

export class ProjectProcessService {
  readonly timeoutMs: number
  readonly maxOutputBytes: number
  readonly inheritedEnv: readonly string[]

  constructor(config: ProjectProcessConfig = {}) {
    this.timeoutMs = config.timeoutMs ?? 60_000
    this.maxOutputBytes = config.maxOutputBytes ?? 1_000_000
    this.inheritedEnv = config.inheritedEnv ?? [
      'PATH', 'HOME', 'LANG', 'LC_ALL', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
      'MLFLOW_TRACKING_URI', 'RAY_ADDRESS',
    ]
    for (const name of this.inheritedEnv) {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) throw new TypeError('inherited environment names must be valid')
    }
  }

  async run(input: {
    readonly projectRoot: string
    readonly argv: readonly string[]
    readonly env?: Readonly<Record<string, string>>
    readonly signal?: AbortSignal
  }): Promise<ProjectProcessResult> {
    if (input.argv.length === 0 || input.argv.some(argument => typeof argument !== 'string' || argument.length === 0)) {
      throw new TypeError('project entrypoint must be a non-empty argv array')
    }
    const cwd = await realpath(input.projectRoot)
    const inherited = Object.fromEntries(this.inheritedEnv.flatMap(name => {
      const value = process.env[name]
      return value === undefined ? [] : [[name, value]]
    }))
    return await new Promise<ProjectProcessResult>((resolve, reject) => {
      const child = spawn(input.argv[0]!, input.argv.slice(1), {
        cwd,
        shell: false,
        detached: process.platform !== 'win32',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...inherited, ...input.env },
      })
      const stdout: Buffer[] = []
      const stderr: Buffer[] = []
      let total = 0
      let settled = false
      const settleError = (error: Error) => {
        if (settled) return
        settled = true
        cleanup()
        if (child.pid !== undefined && process.platform !== 'win32') {
          try {
            process.kill(-child.pid, 'SIGKILL')
          } catch (killError: unknown) {
            if ((killError as NodeJS.ErrnoException).code !== 'ESRCH') child.kill('SIGKILL')
          }
        } else {
          child.kill('SIGKILL')
        }
        reject(error)
      }
      const collect = (target: Buffer[]) => (chunk: Buffer) => {
        total += chunk.byteLength
        if (total > this.maxOutputBytes) return settleError(new Error('project entrypoint output exceeded configured limit'))
        target.push(Buffer.from(chunk))
      }
      child.stdout.on('data', collect(stdout))
      child.stderr.on('data', collect(stderr))
      const timer = setTimeout(() => settleError(new Error('project entrypoint timed out')), this.timeoutMs)
      const onAbort = () => settleError(new Error('project entrypoint was cancelled'))
      input.signal?.addEventListener('abort', onAbort, { once: true })
      const cleanup = () => {
        clearTimeout(timer)
        input.signal?.removeEventListener('abort', onAbort)
      }
      child.once('error', settleError)
      child.once('close', (exitCode, signal) => {
        if (settled) return
        settled = true
        cleanup()
        resolve({
          exitCode,
          signal,
          stdout: Buffer.concat(stdout).toString('utf8'),
          stderr: Buffer.concat(stderr).toString('utf8'),
        })
      })
    })
  }
}
