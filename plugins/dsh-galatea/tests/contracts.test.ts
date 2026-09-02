import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  canonicalJson,
  evidenceDigest,
  failure,
  redactSecrets,
  success,
} from '../src/contracts/index.ts'

test('canonical JSON is stable across object insertion order and preserves arrays', () => {
  const left = { z: 1, nested: { b: true, a: 'x' }, list: [3, 1, 2] }
  const right = { list: [3, 1, 2], nested: { a: 'x', b: true }, z: 1 }

  assert.equal(canonicalJson(left), canonicalJson(right))
  assert.equal(canonicalJson(left), '{"list":[3,1,2],"nested":{"a":"x","b":true},"z":1}')
  assert.equal(evidenceDigest(left), evidenceDigest(right))
  assert.match(evidenceDigest(left), /^sha256:[a-f0-9]{64}$/)
})

test('canonical JSON rejects lossy or ambiguous values', () => {
  for (const value of [undefined, Number.NaN, Number.POSITIVE_INFINITY, -0, 1n, new Date()]) {
    assert.throws(() => canonicalJson(value), /canonical JSON/)
  }
  const cyclic: Record<string, unknown> = {}
  cyclic.self = cyclic
  assert.throws(() => canonicalJson(cyclic), /canonical JSON/)
})

test('structured results keep machine evidence separate from summaries', () => {
  assert.deepEqual(success({ runId: 'run-1' }, 'Run found.', { source: 'mlflow' }), {
    ok: true,
    data: { runId: 'run-1' },
    summary: 'Run found.',
    evidence: { source: 'mlflow' },
  })
  assert.deepEqual(failure({
    category: 'platform-unavailable',
    message: 'MLflow did not respond.',
    retryable: true,
    stateChanged: false,
    nextAction: 'Retry the read after checking MLflow health.',
  }), {
    ok: false,
    error: {
      category: 'platform-unavailable',
      message: 'MLflow did not respond.',
      retryable: true,
      stateChanged: false,
      nextAction: 'Retry the read after checking MLflow health.',
    },
  })
})

test('secret redaction removes credentials recursively without changing IDs', () => {
  assert.deepEqual(redactSecrets({
    runId: 'run-1',
    token: 'secret',
    runtimeEnv: { env_vars: { SAFE_NAME: 'still-sensitive-as-a-container' } },
    nested: {
      password: 'secret',
      authorization: 'Bearer hidden',
      AWS_ACCESS_KEY_ID: 'hidden',
      database_password_value: 'hidden',
      uri: 's3://bucket/object',
    },
  }), {
    runId: 'run-1',
    token: '[REDACTED]',
    runtimeEnv: '[REDACTED]',
    nested: {
      password: '[REDACTED]',
      authorization: '[REDACTED]',
      AWS_ACCESS_KEY_ID: '[REDACTED]',
      database_password_value: '[REDACTED]',
      uri: 's3://bucket/object',
    },
  })
})
