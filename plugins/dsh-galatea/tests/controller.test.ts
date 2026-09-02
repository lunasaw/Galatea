import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import type { ApprovalReference } from '../src/policies/lifecycle.ts'
import type { TrainingProjectManifest } from '../src/policies/project.ts'
import type { MlflowRun } from '../src/services/mlflow.ts'
import type { RayJobInfo } from '../src/services/ray.ts'
import { GalateaController, type StageEvidence } from '../src/tools/controller.ts'

const manifest: TrainingProjectManifest = {
  apiVersion: 'galatea/v1',
  kind: 'TrainingProject',
  metadata: { name: 'demo-project' },
  spec: {
    task: 'image-classification',
    objective: { metric: 'val_accuracy', direction: 'max' },
    compatibility: [
      'task', 'datasetDigest', 'splitDigest', 'preprocessingVersion',
      'metricDefinition', 'evaluationProtocol', 'role',
    ],
    capabilities: { pauseResume: false },
    configRoot: 'configs',
    entrypoints: {
      checkConfig: ['python', 'scripts/train.py', '--config', '{config}', '--check-config'],
      plan: ['python', 'scripts/train.py', '--config', '{config}', '--plan'],
      train: ['python', 'scripts/train.py', '--config', '{config}'],
    },
    mlflow: {
      experimentName: 'demo-project',
      trackingUriEnv: 'MLFLOW_TRACKING_URI',
      registeredModelName: 'demo-model',
    },
    runEvidence: {
      compatibility: {
        task: { source: 'constant', value: 'image-classification' },
        datasetDigest: { source: 'param', key: 'data.content_sha256' },
        splitDigest: { source: 'param', key: 'data.split_sha256' },
        preprocessingVersion: { source: 'param', key: 'data.preprocessing_version' },
        metricDefinition: { source: 'constant', value: 'accuracy-v1' },
        evaluationProtocol: { source: 'constant', value: 'fixed-holdout-v1' },
        role: { source: 'tag', key: 'run.role' },
      },
      requiredTags: {
        'run.outcome': 'succeeded',
        'artifact.roundtrip_verified': 'true',
      },
      stageArtifacts: {
        'training-optimization': ['reports/model-selection.json'],
        'final-validation': ['reports/final-test-evaluation.json', 'model/MLmodel'],
      },
      modelSource: { artifactPath: 'model', uriTag: 'model.uri' },
    },
    qualityGates: [
      { name: 'accuracy', source: 'metric', key: 'test_accuracy', operator: 'gte', threshold: 0.8, required: true },
      { name: 'model', source: 'evidence', key: 'model/MLmodel', operator: 'exists', required: true },
    ],
  },
}

const resumableManifest: TrainingProjectManifest = {
  ...manifest,
  spec: {
    ...manifest.spec,
    capabilities: {
      pauseResume: true,
      checkpointEntrypoint: ['python', 'scripts/checkpoint.py'],
      resumeEntrypoint: ['python', 'scripts/train.py', '--config', '{config}', '--resume'],
    },
  },
}

function mlflowRun(input: {
  runId: string
  role: 'trial' | 'champion'
  valAccuracy: number
  testAccuracy?: number
}): MlflowRun {
  return {
    info: { run_id: input.runId, experiment_id: '1', status: 'FINISHED' },
    data: {
      metrics: [
        { key: 'val_accuracy', value: input.valAccuracy },
        ...(input.testAccuracy === undefined ? [] : [{ key: 'test_accuracy', value: input.testAccuracy }]),
      ],
      params: [
        { key: 'data.content_sha256', value: 'data-1' },
        { key: 'data.split_sha256', value: 'split-1' },
        { key: 'data.preprocessing_version', value: 'prep-1' },
      ],
      tags: [
        { key: 'run.role', value: input.role },
        { key: 'run.outcome', value: 'succeeded' },
        { key: 'artifact.roundtrip_verified', value: 'true' },
        ...(input.role === 'champion' ? [
          { key: 'test.evaluated', value: 'true' },
          { key: 'model.uri', value: `runs:/${input.runId}/model` },
        ] : []),
      ],
    },
  }
}

