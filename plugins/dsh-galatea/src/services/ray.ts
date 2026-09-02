import { createHash } from 'node:crypto'
import { HttpServiceError, requestJson } from './http.ts'

export type RayJobStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'STOPPED'

export interface RayJobInfo {
  readonly submission_id: string
  readonly status: RayJobStatus
  readonly metadata?: Readonly<Record<string, string>>
  readonly message?: string
  readonly error_type?: string
}

export interface RayJobsConfig {
  readonly baseUrl: string
  readonly token?: string
  readonly timeoutMs?: number
  readonly maxResponseBytes?: number
  readonly maxLogChars?: number
}

function cleanBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}

function jobPath(id: string): string {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new TypeError('Ray submission id contains unsupported characters')
  return encodeURIComponent(id)
}

/** Produce a readable stable Ray submission id from a training identity. */
export function deterministicSubmissionId(project: string, role: string, identity: string): string {
  const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40)
  const rawDigest = identity.startsWith('sha256:') ? identity.slice(7) : createHash('sha256').update(identity).digest('hex')
  return `${slug(project)}-${slug(role)}-${rawDigest.slice(0, 12)}`
}

export class RayJobsService {
  readonly config: RayJobsConfig
  readonly baseUrl: string
  readonly timeoutMs: number
  readonly maxResponseBytes: number
  readonly maxLogChars: number

  constructor(config: RayJobsConfig) {
    this.config = config
    this.baseUrl = cleanBaseUrl(config.baseUrl)
    this.timeoutMs = config.timeoutMs ?? 30_000
    this.maxResponseBytes = config.maxResponseBytes ?? 1_000_000
    this.maxLogChars = config.maxLogChars ?? 100_000
  }

  private options(signal?: AbortSignal) {
    return {
      ...(this.config.token === undefined ? {} : { token: this.config.token }),
      timeoutMs: this.timeoutMs,
      maxResponseBytes: this.maxResponseBytes,
      ...(signal === undefined ? {} : { signal }),
    }
  }

  async version(signal?: AbortSignal): Promise<{ version: string; ray_version: string }> {
    return requestJson(`${this.baseUrl}/api/version`, this.options(signal))
  }

  async get(submissionId: string, signal?: AbortSignal): Promise<RayJobInfo | undefined> {
    try {
      return await requestJson(`${this.baseUrl}/api/jobs/${jobPath(submissionId)}`, this.options(signal))
    } catch (error: unknown) {
      if (error instanceof HttpServiceError && error.status === 404) return undefined
      throw error
    }
  }

  async list(signal?: AbortSignal): Promise<readonly RayJobInfo[]> {
    const value = await requestJson<unknown>(`${this.baseUrl}/api/jobs/`, this.options(signal))
    if (!Array.isArray(value)) throw new HttpServiceError('Ray jobs response is not an array', 'integrity-error', false)
    return value as RayJobInfo[]
  }

  async submit(input: {
    readonly submissionId: string
    readonly idempotencyKey: string
    readonly entrypoint: string
    readonly runtimeEnv: Readonly<Record<string, unknown>>
    readonly metadata: Readonly<Record<string, string>>
    readonly entrypointNumCpus?: number
    readonly entrypointNumGpus?: number
    readonly signal?: AbortSignal
  }): Promise<{ submissionId: string; reused: boolean; status?: RayJobStatus }> {
    const existing = await this.get(input.submissionId, input.signal)
    if (existing !== undefined) return this.reuse(existing, input.idempotencyKey)
    try {
      const created = await requestJson<{ submission_id?: string }>(`${this.baseUrl}/api/jobs/`, {
        ...this.options(input.signal),
        method: 'POST',
        body: {
          entrypoint: input.entrypoint,
          submission_id: input.submissionId,
          runtime_env: input.runtimeEnv,
          metadata: { ...input.metadata, idempotency_key: input.idempotencyKey },
          ...(input.entrypointNumCpus === undefined ? {} : { entrypoint_num_cpus: input.entrypointNumCpus }),
          ...(input.entrypointNumGpus === undefined ? {} : { entrypoint_num_gpus: input.entrypointNumGpus }),
        },
      })
      return { submissionId: created.submission_id ?? input.submissionId, reused: false }
    } catch (error: unknown) {
      if (!(error instanceof HttpServiceError) || !error.retryable) throw error
      const afterFailure = await this.get(input.submissionId, input.signal)
      if (afterFailure !== undefined) return this.reuse(afterFailure, input.idempotencyKey)
      throw error
    }
  }

  private reuse(existing: RayJobInfo, idempotencyKey: string): { submissionId: string; reused: true; status: RayJobStatus } {
    if (existing.metadata?.['idempotency_key'] !== idempotencyKey) {
      throw new HttpServiceError('Ray submission id already belongs to a different idempotency identity', 'conflict', false, 409)
    }
    return { submissionId: existing.submission_id, reused: true, status: existing.status }
  }

  async logs(submissionId: string, signal?: AbortSignal): Promise<{ logs: string; truncated: boolean }> {
    const value = await requestJson<{ logs?: unknown }>(
      `${this.baseUrl}/api/jobs/${jobPath(submissionId)}/logs`,
      this.options(signal),
    )
    if (typeof value.logs !== 'string') throw new HttpServiceError('Ray logs response lacks logs text', 'integrity-error', false)
    const truncated = value.logs.length > this.maxLogChars
    return { logs: truncated ? value.logs.slice(-this.maxLogChars) : value.logs, truncated }
  }

  async stop(submissionId: string, signal?: AbortSignal): Promise<{ stopped: boolean; previousStatus: RayJobStatus }> {
    const existing = await this.get(submissionId, signal)
    if (existing === undefined) throw new HttpServiceError('Ray job does not exist', 'not-found', false, 404)
    const value = await requestJson<{ stopped?: unknown }>(`${this.baseUrl}/api/jobs/${jobPath(submissionId)}/stop`, {
      ...this.options(signal), method: 'POST', body: {},
    })
    if (typeof value.stopped !== 'boolean') throw new HttpServiceError('Ray stop response is malformed', 'integrity-error', false)
    return { stopped: value.stopped, previousStatus: existing.status }
  }
}
