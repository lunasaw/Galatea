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

const GOVERNED_METADATA_KEYS = [
  'galatea.execution.identity',
  'galatea.project',
  'galatea.release.id',
  'galatea.submission.id',
  'galatea.readiness.digest',
  'galatea.execution.mode',
  'galatea.promotable',
] as const

type StopArg = string | AbortSignal | undefined

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
    this.validateGovernedMetadata(input.submissionId, input.idempotencyKey, input.metadata)
    const existing = await this.get(input.submissionId, input.signal)
    if (existing !== undefined) return this.reuse(existing, input.idempotencyKey, input.metadata)
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
      if (afterFailure !== undefined) return this.reuse(afterFailure, input.idempotencyKey, input.metadata)
      throw error
    }
  }

  private validateGovernedMetadata(
    submissionId: string,
    idempotencyKey: string,
    metadata: Readonly<Record<string, string>>,
  ): void {
    const hasGovernedKey = Object.keys(metadata).some(key => key.startsWith('galatea.'))
    if (!hasGovernedKey) return
    for (const key of GOVERNED_METADATA_KEYS) {
      if (metadata[key] === undefined || metadata[key]!.trim() === '') {
        throw new TypeError(`Ray governed metadata is missing ${key}`)
      }
    }
    if (metadata['galatea.execution.identity'] !== idempotencyKey) {
      throw new TypeError('Ray governed metadata execution identity must equal the idempotency key')
    }
    if (metadata['galatea.submission.id'] !== submissionId) {
      throw new TypeError('Ray governed metadata submission identity must equal submission_id')
    }
    if (metadata['galatea.execution.mode'] !== 'governed-ray-job' || metadata['galatea.promotable'] !== 'true') {
      throw new TypeError('Ray governed metadata must identify a promotable governed-ray-job')
    }
  }

  private reuse(
    existing: RayJobInfo,
    idempotencyKey: string,
    expectedMetadata: Readonly<Record<string, string>> = {},
  ): { submissionId: string; reused: true; status: RayJobStatus } {
    if (existing.metadata?.['idempotency_key'] !== idempotencyKey) {
      throw new HttpServiceError('Ray submission id already belongs to a different idempotency identity', 'conflict', false, 409)
    }
    for (const key of GOVERNED_METADATA_KEYS) {
      if (expectedMetadata[key] !== undefined && existing.metadata?.[key] !== expectedMetadata[key]) {
        throw new HttpServiceError(`Ray submission id has mismatched ${key}`, 'conflict', false, 409)
      }
    }
    return { submissionId: existing.submission_id, reused: true, status: existing.status }
  }

  async logs(
    submissionId: string,
    cursor = 0,
    signal?: AbortSignal,
  ): Promise<{ logs: string; truncated: boolean; cursor: number; nextCursor: number; reset: boolean }> {
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new TypeError('Ray log cursor must be a non-negative integer')
    const value = await requestJson<{ logs?: unknown }>(
      `${this.baseUrl}/api/jobs/${jobPath(submissionId)}/logs`,
      this.options(signal),
    )
    if (typeof value.logs !== 'string') throw new HttpServiceError('Ray logs response lacks logs text', 'integrity-error', false)
    const reset = cursor > value.logs.length
    const effectiveCursor = reset ? 0 : cursor
    const delta = value.logs.slice(effectiveCursor)
    const truncated = delta.length > this.maxLogChars
    return {
      logs: truncated ? delta.slice(-this.maxLogChars) : delta,
      truncated,
      cursor: effectiveCursor,
      nextCursor: value.logs.length,
      reset,
    }
  }

  /** Stop only after the caller's idempotency identity matches stored Job metadata. */
  async stop(
    submissionId: string,
    idempotencyKeyOrSignal?: StopArg,
    signal?: AbortSignal,
  ): Promise<{ stopped: boolean; previousStatus: RayJobStatus }> {
    const idempotencyKey = typeof idempotencyKeyOrSignal === 'string' ? idempotencyKeyOrSignal : undefined
    const requestSignal = idempotencyKeyOrSignal instanceof AbortSignal ? idempotencyKeyOrSignal : signal
    const existing = await this.get(submissionId, requestSignal)
    if (existing === undefined) throw new HttpServiceError('Ray job does not exist', 'not-found', false, 404)
    if (idempotencyKey !== undefined && existing.metadata?.['idempotency_key'] !== idempotencyKey) {
      throw new HttpServiceError('Ray stop idempotency identity does not own this submission', 'conflict', false, 409)
    }
    const value = await requestJson<{ stopped?: unknown }>(`${this.baseUrl}/api/jobs/${jobPath(submissionId)}/stop`, {
      ...this.options(requestSignal), method: 'POST', body: {},
    })
    if (typeof value.stopped !== 'boolean') throw new HttpServiceError('Ray stop response is malformed', 'integrity-error', false)
    return { stopped: value.stopped, previousStatus: existing.status }
  }
}