async function fixture(options: {
  manifest?: TrainingProjectManifest
  job?: RayJobInfo
  checkpointReceipt?: { readonly runId: string; readonly path: string; readonly digest: string }
} = {}) {
  const projectRoot = await mkdtemp(join(tmpdir(), 'galatea-controller-'))
  await mkdir(join(projectRoot, 'configs'))
  await mkdir(join(projectRoot, 'releases', 'release-1'), { recursive: true })
  await writeFile(join(projectRoot, 'configs', 'trial.yaml'), 'run:\n  role: trial\nevaluation:\n  evaluate_test: false\n')
  await writeFile(join(projectRoot, 'configs', 'champion.yaml'), 'run:\n  role: champion\nevaluation:\n  evaluate_test: true\n')
  await writeFile(join(projectRoot, 'releases', 'release-1', 'release.json'), JSON.stringify({
    schema_version: 1,
    project: 'demo-project',
    release_id: 'release-1',
    runtime_env: { working_dir: 's3://releases/release-1/working-dir.zip' },
    files: { working_dir: { sha256: 'abc123', size_bytes: 10 } },
  }))

  const processCalls: readonly string[][] = []
  const mutableProcessCalls = processCalls as string[][]
  const processInputs: Array<{ readonly argv: readonly string[]; readonly env?: Readonly<Record<string, string>> }> = []
  const submitted: Array<Record<string, unknown>> = []
  const events: string[] = []
  const artifactVerifications: Array<{ readonly runId: string; readonly path: string; readonly expectedDigest?: string }> = []
  const artifacts = new Map<string, string>([
    ['trial-2:reports/model-selection.json', 'sha256:selection'],
    ['champion-1:reports/final-test-evaluation.json', 'sha256:report'],
    ['champion-1:model/MLmodel', 'sha256:model'],
  ])
  const runs = new Map<string, MlflowRun>([
    ['trial-1', mlflowRun({ runId: 'trial-1', role: 'trial', valAccuracy: 0.81 })],
    ['trial-2', mlflowRun({ runId: 'trial-2', role: 'trial', valAccuracy: 0.91 })],
    ['champion-1', mlflowRun({ runId: 'champion-1', role: 'champion', valAccuracy: 0.9, testAccuracy: 0.84 })],
  ])
  const aliases: Array<Record<string, string>> = []
  const controller = new GalateaController({
    projectRoot,
    releaseRoot: join(projectRoot, 'releases'),
    manifest: options.manifest ?? manifest,
    process: {
      async run(input) {
        mutableProcessCalls.push([...input.argv])
        processInputs.push({ argv: [...input.argv], ...(input.env === undefined ? {} : { env: input.env }) })
        if (input.argv.includes('scripts/checkpoint.py')) {
          events.push('checkpoint')
          return {
            exitCode: 0,
            signal: null,
            stderr: '',
            stdout: JSON.stringify(options.checkpointReceipt),
          }
        }
        const role = input.argv.some(value => value.includes('champion')) ? 'champion' : 'trial'
        return {
          exitCode: 0,
          signal: null,
          stderr: '',
          stdout: JSON.stringify({
            config: { run: { role }, evaluation: { evaluate_test: role === 'champion' } },
            config_digest: `${role}-config`,
            objective: { metric: 'val_accuracy', mode: 'max', uses_test_holdout: false },
            dataset: { content_sha256: 'data-1', split_sha256: 'split-1' },
            code: { source_sha256: 'code-1' },
            idempotency_key: `${role}-identity`,
          }),
        }
      },
    },
    ray: {
      async get(submissionId) { return options.job?.submission_id === submissionId ? options.job : undefined },
      async list() { return [] },
      async logs() { return { logs: 'bounded logs', truncated: false } },
      async stop() {
        events.push('stop')
        return { stopped: true, previousStatus: 'RUNNING' }
      },
      async submit(input) {
        events.push('submit')
        submitted.push(input)
        return { submissionId: input.submissionId, reused: false }
      },
    },
    mlflow: {
      async getExperimentByName(name) {
        return name === 'demo-project'
          ? { experiment_id: '1', name: 'demo-project', lifecycle_stage: 'active' }
          : undefined
      },
      async searchRuns() { return [...runs.values()] },
      async getRun(runId) {
        const run = runs.get(runId)
        if (run === undefined) throw new Error('missing mock Run')
        return run
      },
      async verifyArtifact(input) {
        events.push('verify-artifact')
        artifactVerifications.push(input)
        const digest = artifacts.get(`${input.runId}:${input.path}`)
        if (digest === undefined) throw new Error('missing mock Artifact')
        if (input.expectedDigest !== undefined && input.expectedDigest !== digest) {
          throw new Error('mock Artifact digest mismatch')
        }
        return { runId: input.runId, path: input.path, size: 10, digest, verified: true }
      },
      async getRegisteredModel() { return undefined },
      async createRegisteredModel(name) { return { name } },
      async searchModelVersions() { return [] },
      async createModelVersion(input) {
        return { name: input.name, version: '7', run_id: input.runId, source: input.source }
      },
      async setRegisteredModelAlias(input) {
        const result = { name: input.name, alias: input.alias, version: input.version }
        aliases.push(result)
        return result
      },
    },
  })
  return {
    controller,
    processCalls,
    processInputs,
    submitted,
    aliases,
    runs,
    artifacts,
    events,
    artifactVerifications,
  }
}

