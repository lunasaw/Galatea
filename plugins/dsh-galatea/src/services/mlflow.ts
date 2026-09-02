import { createHash } from 'node:crypto'
import { HttpServiceError, requestBytes, requestJson } from './http.ts'

export interface MlflowConfig {
  readonly baseUrl: string
  readonly token?: string
  readonly timeoutMs?: number
  readonly maxResponseBytes?: number
  readonly maxArtifactBytes?: number
  readonly maxPages?: number
  readonly pageSize?: number
}

export interface MlflowRun {
  readonly info: { readonly run_id: string; readonly experiment_id?: string; readonly status?: string; readonly [key: string]: unknown }
  readonly data?: {
    readonly metrics?: readonly { readonly key: string; readonly value: number; readonly step?: number }[]
    readonly params?: readonly { readonly key: string; readonly value: string }[]
    readonly tags?: readonly { readonly key: string; readonly value: string }[]
  }
}

export interface MlflowExperiment {
  readonly experiment_id: string
  readonly name: string
  readonly lifecycle_stage?: string
  readonly [key: string]: unknown
}

export interface ArtifactFile {
  readonly path: string
  readonly file_size?: number
  readonly is_dir?: boolean
}

export interface MlflowModelVersion {
  readonly name: string
  readonly version: string
  readonly run_id?: string
  readonly source?: string
  readonly description?: string
}

export class MlflowService {
  readonly config: MlflowConfig
  readonly baseUrl: string
  readonly timeoutMs: number
  readonly maxResponseBytes: number
  readonly maxArtifactBytes: number
  readonly maxPages: number
  readonly pageSize: number

  constructor(config: MlflowConfig) {
    this.config = config
    this.baseUrl = config.baseUrl.replace(/\/+$/, '')
    this.timeoutMs = config.timeoutMs ?? 30_000
    this.maxResponseBytes = config.maxResponseBytes ?? 2_000_000
    this.maxArtifactBytes = config.maxArtifactBytes ?? 50_000_000
    this.maxPages = config.maxPages ?? 100
    this.pageSize = config.pageSize ?? 1_000
  }

  private options(signal?: AbortSignal) {
    return {
      ...(this.config.token === undefined ? {} : { token: this.config.token }),
      timeoutMs: this.timeoutMs,
      maxResponseBytes: this.maxResponseBytes,
      ...(signal === undefined ? {} : { signal }),
    }
  }

  async getExperimentByName(name: string, signal?: AbortSignal): Promise<MlflowExperiment | undefined> {
    const query = new URLSearchParams({ experiment_name: name })
    try {
      const response = await requestJson<{ experiment?: unknown }>(
        `${this.baseUrl}/api/2.0/mlflow/experiments/get-by-name?${query}`,
        this.options(signal),
      )
      if (response.experiment === undefined || response.experiment === null || typeof response.experiment !== 'object') {
        throw new HttpServiceError('MLflow Experiment response is malformed', 'integrity-error', false)
      }
      const experiment = response.experiment as Record<string, unknown>
      if (typeof experiment['experiment_id'] !== 'string' || typeof experiment['name'] !== 'string') {
        throw new HttpServiceError('MLflow Experiment identity is malformed', 'integrity-error', false)
      }
      return experiment as MlflowExperiment
    } catch (error: unknown) {
      if (error instanceof HttpServiceError && error.status === 404) return undefined
      throw error
    }
  }

