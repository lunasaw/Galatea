import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
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

interface RawHttpResponse {
  readonly status: number
  readonly body: Uint8Array
}

function requestRaw(
  url: string,
  options: HttpRequestOptions,
  signal: AbortSignal,
  maxBytes: number,
): Promise<RawHttpResponse> {
  return new Promise((resolve, reject) => {
    let target: URL
    try {
      target = new URL(url)
    } catch {
      reject(new TypeError('remote URL must be valid'))
      return
    }
    const requestFunction = target.protocol === 'https:' ? httpsRequest : target.protocol === 'http:' ? httpRequest : undefined
    if (requestFunction === undefined) {
      reject(new TypeError('remote URL must use HTTP or HTTPS'))
      return
    }
    if (signal.aborted) {
      reject(new Error('remote request was cancelled'))
      return
    }
    const body = options.body === undefined ? undefined : JSON.stringify(options.body)
    const headers: Record<string, string> = {
      accept: 'application/json',
      'accept-encoding': 'identity',
      // 使用 Node HTTP API，避免 Node fetch 自动添加 sec-fetch-mode 浏览器头。
      ...(options.body === undefined ? {} : { 'content-type': 'application/json' }),
      ...(options.token === undefined ? {} : { authorization: `Bearer ${options.token}` }),
      ...options.headers,
      // Node HTTP API 不会自动注入 Ray Dashboard 识别的浏览器请求头。
      'user-agent': 'dsh-galatea',
    }
    const request = requestFunction(target, {
      method: options.method ?? (body === undefined ? 'GET' : 'POST'),
      headers,
    })
    let settled = false
    const finish = (callback: () => void) => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      callback()
    }
    const abort = () => request.destroy(new Error('remote request was cancelled'))
    signal.addEventListener('abort', abort, { once: true })
    request.on('response', response => {
      const chunks: Buffer[] = []
      let size = 0
      response.on('data', chunk => {
        const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
        size += bytes.length
        if (size > maxBytes) {
          response.destroy()
          finish(() => reject(new HttpServiceError('remote response exceeded the configured size limit', 'integrity-error', false, response.statusCode)))
          return
        }
        chunks.push(bytes)
      })
      response.on('end', () => finish(() => resolve({
        status: response.statusCode ?? 0,
        body: Buffer.concat(chunks),
      })))
      response.on('error', error => finish(() => reject(error)))
    })
    request.on('error', error => finish(() => reject(error)))
    if (body === undefined) request.end()
    else request.end(body)
  })
}

async function requestBytesInternal(url: string, options: HttpRequestOptions): Promise<Uint8Array> {
  const timeoutMs = options.timeoutMs ?? 30_000
  const maxResponseBytes = options.maxResponseBytes ?? 1_000_000
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError('timeoutMs must be positive')
  if (!Number.isSafeInteger(maxResponseBytes) || maxResponseBytes <= 0) throw new TypeError('maxResponseBytes must be positive')
  const fused = fusedSignal(options.signal, timeoutMs)
  try {
    let response: RawHttpResponse
    try {
      response = await requestRaw(url, options, fused.signal, maxResponseBytes)
    } catch (error: unknown) {
      if (error instanceof HttpServiceError) throw error
      if (fused.timedOut()) throw new HttpServiceError('remote request timed out', 'timeout', true)
      if (options.signal?.aborted === true) throw new HttpServiceError('remote request was cancelled', 'cancelled', false)
      throw new HttpServiceError('remote service is unavailable', 'platform-unavailable', true)
    }
    if (response.status < 200 || response.status >= 300) {
      const retryable = response.status === 408 || response.status === 429 || response.status >= 500
      const category: ErrorCategory = response.status === 404
        ? 'not-found'
        : response.status === 401 || response.status === 403
          ? 'permission-denied'
          : response.status === 409
            ? 'conflict'
            : retryable ? 'platform-unavailable' : 'remote-error'
      throw new HttpServiceError(`remote service returned HTTP ${response.status}`, category, retryable, response.status)
    }
    return response.body
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