function approvalFor(evidence: StageEvidence): ApprovalReference {
  return {
    valid: true,
    stage: evidence.stage,
    artifactId: evidence.artifactId,
    evidenceDigest: evidence.digest,
  }
}

test('plans and submits a fixed project entrypoint with deterministic identity', async () => {
  const { controller, processCalls, submitted } = await fixture()
  const planned = await controller.planRun({
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
  })
  assert.equal(planned.ok, true)
  if (!planned.ok) return
  assert.equal(planned.data.evidence.stage, 'readiness')

  const result = await controller.submitJob({
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
    approval: approvalFor(planned.data.evidence),
  })
  assert.equal(result.ok, true)
  assert.deepEqual(processCalls[0], ['python', 'scripts/train.py', '--config', 'configs/trial.yaml', '--plan'])
  assert.equal(submitted.length, 1)
  assert.equal(submitted[0]?.['entrypoint'], 'python scripts/train.py --config configs/trial.yaml')
  assert.deepEqual(submitted[0]?.['runtimeEnv'], { working_dir: 's3://releases/release-1/working-dir.zip' })
  assert.equal(typeof submitted[0]?.['idempotencyKey'], 'string')
  assert.match(String(submitted[0]?.['submissionId']), /^demo-project-trial-[a-f0-9]{12}$/)
})

test('binds the declared Runtime Environment to readiness identity', async () => {
  const { controller } = await fixture()
  const first = await controller.planRun({
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
  })
  assert.equal(first.ok, true)
  if (!first.ok) return
  const releasePath = join(controller.releaseRoot, 'release-1', 'release.json')
  const original = await readFile(releasePath, 'utf8')
  await writeFile(releasePath, original.replace('working-dir.zip', 'tampered.zip'))
  const second = await controller.planRun({
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
  })
  assert.equal(second.ok, true)
  if (!second.ok) return
  assert.notEqual(second.data.identity, first.data.identity)
})