  async searchRuns(input: {
    readonly experimentIds: readonly string[]
    readonly filter?: string
    readonly orderBy?: readonly string[]
    readonly signal?: AbortSignal
  }): Promise<readonly MlflowRun[]> {
    const runs: MlflowRun[] = []
    let pageToken: string | undefined
    for (let page = 0; page < this.maxPages; page += 1) {
      const response = await requestJson<{ runs?: unknown; next_page_token?: unknown }>(
        `${this.baseUrl}/api/2.0/mlflow/runs/search`, {
          ...this.options(input.signal),
          method: 'POST',
          body: {
            experiment_ids: input.experimentIds,
            max_results: this.pageSize,
            ...(input.filter === undefined ? {} : { filter: input.filter }),
            ...(input.orderBy === undefined ? {} : { order_by: input.orderBy }),
            ...(pageToken === undefined ? {} : { page_token: pageToken }),
          },
        },
      )
      if (response.runs !== undefined && !Array.isArray(response.runs)) {
        throw new HttpServiceError('MLflow Runs response is malformed', 'integrity-error', false)
      }
      runs.push(...(response.runs as MlflowRun[] | undefined ?? []))
      if (response.next_page_token === undefined || response.next_page_token === '') return runs
      if (typeof response.next_page_token !== 'string') throw new HttpServiceError('MLflow page token is malformed', 'integrity-error', false)
      pageToken = response.next_page_token
    }
    throw new HttpServiceError('MLflow pagination exceeded the configured page limit', 'integrity-error', false)
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<MlflowRun> {
    const query = new URLSearchParams({ run_id: runId })
    const response = await requestJson<{ run?: unknown }>(
      `${this.baseUrl}/api/2.0/mlflow/runs/get?${query}`, this.options(signal),
    )
    if (response.run === undefined || response.run === null || typeof response.run !== 'object') {
      throw new HttpServiceError('MLflow Run response is malformed', 'integrity-error', false)
    }
    return response.run as MlflowRun
  }

  async metricHistory(runId: string, key: string, signal?: AbortSignal): Promise<readonly { key: string; value: number; step?: number }[]> {
    const query = new URLSearchParams({ run_id: runId, metric_key: key })
    const response = await requestJson<{ metrics?: unknown }>(
      `${this.baseUrl}/api/2.0/mlflow/metrics/get-history?${query}`, this.options(signal),
    )
    if (!Array.isArray(response.metrics)) throw new HttpServiceError('MLflow metric history is malformed', 'integrity-error', false)
    return response.metrics as { key: string; value: number; step?: number }[]
  }

  async listArtifacts(runId: string, path = '', signal?: AbortSignal): Promise<readonly ArtifactFile[]> {
    const files: ArtifactFile[] = []
    let pageToken: string | undefined
    for (let page = 0; page < this.maxPages; page += 1) {
      const query = new URLSearchParams({ run_id: runId, path })
      if (pageToken !== undefined) query.set('page_token', pageToken)
      const response = await requestJson<{ files?: unknown; next_page_token?: unknown }>(
        `${this.baseUrl}/api/2.0/mlflow/artifacts/list?${query}`, this.options(signal),
      )
      if (response.files !== undefined && !Array.isArray(response.files)) {
        throw new HttpServiceError('MLflow Artifact listing is malformed', 'integrity-error', false)
      }
      files.push(...(response.files as ArtifactFile[] | undefined ?? []))
      if (response.next_page_token === undefined || response.next_page_token === '') return files
      if (typeof response.next_page_token !== 'string') throw new HttpServiceError('MLflow Artifact page token is malformed', 'integrity-error', false)
      pageToken = response.next_page_token
    }
    throw new HttpServiceError('MLflow Artifact pagination exceeded the configured page limit', 'integrity-error', false)
  }

  async verifyArtifact(input: {
    readonly runId: string
    readonly path: string
    readonly expectedDigest?: string
    readonly signal?: AbortSignal
  }): Promise<{ runId: string; path: string; size: number; digest: string; verified: boolean }> {
    if (input.path.split('/').includes('..') || input.path.startsWith('/')) throw new TypeError('Artifact path must be relative')
    const encodedPath = input.path.split('/').map(encodeURIComponent).join('/')
    const query = new URLSearchParams({ run_id: input.runId })
    const bytes = await requestBytes(
      `${this.baseUrl}/api/2.0/mlflow-artifacts/artifacts/${encodedPath}?${query}`,
      { ...this.options(input.signal), maxResponseBytes: this.maxArtifactBytes },
    )
    const digest = `sha256:${createHash('sha256').update(bytes).digest('hex')}`
    const verified = input.expectedDigest === undefined || input.expectedDigest === digest
    if (!verified) throw new HttpServiceError('MLflow Artifact digest does not match expected evidence', 'integrity-error', false)
    return { runId: input.runId, path: input.path, size: bytes.byteLength, digest, verified }
  }


  async createRegisteredModel(name: string, signal?: AbortSignal): Promise<{ name: string }> {
    const response = await requestJson<{ registered_model?: unknown }>(
      `${this.baseUrl}/api/2.0/mlflow/registered-models/create`, {
        ...this.options(signal), method: 'POST', body: { name },
      },
    )
    const model = response.registered_model
    if (model === null || typeof model !== 'object' || typeof (model as Record<string, unknown>)['name'] !== 'string') {
      throw new HttpServiceError('MLflow Registered Model response is malformed', 'integrity-error', false)
    }
    return { name: (model as Record<string, string>)['name']! }
  }

  async getRegisteredModel(name: string, signal?: AbortSignal): Promise<{ name: string } | undefined> {
    const query = new URLSearchParams({ name })
    try {
      const response = await requestJson<{ registered_model?: unknown }>(
        `${this.baseUrl}/api/2.0/mlflow/registered-models/get?${query}`, this.options(signal),
      )
      const model = response.registered_model
      if (model === null || typeof model !== 'object' || typeof (model as Record<string, unknown>)['name'] !== 'string') {
        throw new HttpServiceError('MLflow Registered Model response is malformed', 'integrity-error', false)
      }
      return { name: (model as Record<string, string>)['name']! }
    } catch (error: unknown) {
      if (error instanceof HttpServiceError && error.status === 404) return undefined
      throw error
    }
  }

  async searchModelVersions(name: string, signal?: AbortSignal): Promise<readonly MlflowModelVersion[]> {
    const versions: MlflowModelVersion[] = []
    let pageToken: string | undefined
    for (let page = 0; page < this.maxPages; page += 1) {
      const query = new URLSearchParams({
        filter: `name = '${name.replaceAll("'", "\\'")}'`,
        max_results: String(this.pageSize),
      })
      if (pageToken !== undefined) query.set('page_token', pageToken)
      const response = await requestJson<{ model_versions?: unknown; next_page_token?: unknown }>(
        `${this.baseUrl}/api/2.0/mlflow/model-versions/search?${query}`, this.options(signal),
      )
      if (response.model_versions !== undefined && !Array.isArray(response.model_versions)) {
        throw new HttpServiceError('MLflow Model Versions response is malformed', 'integrity-error', false)
      }
      versions.push(...(response.model_versions as MlflowModelVersion[] | undefined ?? []))
      if (response.next_page_token === undefined || response.next_page_token === '') return versions
      if (typeof response.next_page_token !== 'string') {
        throw new HttpServiceError('MLflow Model Versions page token is malformed', 'integrity-error', false)
      }
      pageToken = response.next_page_token
    }
    throw new HttpServiceError('MLflow Model Versions pagination exceeded the configured page limit', 'integrity-error', false)
  }

  async createModelVersion(input: {
    readonly name: string
    readonly source: string
    readonly runId: string
    readonly description?: string
    readonly signal?: AbortSignal
  }): Promise<MlflowModelVersion> {
    const response = await requestJson<{ model_version?: unknown }>(
      `${this.baseUrl}/api/2.0/mlflow/model-versions/create`, {
        ...this.options(input.signal),
        method: 'POST',
        body: {
          name: input.name,
          source: input.source,
          run_id: input.runId,
          ...(input.description === undefined ? {} : { description: input.description }),
        },
      },
    )
    const version = response.model_version
    if (version === null || typeof version !== 'object') {
      throw new HttpServiceError('MLflow Model Version response is malformed', 'integrity-error', false)
    }
    const record = version as Record<string, unknown>
    if (typeof record['name'] !== 'string' || typeof record['version'] !== 'string') {
      throw new HttpServiceError('MLflow Model Version identity is malformed', 'integrity-error', false)
    }
    return {
      name: record['name'],
      version: record['version'],
      ...(typeof record['run_id'] === 'string' ? { run_id: record['run_id'] } : {}),
      ...(typeof record['source'] === 'string' ? { source: record['source'] } : {}),
    }
  }

  async setRegisteredModelAlias(input: {
    readonly name: string
    readonly alias: string
    readonly version: string
    readonly signal?: AbortSignal
  }): Promise<{ name: string; alias: string; version: string }> {
    await requestJson(`${this.baseUrl}/api/2.0/mlflow/registered-models/alias`, {
      ...this.options(input.signal), method: 'POST',
      body: { name: input.name, alias: input.alias, version: input.version },
    })
    return { name: input.name, alias: input.alias, version: input.version }
  }
}
