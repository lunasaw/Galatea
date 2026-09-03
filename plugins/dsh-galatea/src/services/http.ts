import type { ErrorCategory } from '../contracts/index.ts'

export interface HttpRequestOptions {
  readonly method?: string
  readonly headers?: Readonly<Record<string, string>>
  readonly token?: string
  readonly body?: unknown
  readonly timeoutMs?: number
  readonly maxResponseBytes?: number
  readonly signal?: AbortSignal
}

export class HttpServiceError extends Error {
  readonly category: ErrorCategory
  readonly retryable: boolean
  readonly status?: number

  constructor(
    message: string,
    category: ErrorCategory,
    retryable: boolean,
    status?: number,
  ) {
    super(message)
    this.name = 'HttpServiceError'
    this.category = category
    this.retryable = retryable
    if (status !== undefined) this.status = status
  }
}

function fusedSignal(signal: AbortSignal | undefined, timeoutMs: number): {
  signal: AbortSignal
  timedOut(): boolean
  dispose(): void
} {
  const controller = new AbortController()
  let timeout = false
  const onAbort = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', onAbort, { once: true })
  if (signal?.aborted === true) controller.abort(signal.reason)
  const timer = setTimeout(() => {
    timeout = true
    controller.abort(new Error('request timeout'))
  }, timeoutMs)
  return {
    signal: controller.signal,
    timedOut: () => timeout,
    dispose: () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    },
  }
}

async function readLimited(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declared = response.headers.get('content-length')
  if (declared !== null && Number(declared) > maxBytes) {
    throw new HttpServiceError('remote response exceeded the configured size limit', 'integrity-error', false, response.status)
  }
  if (response.body === null) return new Uint8Array()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      size += chunk.value.byteLength
      if (size > maxBytes) {
        await reader.cancel()
        throw new HttpServiceError('remote response exceeded the configured size limit', 'integrity-error', false, response.status)
      }
      chunks.push(chunk.value)
    }
  } finally {
    reader.releaseLock()
  }
  const output = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) {
    output.set(chunk, offset)
    offset += chunk.byteLength
  }
  return output
}

async function requestBytesInternal(url: string, options: HttpRequestOptions): Promise<Uint8Array> {
  const timeoutMs = options.timeoutMs ?? 30_000
  const maxResponseBytes = options.maxResponseBytes ?? 1_000_000
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError('timeoutMs must be positive')
  if (!Number.isSafeInteger(maxResponseBytes) || maxResponseBytes <= 0) throw new TypeError('maxResponseBytes must be positive')
  const fused = fusedSignal(options.signal, timeoutMs)
  try {
    let response: Response
    try {
      response = await fetch(url, {
        method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
        headers: {
          accept: 'application/json',
          ...(options.body === undefined ? {} : { 'content-type': 'application/json' }),
          ...(options.token === undefined ? {} : { authorization: `Bearer ${options.token}` }),
          ...options.headers,
        },
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
        signal: fused.signal,
      })
    } catch (error: unknown) {
      if (fused.timedOut()) throw new HttpServiceError('remote request timed out', 'timeout', true)
      if (options.signal?.aborted === true) throw new HttpServiceError('remote request was cancelled', 'cancelled', false)
      throw new HttpServiceError('remote service is unavailable', 'platform-unavailable', true)
    }
    if (!response.ok) {
      const retryable = response.status === 408 || response.status === 429 || response.status >= 500
      const category: ErrorCategory = response.status === 404
        ? 'not-found'
        : response.status === 401 || response.status === 403
          ? 'permission-denied'
          : response.status === 409
            ? 'conflict'
            : retryable ? 'platform-unavailable' : 'remote-error'
      await readLimited(response, Math.min(maxResponseBytes, 16_384)).catch(() => new Uint8Array())
      throw new HttpServiceError(`remote service returned HTTP ${response.status}`, category, retryable, response.status)
    }
    return await readLimited(response, maxResponseBytes)
  } finally {
    fused.dispose()
  }
}

export async function requestBytes(url: string, options: HttpRequestOptions = {}): Promise<Uint8Array> {
  return requestBytesInternal(url, options)
}

export async function requestJson<T = unknown>(url: string, options: HttpRequestOptions = {}): Promise<T> {
  const bytes = await requestBytesInternal(url, options)
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as T
  } catch {
    throw new HttpServiceError('remote service returned invalid JSON', 'integrity-error', false)
  }
}
