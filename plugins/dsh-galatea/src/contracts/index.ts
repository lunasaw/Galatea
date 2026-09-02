import { createHash } from 'node:crypto'

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | readonly JsonValue[] | { readonly [key: string]: JsonValue }

export type ErrorCategory =
  | 'invalid-input'
  | 'unsupported'
  | 'not-found'
  | 'conflict'
  | 'permission-denied'
  | 'approval-required'
  | 'precondition-failed'
  | 'platform-unavailable'
  | 'timeout'
  | 'cancelled'
  | 'remote-error'
  | 'integrity-error'

export interface ToolError {
  readonly category: ErrorCategory
  readonly message: string
  readonly retryable: boolean
  readonly stateChanged: boolean
  readonly platformIds?: Readonly<Record<string, string>>
  readonly nextAction?: string
}

export type ToolResult<T extends JsonValue> =
  | {
    readonly ok: true
    readonly data: T
    readonly summary: string
    readonly evidence?: Readonly<Record<string, JsonValue>>
  }
  | { readonly ok: false; readonly error: ToolError }

function canonicalize(value: unknown, seen: Set<object>): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Object.is(value, -0)) throw new TypeError('canonical JSON requires finite numbers other than negative zero')
    return value
  }
  if (typeof value !== 'object') throw new TypeError(`canonical JSON does not support ${typeof value}`)
  if (seen.has(value)) throw new TypeError('canonical JSON does not support cyclic values')
  seen.add(value)
  try {
    if (Array.isArray(value)) return value.map(item => canonicalize(item, seen))
    const prototype = Object.getPrototypeOf(value)
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError('canonical JSON requires plain objects')
    }
    const output: Record<string, JsonValue> = {}
    for (const key of Object.keys(value).sort()) {
      output[key] = canonicalize((value as Record<string, unknown>)[key], seen)
    }
    return output
  } finally {
    seen.delete(value)
  }
}

/** Serialize lossless JSON with recursively sorted object keys. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value, new Set()))
}

/** Content identity used for evidence and idempotency subjects. */
export function evidenceDigest(value: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalJson(value)).digest('hex')}`
}

export function success<T extends JsonValue>(
  data: T,
  summary: string,
  evidence?: Readonly<Record<string, JsonValue>>,
): ToolResult<T> {
  return { ok: true, data, summary, ...(evidence === undefined ? {} : { evidence }) }
}

export function failure(error: ToolError): ToolResult<never> {
  return { ok: false, error }
}

const SECRET_KEY = /(?:^|[._-])(?:authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key)(?:$|[._-])/i
const SECRET_CONTAINER_KEY = /^(?:runtime_?env|env_?vars|environment)$/i

/** Return a detached JSON-compatible tree with known credential fields masked. */
export function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets)
  if (value === null || typeof value !== 'object') return value
  const output: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(value)) {
    output[key] = SECRET_KEY.test(key) || SECRET_CONTAINER_KEY.test(key)
      ? '[REDACTED]'
      : redactSecrets(child)
  }
  return output
}