test('rejects changed readiness evidence, test leakage, and unsupported pause/resume', async () => {
  const { controller } = await fixture()
  const rejected = await controller.submitJob({
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
    approval: {
      valid: true,
      stage: 'readiness',
      artifactId: 'wrong',
      evidenceDigest: 'sha256:wrong',
    },
  })
  assert.equal(rejected.ok, false)
  if (!rejected.ok) assert.equal(rejected.error.category, 'approval-required')

  const leaked = await controller.planRun({
    configPath: 'configs/champion.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'trial',
    attempt: 'a1',
  })
  assert.equal(leaked.ok, false)
  if (!leaked.ok) assert.equal(leaked.error.category, 'precondition-failed')

  const paused = await controller.pauseJob({ submissionId: 'job-1', reason: 'maintenance' })
  assert.deepEqual(paused, {
    ok: false,
    error: {
      category: 'unsupported',
      message: 'project demo-project does not declare checkpoint pause/resume support',
      retryable: false,
      stateChanged: false,
    },
  })
  const resumed = await controller.resumeJob({
    originalSubmissionId: 'job-1',
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    checkpoint: { runId: 'trial-1', path: 'checkpoints/best/model.bin', digest: 'sha256:x' },
    attempt: 'resume-1',
  })
  assert.equal(resumed.ok, false)
  if (!resumed.ok) assert.equal(resumed.error.category, 'unsupported')
})

test('requires approved training-optimization evidence before a champion Job', async () => {
  const { controller, submitted } = await fixture()
  const planned = await controller.planRun({
    configPath: 'configs/champion.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'champion',
    attempt: 'champion-1',
  })
  assert.equal(planned.ok, true)
  if (!planned.ok) return
  const readinessApproval = approvalFor(planned.data.evidence)

  const missing = await controller.submitJob({
    configPath: 'configs/champion.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'champion',
    attempt: 'champion-1',
    approval: readinessApproval,
  })
  assert.equal(missing.ok, false)
  if (!missing.ok) assert.equal(missing.error.category, 'approval-required')

  const candidate = await controller.buildStageEvidence({
    runId: 'trial-2',
    stage: 'training-optimization',
  })
  assert.equal(candidate.ok, true)
  if (!candidate.ok) return
  const submittedChampion = await controller.submitJob({
    configPath: 'configs/champion.yaml',
    releaseManifestPath: 'release-1/release.json',
    role: 'champion',
    attempt: 'champion-1',
    approval: readinessApproval,
    candidateRunId: 'trial-2',
    candidateApproval: approvalFor(candidate.data.evidence),
  })
  assert.equal(submittedChampion.ok, true)
  assert.equal(submitted.length, 1)
  assert.equal((submitted[0]?.['metadata'] as Record<string, string>)['candidate_run_id'], 'trial-2')
  assert.equal(
    (submitted[0]?.['metadata'] as Record<string, string>)['candidate_evidence_digest'],
    candidate.data.evidence.digest,
  )
})

test('pauses only after a project checkpoint is verified through the Artifact API', async () => {
  const digest = `sha256:${'a'.repeat(64)}`
  const checkpoint = { runId: 'trial-1', path: 'checkpoints/pause/state.json', digest }
  const { controller, artifacts, processInputs, events, artifactVerifications } = await fixture({
    manifest: resumableManifest,
    job: {
      submission_id: 'job-1',
      status: 'RUNNING',
      metadata: { project: 'demo-project', role: 'trial' },
    },
    checkpointReceipt: checkpoint,
  })
  artifacts.set('trial-1:checkpoints/pause/state.json', digest)

  const paused = await controller.pauseJob({ submissionId: 'job-1', reason: 'planned maintenance' })

  assert.equal(paused.ok, true)
  if (!paused.ok) return
  assert.deepEqual(paused.data.checkpoint, checkpoint)
  assert.equal(paused.data.stopped, true)
  assert.deepEqual(processInputs[0], {
    argv: ['python', 'scripts/checkpoint.py'],
    env: {
      GALATEA_SUBMISSION_ID: 'job-1',
      GALATEA_PAUSE_REASON: 'planned maintenance',
    },
  })
  assert.deepEqual(artifactVerifications, [{
    runId: 'trial-1',
    path: 'checkpoints/pause/state.json',
    expectedDigest: digest,
  }])
  assert.deepEqual(events, ['checkpoint', 'verify-artifact', 'stop'])
})

