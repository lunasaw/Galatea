import assert from 'node:assert/strict'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { HttpServiceError, requestJson } from '../src/services/http.ts'
import { MlflowService } from '../src/services/mlflow.ts'
import { deterministicSubmissionId, RayJobsService } from '../src/services/ray.ts'
import { ProjectProcessService } from '../src/services/project-process.ts'

interface RecordedRequest {
  method: string
  path: string
  body: unknown
  userAgent?: string
  secFetchMode?: string
  authorization?: string
}

async function mockServer(
  handler: (request: RecordedRequest, response: ServerResponse) => void,
): Promise<{ baseUrl: string; requests: RecordedRequest[]; close(): Promise<void> }> {
  const requests: RecordedRequest[] = []
  const server = createServer(async (request: IncomingMessage, response) => {
    const chunks: Buffer[] = []
    for await (const chunk of request) chunks.push(Buffer.from(chunk))
    const text = Buffer.concat(chunks).toString('utf8')
    const recorded: RecordedRequest = {
      method: request.method ?? 'GET',
      path: request.url ?? '/',
      body: text === '' ? undefined : JSON.parse(text),
      ...(request.headers['user-agent'] === undefined ? {} : { userAgent: request.headers['user-agent'] }),
      ...(request.headers['sec-fetch-mode'] === undefined ? {} : { secFetchMode: request.headers['sec-fetch-mode'] }),
      ...(request.headers.authorization === undefined ? {} : { authorization: request.headers.authorization }),
    }
    requests.push(recorded)
    handler(recorded, response)
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('mock server did not bind TCP')
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    requests,
    close: () => new Promise((resolve, reject) => server.close(error => error === undefined ? resolve() : reject(error))),
  }
}

function json(response: ServerResponse, value: unknown, status = 200): void {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

test('HTTP service uses a non-browser User-Agent for Ray Dashboard POST protection', async () => {
  const server = await mockServer((_request, response) => json(response, { ok: true }))
  try {
    await requestJson(`${server.baseUrl}/headers`, { method: 'POST', body: { probe: true } })
    assert.equal(server.requests[0]?.userAgent, 'dsh-galatea')
    assert.equal(server.requests[0]?.secFetchMode, undefined)
  } finally {
    await server.close()
  }
})

test('HTTP service enforces timeout, response limits, auth, and structured errors', async () => {
  const server = await mockServer((request, response) => {
    if (request.path === '/slow') return void setTimeout(() => json(response, { ok: true }), 100)
    if (request.path === '/large') return void json(response, { value: 'x'.repeat(2_000) })
    if (request.path === '/error') return void json(response, { message: 'private remote detail' }, 503)
    json(response, { authorization: request.authorization })
  })
  try {
    assert.deepEqual(await requestJson(`${server.baseUrl}/auth`, { token: 'ray-token' }), {
      authorization: 'Bearer ray-token',
    })
    await assert.rejects(requestJson(`${server.baseUrl}/slow`, { timeoutMs: 10 }), (error: unknown) =>
      error instanceof HttpServiceError && error.category === 'timeout' && error.retryable)
    await assert.rejects(requestJson(`${server.baseUrl}/large`, { maxResponseBytes: 100 }), (error: unknown) =>
      error instanceof HttpServiceError && error.category === 'integrity-error')
    await assert.rejects(requestJson(`${server.baseUrl}/error`), (error: unknown) =>
      error instanceof HttpServiceError && error.status === 503 && !error.message.includes('private remote detail'))
  } finally {
    await server.close()
  }
})

test('Ray service reuses an existing deterministic submission and caps logs', async () => {
  const server = await mockServer((request, response) => {
    if (request.path === '/api/jobs/train-abc') return void json(response, {
      submission_id: 'train-abc', status: 'RUNNING', metadata: { idempotency_key: 'sha256:abc' },
    })
    if (request.path === '/api/jobs/train-abc/logs') return void json(response, { logs: '0123456789' })
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const ray = new RayJobsService({ baseUrl: server.baseUrl, timeoutMs: 1_000, maxLogChars: 5 })
    const submitted = await ray.submit({
      submissionId: 'train-abc',
      idempotencyKey: 'sha256:abc',
      entrypoint: 'python scripts/train.py --config configs/baseline.yaml',
      runtimeEnv: {},
      metadata: { project: 'demo' },
    })
    assert.deepEqual(submitted, { submissionId: 'train-abc', reused: true, status: 'RUNNING' })
    assert.deepEqual(await ray.logs('train-abc'), {
      logs: '56789', truncated: true, cursor: 0, nextCursor: 10, reset: false,
    })
    assert.deepEqual(await ray.logs('train-abc', 8), {
      logs: '89', truncated: false, cursor: 8, nextCursor: 10, reset: false,
    })
    assert.equal(server.requests.some(request => request.method === 'POST'), false)
    assert.equal(deterministicSubmissionId('demo', 'train', 'sha256:abcdef'), 'demo-train-abcdef')
  } finally {
    await server.close()
  }
})

test('Ray submit rejects an identity conflict and stops only the requested job', async () => {
  const server = await mockServer((request, response) => {
    if (request.path === '/api/jobs/train-conflict') return void json(response, {
      submission_id: 'train-conflict', status: 'FAILED', metadata: { idempotency_key: 'sha256:other' },
    })
    if (request.path === '/api/jobs/train-stop') return void json(response, {
      submission_id: 'train-stop', status: 'RUNNING', metadata: {},
    })
    if (request.path === '/api/jobs/train-stop/stop') return void json(response, { stopped: true })
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const ray = new RayJobsService({ baseUrl: server.baseUrl })
    await assert.rejects(ray.submit({
      submissionId: 'train-conflict',
      idempotencyKey: 'sha256:current',
      entrypoint: 'fixed',
      runtimeEnv: {},
      metadata: {},
    }), /different idempotency identity/)
    assert.deepEqual(await ray.stop('train-stop'), { stopped: true, previousStatus: 'RUNNING' })
  } finally {
    await server.close()
  }
})

test('Ray submit reconciles a timed-out POST against the Jobs fact source', async () => {
  let submitted = false
  const server = await mockServer((request, response) => {
    if (request.path === '/api/jobs/train-timeout' && request.method === 'GET') {
      return submitted
        ? void json(response, { submission_id: 'train-timeout', status: 'PENDING', metadata: { idempotency_key: 'sha256:timeout' } })
        : void json(response, { message: 'not found' }, 404)
    }
    if (request.path === '/api/jobs/' && request.method === 'POST') {
      submitted = true
      return void setTimeout(() => json(response, { submission_id: 'train-timeout' }), 100)
    }
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const ray = new RayJobsService({ baseUrl: server.baseUrl, timeoutMs: 10 })
    assert.deepEqual(await ray.submit({
      submissionId: 'train-timeout',
      idempotencyKey: 'sha256:timeout',
      entrypoint: 'fixed',
      runtimeEnv: {},
      metadata: {},
    }), { submissionId: 'train-timeout', reused: true, status: 'PENDING' })
    assert.equal(server.requests.filter(request => request.method === 'POST').length, 1)
  } finally {
    await server.close()
  }
})

test('MLflow service paginates Runs, metric history, and Artifact listings', async () => {
  const server = await mockServer((request, response) => {
    if (request.path === '/api/2.0/mlflow/experiments/get-by-name?experiment_name=demo-project') {
      return void json(response, { experiment: { experiment_id: '1', name: 'demo-project', lifecycle_stage: 'active' } })
    }
    if (request.path === '/api/2.0/mlflow/runs/search') {
      const body = request.body as { page_token?: string }
      return void json(response, body.page_token === undefined
        ? { runs: [{ info: { run_id: 'a' } }], next_page_token: 'next' }
        : { runs: [{ info: { run_id: 'b' } }] })
    }
    if (request.path.startsWith('/api/2.0/mlflow/metrics/get-history')) {
      return void json(response, { metrics: [{ key: 'val_accuracy', value: 0.9, step: 1 }] })
    }
    if (request.path.startsWith('/api/2.0/mlflow/artifacts/list')) {
      const next = request.path.includes('page_token=next')
      return void json(response, next
        ? { files: [{ path: 'model/MLmodel', file_size: 10 }] }
        : { files: [{ path: 'checkpoint/meta.json', file_size: 20 }], next_page_token: 'next' })
    }
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const mlflow = new MlflowService({ baseUrl: server.baseUrl, maxPages: 3, pageSize: 1 })
    assert.deepEqual(await mlflow.getExperimentByName('demo-project'), {
      experiment_id: '1', name: 'demo-project', lifecycle_stage: 'active',
    })
    assert.deepEqual((await mlflow.searchRuns({ experimentIds: ['1'] })).map(run => run.info.run_id), ['a', 'b'])
    assert.equal((await mlflow.metricHistory('a', 'val_accuracy'))[0]?.value, 0.9)
    assert.deepEqual((await mlflow.listArtifacts('a')).map(file => file.path), ['checkpoint/meta.json', 'model/MLmodel'])
  } finally {
    await server.close()
  }
})

test('MLflow Artifact verification reads proxy bytes and checks digest and size', async () => {
  const artifact = Buffer.from('model-bytes')
  const server = await mockServer((request, response) => {
    if (request.path.startsWith('/api/2.0/mlflow-artifacts/artifacts/model.bin')) {
      response.writeHead(200, { 'content-type': 'application/octet-stream' })
      response.end(artifact)
      return
    }
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const mlflow = new MlflowService({ baseUrl: server.baseUrl, maxArtifactBytes: 100 })
    assert.deepEqual(await mlflow.verifyArtifact({
      runId: 'run-1',
      path: 'model.bin',
      expectedDigest: 'sha256:357e5d6fafa34d27360fec24b4326d3534905e33c6acdee60198fb078b7b79e5',
    }), {
      runId: 'run-1', path: 'model.bin', size: 11,
      digest: 'sha256:357e5d6fafa34d27360fec24b4326d3534905e33c6acdee60198fb078b7b79e5', verified: true,
    })
  } finally {
    await server.close()
  }
})

test('MLflow Registry methods use the public API and return durable receipts', async () => {
  const server = await mockServer((request, response) => {
    if (request.path === '/api/2.0/mlflow/runs/get?run_id=run-1') {
      return void json(response, { run: { info: { run_id: 'run-1', status: 'FINISHED' }, data: { tags: [] } } })
    }
    if (request.path === '/api/2.0/mlflow/registered-models/create') {
      return void json(response, { registered_model: { name: 'demo-model' } })
    }
    if (request.path.startsWith('/api/2.0/mlflow/registered-models/get')) {
      return void json(response, { registered_model: { name: 'demo-model' } })
    }
    if (request.path.startsWith('/api/2.0/mlflow/model-versions/search')) {
      const next = request.path.includes('page_token=next')
      return void json(response, next
        ? { model_versions: [{ name: 'demo-model', version: '6', description: 'older' }] }
        : { model_versions: [{ name: 'demo-model', version: '7', description: 'current' }], next_page_token: 'next' })
    }
    if (request.path === '/api/2.0/mlflow/model-versions/create') {
      return void json(response, { model_version: { name: 'demo-model', version: '7', run_id: 'run-1', source: 'runs:/run-1/model' } })
    }
    if (request.path === '/api/2.0/mlflow/registered-models/alias') {
      return void json(response, {})
    }
    json(response, { message: 'unexpected' }, 500)
  })
  try {
    const mlflow = new MlflowService({ baseUrl: server.baseUrl })
    assert.equal((await mlflow.getRun('run-1')).info.run_id, 'run-1')
    assert.equal((await mlflow.getRegisteredModel('demo-model'))?.name, 'demo-model')
    assert.equal((await mlflow.createRegisteredModel('demo-model')).name, 'demo-model')
    assert.deepEqual((await mlflow.searchModelVersions('demo-model')).map(version => version.version), ['7', '6'])
    assert.deepEqual(await mlflow.createModelVersion({
      name: 'demo-model', source: 'runs:/run-1/model', runId: 'run-1', description: 'Approved evidence sha256:abc',
    }), { name: 'demo-model', version: '7', run_id: 'run-1', source: 'runs:/run-1/model' })
    assert.deepEqual(await mlflow.setRegisteredModelAlias({ name: 'demo-model', alias: 'candidate', version: '7' }), {
      name: 'demo-model', alias: 'candidate', version: '7',
    })
    assert.deepEqual(server.requests.at(-1)?.body, { name: 'demo-model', alias: 'candidate', version: '7' })
    const searches = server.requests.filter(request => request.path.includes('/model-versions/search'))
    assert.equal(searches.every(request => request.method === 'GET' && request.body === undefined), true)
  } finally {
    await server.close()
  }
})

test('project process executes only fixed argv without a shell and enforces output limits', async () => {
  const root = await mkdtemp(join(tmpdir(), 'galatea-process-'))
  await mkdir(join(root, 'scripts'))
  await writeFile(join(root, 'scripts', 'check.mjs'), [
    "process.stdout.write(JSON.stringify({argv: process.argv.slice(2), injected: process.env.INJECTED ?? null}))",
  ].join('\n'))
  const service = new ProjectProcessService({ timeoutMs: 1_000, maxOutputBytes: 1_000 })
  const result = await service.run({
    projectRoot: root,
    argv: [process.execPath, 'scripts/check.mjs', 'literal;echo injected'],
    env: { INJECTED: 'safe' },
  })
  assert.equal(result.exitCode, 0)
  assert.deepEqual(JSON.parse(result.stdout), { argv: ['literal;echo injected'], injected: 'safe' })
})