test('rejects a checkpoint whose Run belongs to another Experiment or role', async () => {
  const digest = `sha256:${'c'.repeat(64)}`
  const checkpoint = { runId: 'champion-1', path: 'checkpoints/pause/state.json', digest }
  const { controller, artifacts } = await fixture({
    manifest: resumableManifest,
    job: {
      submission_id: 'job-1',
      status: 'RUNNING',
      metadata: { project: 'demo-project', role: 'trial' },
    },
    checkpointReceipt: checkpoint,
  })
  artifacts.set('champion-1:checkpoints/pause/state.json', digest)

  const paused = await controller.pauseJob({ submissionId: 'job-1', reason: 'maintenance' })

  assert.equal(paused.ok, false)
  if (!paused.ok) assert.equal(paused.error.category, 'precondition-failed')
})

test('resumes as a new lineage-linked Job from a verified durable checkpoint', async () => {
  const digest = `sha256:${'b'.repeat(64)}`
  const checkpoint = { runId: 'trial-1', path: 'checkpoints/pause/state.json', digest }
  const { controller, artifacts, submitted, events } = await fixture({
    manifest: resumableManifest,
    job: {
      submission_id: 'job-1',
      status: 'STOPPED',
      metadata: { project: 'demo-project', role: 'trial' },
    },
  })
  artifacts.set('trial-1:checkpoints/pause/state.json', digest)

  const input = {
    originalSubmissionId: 'job-1',
    configPath: 'configs/trial.yaml',
    releaseManifestPath: 'release-1/release.json',
    checkpoint,
    attempt: 'resume-1',
  } as const
  const blocked = await controller.resumeJob(input)
  assert.equal(blocked.ok, false)
  if (!blocked.ok) assert.equal(blocked.error.category, 'approval-required')
  assert.equal(submitted.length, 0)

  const planned = await controller.planResume(input)
  assert.equal(planned.ok, true)
  if (!planned.ok) return
  const resumed = await controller.resumeJob({
    ...input,
    approval: approvalFor(planned.data.evidence),
  })

  assert.equal(resumed.ok, true)
  if (!resumed.ok) return
  assert.equal(resumed.data.originalSubmissionId, 'job-1')
  assert.equal(resumed.data.checkpointRunId, 'trial-1')
  assert.equal(submitted.length, 1)
  assert.equal(submitted[0]?.['entrypoint'], 'python scripts/train.py --config configs/trial.yaml --resume')
  assert.deepEqual(submitted[0]?.['runtimeEnv'], {
    working_dir: 's3://releases/release-1/working-dir.zip',
    env_vars: {
      GALATEA_RESUMED_FROM_SUBMISSION_ID: 'job-1',
      GALATEA_RESUME_RUN_ID: 'trial-1',
      GALATEA_RESUME_ARTIFACT_PATH: 'checkpoints/pause/state.json',
      GALATEA_RESUME_ARTIFACT_DIGEST: digest,
      GALATEA_RESUME_ATTEMPT: 'resume-1',
    },
  })
  assert.deepEqual(submitted[0]?.['metadata'], {
    project: 'demo-project',
    role: 'trial',
    attempt: 'resume-1',
    release_id: 'release-1',
    evidence_digest: resumed.data.readinessEvidenceDigest,
    resumed_from_submission_id: 'job-1',
    resumed_from_run_id: 'trial-1',
    checkpoint_path: 'checkpoints/pause/state.json',
    checkpoint_digest: digest,
  })
  assert.match(String(submitted[0]?.['submissionId']), /^demo-project-trial-resume-[a-f0-9]{12}$/)
  assert.deepEqual(events, [
    'verify-artifact',
    'verify-artifact',
    'verify-artifact',
    'submit',
  ])
})

test('compares only compatible successful Runs in the declared direction', async () => {
  const { controller, runs } = await fixture()
  runs.set('champion-2', mlflowRun({ runId: 'champion-2', role: 'champion', valAccuracy: 0.99, testAccuracy: 0.99 }))
  const result = await controller.compareRuns({ referenceRunId: 'trial-1' })
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.data.bestRunId, 'trial-2')
  assert.deepEqual(result.data.rankedRunIds, ['trial-2', 'trial-1'])
  assert.equal(result.data.rejected.some(item => item.runId === 'champion-2'), true)
})

test('rejects Runs outside the project-declared MLflow Experiment', async () => {
  const { controller, runs } = await fixture()
  const external = mlflowRun({ runId: 'external', role: 'trial', valAccuracy: 0.99 })
  runs.set('external', { ...external, info: { ...external.info, experiment_id: 'other' } })

  const evidence = await controller.buildStageEvidence({
    runId: 'external',
    stage: 'training-optimization',
  })
  assert.equal(evidence.ok, false)
  if (!evidence.ok) assert.equal(evidence.error.category, 'precondition-failed')
})

test('recomputes final evidence through Artifact APIs before promotion', async () => {
  const { controller, aliases, artifacts } = await fixture()
  const verified = await controller.verifyCandidate({ runId: 'champion-1' })
  assert.equal(verified.ok, true)
  if (!verified.ok) return
  assert.equal(verified.data.evidence.qualityGatesPassed, true)

  artifacts.set('champion-1:model/MLmodel', 'sha256:model-changed')
  const blocked = await controller.promoteModel({
    runId: 'champion-1',
    alias: 'champion',
    idempotencyKey: 'promote-1',
    approval: approvalFor(verified.data.evidence),
  })
  assert.equal(blocked.ok, false)
  if (!blocked.ok) assert.equal(blocked.error.category, 'approval-required')
  assert.equal(aliases.length, 0)

  const current = await controller.verifyCandidate({ runId: 'champion-1' })
  assert.equal(current.ok, true)
  if (!current.ok) return
  const promoted = await controller.promoteModel({
    runId: 'champion-1',
    alias: 'champion',
    idempotencyKey: 'promote-1',
    approval: approvalFor(current.data.evidence),
  })
  assert.equal(promoted.ok, true)
  assert.deepEqual(aliases, [{ name: 'demo-model', alias: 'champion', version: '7' }])
})

test('observes and stops jobs with structured results', async () => {
  const { controller } = await fixture()
  const observed = await controller.observeJob({ submissionId: 'job-1', includeLogs: true })
  assert.equal(observed.ok, false)
  if (!observed.ok) assert.equal(observed.error.category, 'not-found')

  const stopped = await controller.stopJob({ submissionId: 'job-1', reason: 'operator request', idempotencyKey: 'stop-1' })
  assert.equal(stopped.ok, true)
})

test('patches YAML structurally, validates it, and restores invalid changes', async () => {
  const { controller, processCalls } = await fixture()
  const updated = await controller.patchConfig({
    configPath: 'configs/trial.yaml',
    patches: [
      { path: ['training'], value: { epochs: 2 } },
      { path: ['run', 'name_prefix'], value: 'candidate; no shell' },
    ],
  })
  assert.equal(updated.ok, true)
  const path = join(controller.projectRoot, 'configs', 'trial.yaml')
  const changed = await readFile(path, 'utf8')
  assert.match(changed, /epochs: 2/)
  assert.match(changed, /candidate; no shell/)
  assert.deepEqual(processCalls.at(-1), [
    'python', 'scripts/train.py', '--config', 'configs/trial.yaml', '--check-config',
  ])

  const process = (controller as unknown as { process: { run(input: { argv: readonly string[] }): Promise<unknown> } }).process
  const originalRun = process.run.bind(process)
  process.run = async () => ({ exitCode: 2, signal: null, stdout: '', stderr: 'invalid private detail' })
  const beforeRejectedPatch = await readFile(path, 'utf8')
  const rejected = await controller.patchConfig({
    configPath: 'configs/trial.yaml',
    patches: [{ path: ['training', 'epochs'], value: -1 }],
  })
  assert.equal(rejected.ok, false)
  if (!rejected.ok) {
    assert.equal(rejected.error.category, 'precondition-failed')
    assert.equal(rejected.error.stateChanged, false)
    assert.equal(rejected.error.message.includes('private detail'), false)
  }
  assert.equal(await readFile(path, 'utf8'), beforeRejectedPatch)
  process.run = originalRun
})
