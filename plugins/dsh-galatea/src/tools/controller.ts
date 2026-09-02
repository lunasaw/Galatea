import { randomUUID } from 'node:crypto'
import { readFile, rename, stat, unlink, writeFile } from 'node:fs/promises'
import { dirname, join, relative } from 'node:path'
import { parseDocument } from 'yaml'
import type { JsonValue, ToolResult } from '../contracts/index.ts'
import { evidenceDigest, failure, success } from '../contracts/index.ts'
import { selectBestRun, type ComparableRun, type RunIdentity } from '../policies/comparability.ts'
import {
  authorizeDatasetAccess,
  authorizeTransition,
  type ApprovalReference,
  type ExecutionRole,
  type LifecycleStage,
} from '../policies/lifecycle.ts'
import { resolveProjectPath, type RunEvidenceSource, type TrainingProjectManifest } from '../policies/project.ts'
import { evaluateQualityGates } from '../policies/quality-gates.ts'
import { HttpServiceError } from '../services/http.ts'
import type { MlflowExperiment, MlflowModelVersion, MlflowRun, MlflowService } from '../services/mlflow.ts'
import type { ProjectProcessService } from '../services/project-process.ts'
import { deterministicSubmissionId, type RayJobsService } from '../services/ray.ts'

type ProcessClient = Pick<ProjectProcessService, 'run'>
type RayClient = Pick<RayJobsService, 'get' | 'list' | 'logs' | 'stop' | 'submit'>
type MlflowClient = Pick<MlflowService,
  | 'getExperimentByName'
  | 'searchRuns'
  | 'getRun'
  | 'verifyArtifact'
  | 'getRegisteredModel'
  | 'createRegisteredModel'
  | 'searchModelVersions'
  | 'createModelVersion'
  | 'setRegisteredModelAlias'>

export type StageEvidence = {
  readonly [key: string]: JsonValue
  readonly stage: LifecycleStage
  readonly artifactId: string
  readonly digest: string
  readonly qualityGatesPassed?: boolean
}

export type RunComparisonOutput = {
  readonly objective: { readonly metric: string; readonly direction: 'max' | 'min' }
  readonly bestRunId?: string
  readonly rankedRunIds: readonly string[]
  readonly rejected: readonly {
    readonly runId: string
    readonly reasons: readonly string[]
  }[]
}

export type CandidateVerificationOutput = {
  readonly runId: string
  readonly metrics: Readonly<Record<string, number>>
  readonly artifacts: Readonly<Record<string, JsonValue>>
  readonly qualityGates: JsonValue
  readonly modelUri: string
  readonly evidence: StageEvidence
}

export type StageEvidenceOutput = {
  readonly runId: string
  readonly stage: 'training-optimization'
  readonly identity: RunIdentity
  readonly metrics: Readonly<Record<string, number>>
  readonly artifacts: Readonly<Record<string, JsonValue>>
  readonly evidence: StageEvidence
}

export interface GalateaControllerOptions {
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifest: TrainingProjectManifest
  readonly process: ProcessClient
  readonly ray: RayClient
  readonly mlflow: MlflowClient
}

interface ReleaseManifest {
  readonly schema_version: 1
  readonly project: string
  readonly release_id: string
  readonly runtime_env: Readonly<Record<string, JsonValue>>
  readonly files: {
    readonly working_dir: { readonly sha256: string; readonly size_bytes: number }
  }
}

interface RunPlan {
  readonly [key: string]: JsonValue
  readonly role: ExecutionRole
  readonly attempt: string
  readonly configPath: string
  readonly releaseManifestPath: string
  readonly releaseId: string
  readonly runtimeEnv: Readonly<Record<string, JsonValue>>
  readonly identity: string
  readonly plan: Readonly<Record<string, JsonValue>>
  readonly evidence: StageEvidence
}

interface ResumePlan extends RunPlan {
  readonly originalSubmissionId: string
  readonly checkpoint: CheckpointReference
}

interface CheckpointReference {
  readonly [key: string]: JsonValue
  readonly runId: string
  readonly path: string
  readonly digest: string
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${path} must be an object`)
  return value as Record<string, unknown>
}

function nonEmpty(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new TypeError(`${path} must be a non-empty string`)
  return value
}

function checkpointReference(value: unknown): CheckpointReference {
  const input = object(value, 'checkpoint')
  const runId = nonEmpty(input['runId'], 'checkpoint.runId')
  const path = nonEmpty(input['path'], 'checkpoint.path')
  if (path.startsWith('/') || path.replaceAll('\\', '/').split('/').includes('..')) {
    throw new TypeError('checkpoint.path must be a relative Artifact path')
  }
  const digest = nonEmpty(input['digest'], 'checkpoint.digest')
  if (!/^sha256:[a-f0-9]{64}$/.test(digest)) {
    throw new TypeError('checkpoint.digest must be a lowercase SHA-256 digest')
  }
  return { runId, path, digest }
}

function jsonObject(value: unknown, path: string): Record<string, JsonValue> {
  const input = object(value, path)
  JSON.stringify(input)
  return input as Record<string, JsonValue>
}

function stringMap(values: readonly { readonly key: string; readonly value: string }[] | undefined): Record<string, string> {
  return Object.fromEntries((values ?? []).map(item => [item.key, item.value]))
}

function metricMap(values: readonly { readonly key: string; readonly value: number }[] | undefined): Record<string, number> {
  return Object.fromEntries((values ?? []).filter(item => Number.isFinite(item.value)).map(item => [item.key, item.value]))
}

function shellArgument(value: string): string {
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(value)) return value
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

function renderEntrypoint(argv: readonly string[], configPath: string): string {
  let substitutions = 0
  const rendered = argv.map((argument) => {
    if (argument === '{config}') {
      substitutions += 1
      return configPath
    }
    if (argument.includes('{config}')) throw new TypeError('{config} must occupy a complete argv element')
    return argument
  })
  if (substitutions !== 1) throw new TypeError('project entrypoint must contain exactly one {config} element')
  return rendered.map(shellArgument).join(' ')
}

function runtimeEnvWith(
  runtimeEnv: Readonly<Record<string, JsonValue>>,
  additions: Readonly<Record<string, string>>,
): Readonly<Record<string, JsonValue>> {
  const existingValue = runtimeEnv['env_vars']
  const existing = existingValue === undefined ? {} : object(existingValue, 'runtime_env.env_vars')
  for (const [key, value] of Object.entries(existing)) {
    if (typeof value !== 'string') throw new TypeError(`runtime_env.env_vars.${key} must be a string`)
  }
  return { ...runtimeEnv, env_vars: { ...existing as Record<string, string>, ...additions } }
}

function executionRole(value: unknown, path: string): ExecutionRole {
  if (value !== 'smoke' && value !== 'trial' && value !== 'champion') {
    throw new TypeError(`${path} must be smoke, trial, or champion`)
  }
  return value
}

const UNSAFE_PATCH_KEY = /^(?:__proto__|prototype|constructor)$/
const SECRET_PATCH_KEY = /(?:^|[._-])(?:authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key)(?:$|[._-])/i

function validatePatchPath(path: readonly string[]): void {
  if (path.length === 0) throw new TypeError('config patch path must not be empty')
  for (const segment of path) {
    if (segment.trim() === '') throw new TypeError('config patch path segments must be non-empty')
    if (UNSAFE_PATCH_KEY.test(segment)) throw new TypeError('config patch path contains an unsafe key')
    if (SECRET_PATCH_KEY.test(segment)) throw new TypeError('config patch path must not target a secret-like field')
  }
}

async function atomicWrite(path: string, contents: string, mode: number): Promise<void> {
  const temporary = join(dirname(path), `.${randomUUID()}.galatea.tmp`)
  try {
    await writeFile(temporary, contents, { encoding: 'utf8', mode, flag: 'wx' })
    await rename(temporary, path)
  } catch (error: unknown) {
    await unlink(temporary).catch(() => undefined)
    throw error
  }
}

function sourceValue(
  source: RunEvidenceSource,
  params: Readonly<Record<string, string>>,
  tags: Readonly<Record<string, string>>,
): string | undefined {
  if (source.source === 'constant') return source.value
  return source.source === 'param' ? params[source.key] : tags[source.key]
}

function normalizeError(error: unknown, stateChanged = false): ToolResult<never> {
  if (error instanceof HttpServiceError) {
    return failure({
      category: error.category,
      message: error.message,
      retryable: error.retryable,
      stateChanged,
    })
  }
  const message = error instanceof Error ? error.message : 'unknown Galatea failure'
  const category = /cancel/i.test(message) ? 'cancelled'
    : /timed out|timeout/i.test(message) ? 'timeout'
      : error instanceof TypeError || error instanceof SyntaxError ? 'invalid-input' : 'precondition-failed'
  return failure({ category, message, retryable: category === 'timeout', stateChanged })
}

export class GalateaController {
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifest: TrainingProjectManifest
  private readonly process: ProcessClient
  private readonly ray: RayClient
  private readonly mlflow: MlflowClient

  constructor(options: GalateaControllerOptions) {
    this.projectRoot = options.projectRoot
    this.releaseRoot = options.releaseRoot
    this.manifest = options.manifest
    this.process = options.process
    this.ray = options.ray
    this.mlflow = options.mlflow
  }

  private unsupportedPause(): ToolResult<never> {
    return failure({
      category: 'unsupported',
      message: `project ${this.manifest.metadata.name} does not declare checkpoint pause/resume support`,
      retryable: false,
      stateChanged: false,
    })
  }

  private async configPath(candidate: string): Promise<string> {
    const configRoot = await resolveProjectPath(this.projectRoot, this.manifest.spec.configRoot)
    const config = await resolveProjectPath(this.projectRoot, candidate)
    const within = relative(configRoot, config)
    if (within === '..' || within.startsWith('../') || within.startsWith('..\\')) {
      throw new TypeError('config path must stay below the declared configRoot')
    }
    return relative(await resolveProjectPath(this.projectRoot, '.'), config).replaceAll('\\', '/')
  }

  private async release(candidate: string): Promise<ReleaseManifest> {
    const path = await resolveProjectPath(this.releaseRoot, candidate)
    const value = object(JSON.parse(await readFile(path, 'utf8')), 'release manifest')
    if (value['schema_version'] !== 1) throw new TypeError('release manifest schema_version must be 1')
    if (value['project'] !== this.manifest.metadata.name) throw new TypeError('release manifest belongs to another project')
    const files = object(value['files'], 'release manifest files')
    const workingDir = object(files['working_dir'], 'release manifest working_dir')
    const size = workingDir['size_bytes']
    if (!Number.isSafeInteger(size) || (size as number) < 1) throw new TypeError('release working_dir size must be positive')
    return {
      schema_version: 1,
      project: value['project'],
      release_id: nonEmpty(value['release_id'], 'release_id'),
      runtime_env: jsonObject(value['runtime_env'], 'runtime_env'),
      files: {
        working_dir: {
          sha256: nonEmpty(workingDir['sha256'], 'working_dir.sha256'),
          size_bytes: size as number,
        },
      },
    }
  }

  async inspectProject(): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      await resolveProjectPath(this.projectRoot, this.manifest.spec.configRoot)
      return success({
        project: this.manifest.metadata.name,
        task: this.manifest.spec.task,
        objective: this.manifest.spec.objective,
        pauseResume: this.manifest.spec.capabilities.pauseResume,
        experimentName: this.manifest.spec.mlflow.experimentName,
      }, `Project ${this.manifest.metadata.name} satisfies the declared Galatea contract.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async patchConfig(input: {
    readonly configPath: string
    readonly patches: readonly { readonly path: readonly string[]; readonly value: JsonValue }[]
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    let path: string | undefined
    let original: string | undefined
    let mode: number | undefined
    let changed = false
    try {
      if (input.patches.length === 0) throw new TypeError('at least one config patch is required')
      const configPath = await this.configPath(input.configPath)
      path = await resolveProjectPath(this.projectRoot, configPath)
      original = await readFile(path, 'utf8')
      mode = (await stat(path)).mode
      const document = parseDocument(original)
      if (document.errors.length > 0) throw new TypeError('config YAML is invalid')
      for (const patch of input.patches) {
        validatePatchPath(patch.path)
        document.setIn([...patch.path], patch.value)
      }
      const rendered = document.toString()
      await atomicWrite(path, rendered, mode)
      changed = true
      const argv = this.manifest.spec.entrypoints.checkConfig.map(argument => argument === '{config}' ? configPath : argument)
      const checked = await this.process.run({
        projectRoot: this.projectRoot,
        argv,
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      if (checked.exitCode !== 0) throw new Error(`project config validation failed with exit code ${String(checked.exitCode)}`)
      let validation: JsonValue = null
      if (checked.stdout.trim() !== '') validation = jsonObject(JSON.parse(checked.stdout), 'project config validation')
      return success({
        configPath,
        patches: input.patches.map(patch => ({ path: patch.path, value: patch.value })),
        validation,
        configDigest: evidenceDigest(rendered),
      }, `Updated and validated ${configPath}.`)
    } catch (error: unknown) {
      if (changed && path !== undefined && original !== undefined && mode !== undefined) {
        try {
          await atomicWrite(path, original, mode)
          changed = false
        } catch {
          return failure({
            category: 'integrity-error',
            message: 'config validation failed and the original file could not be restored',
            retryable: false,
            stateChanged: true,
            ...(path === undefined ? {} : { platformIds: { configPath: path } }),
          })
        }
      }
      return normalizeError(error, changed)
    }
  }

  async planRun(input: {
    readonly configPath: string
    readonly releaseManifestPath: string
    readonly role: ExecutionRole
    readonly attempt: string
    readonly signal?: AbortSignal
  }): Promise<ToolResult<RunPlan>> {
    try {
      nonEmpty(input.attempt, 'attempt')
      const configPath = await this.configPath(input.configPath)
      const release = await this.release(input.releaseManifestPath)
      const argv = this.manifest.spec.entrypoints.plan.map(argument => argument === '{config}' ? configPath : argument)
      const executed = await this.process.run({ projectRoot: this.projectRoot, argv, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      if (executed.exitCode !== 0) throw new Error(`project plan failed with exit code ${String(executed.exitCode)}`)
      const plan = jsonObject(JSON.parse(executed.stdout), 'project plan')
      const config = object(plan['config'], 'project plan config')
      const run = object(config['run'], 'project plan config.run')
      if (run['role'] !== input.role) throw new Error(`requested role ${input.role} does not match resolved config role ${String(run['role'])}`)
      const evaluation = object(config['evaluation'], 'project plan config.evaluation')
      if (evaluation['evaluate_test'] === true) {
        const access = authorizeDatasetAccess(input.role, 'test')
        if (!access.allowed) throw new Error(access.reason)
      }
      const objective = object(plan['objective'], 'project plan objective')
      if (objective['metric'] !== this.manifest.spec.objective.metric
        || objective['mode'] !== this.manifest.spec.objective.direction) {
        throw new Error('project plan objective does not match the project declaration')
      }
      if (input.role !== 'champion' && objective['uses_test_holdout'] !== false) {
        throw new Error(`${input.role} plan must prove it does not use the final test holdout`)
      }
      const identityMaterial = {
        project: this.manifest.metadata.name,
        role: input.role,
        attempt: input.attempt,
        configPath,
        configDigest: plan['config_digest'] ?? null,
        dataset: plan['dataset'] ?? null,
        code: plan['code'] ?? null,
        workloadIdempotencyKey: plan['idempotency_key'] ?? null,
        release: {
          id: release.release_id,
          workingDirSha256: release.files.working_dir.sha256,
          runtimeEnv: release.runtime_env,
        },
      }
      const identity = evidenceDigest(identityMaterial)
      const evidencePackage = { ...identityMaterial, identity }
      const evidence: StageEvidence = {
        stage: 'readiness',
        artifactId: `${this.manifest.metadata.name}:${input.role}:${input.attempt}`,
        digest: evidenceDigest(evidencePackage),
      }
      return success({
        role: input.role,
        attempt: input.attempt,
        configPath,
        releaseManifestPath: input.releaseManifestPath,
        releaseId: release.release_id,
        runtimeEnv: release.runtime_env,
        identity,
        plan,
        evidence,
      } as RunPlan, `Prepared ${input.role} Run readiness evidence.`, { evidenceDigest: evidence.digest })
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async submitJob(input: {
    readonly configPath: string
    readonly releaseManifestPath: string
    readonly role: ExecutionRole
    readonly attempt: string
    readonly approval?: ApprovalReference
    readonly candidateRunId?: string
    readonly candidateApproval?: ApprovalReference
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    const planned = await this.planRun(input)
    if (!planned.ok) return planned
    const transition = authorizeTransition({
      to: 'training-optimization',
      evidence: planned.data.evidence,
      ...(input.approval === undefined ? {} : { approval: input.approval }),
    })
    if (!transition.allowed) {
      return failure({
        category: 'approval-required',
        message: transition.reasons.join('; '),
        retryable: false,
        stateChanged: false,
        nextAction: 'Approve the current readiness evidence before submitting the Ray Job.',
      })
    }
    let candidateEvidence: StageEvidence | undefined
    if (input.role === 'champion') {
      if (input.candidateRunId === undefined) {
        return failure({
          category: 'approval-required',
          message: 'champion submission requires an approved training-optimization candidate Run',
          retryable: false,
          stateChanged: false,
          nextAction: 'Build and approve training-optimization evidence for the selected Trial Run.',
        })
      }
      const built = await this.buildStageEvidence({
        runId: input.candidateRunId,
        stage: 'training-optimization',
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      if (!built.ok) return built
      candidateEvidence = built.data.evidence
      const candidateTransition = authorizeTransition({
        to: 'final-validation',
        evidence: candidateEvidence,
        ...(input.candidateApproval === undefined ? {} : { approval: input.candidateApproval }),
      })
      if (!candidateTransition.allowed) {
        return failure({
          category: 'approval-required',
          message: candidateTransition.reasons.join('; '),
          retryable: false,
          stateChanged: false,
          nextAction: 'Approve the current training-optimization evidence before submitting the Champion Job.',
        })
      }
    } else if (input.candidateRunId !== undefined || input.candidateApproval !== undefined) {
      return failure({
        category: 'invalid-input',
        message: 'candidate approval applies only to champion submission',
        retryable: false,
        stateChanged: false,
      })
    }
    try {
      const submissionId = deterministicSubmissionId(this.manifest.metadata.name, input.role, planned.data.identity)
      const result = await this.ray.submit({
        submissionId,
        idempotencyKey: planned.data.identity,
        entrypoint: renderEntrypoint(this.manifest.spec.entrypoints.train, planned.data.configPath),
        runtimeEnv: planned.data.runtimeEnv,
        metadata: {
          project: this.manifest.metadata.name,
          role: input.role,
          attempt: input.attempt,
          release_id: planned.data.releaseId,
          evidence_digest: planned.data.evidence.digest,
          ...(input.candidateRunId === undefined ? {} : { candidate_run_id: input.candidateRunId }),
          ...(candidateEvidence === undefined ? {} : { candidate_evidence_digest: candidateEvidence.digest }),
        },
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      return success({
        submissionId: result.submissionId,
        reused: result.reused,
        ...(result.status === undefined ? {} : { status: result.status }),
        idempotencyKey: planned.data.identity,
        readinessEvidenceDigest: planned.data.evidence.digest,
      }, result.reused ? `Reused Ray Job ${result.submissionId}.` : `Submitted Ray Job ${result.submissionId}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async observeJob(input: { readonly submissionId: string; readonly includeLogs?: boolean; readonly signal?: AbortSignal }): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      const job = await this.ray.get(input.submissionId, input.signal)
      if (job === undefined) return failure({ category: 'not-found', message: 'Ray Job does not exist', retryable: false, stateChanged: false })
      const logs = input.includeLogs === true ? await this.ray.logs(input.submissionId, input.signal) : undefined
      return success({
        submissionId: job.submission_id,
        status: job.status,
        ...(job.message === undefined ? {} : { message: job.message }),
        ...(job.error_type === undefined ? {} : { errorType: job.error_type }),
        ...(logs === undefined ? {} : { logs: logs.logs, logsTruncated: logs.truncated }),
      }, `Ray Job ${job.submission_id} is ${job.status}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async stopJob(input: { readonly submissionId: string; readonly reason: string; readonly idempotencyKey: string; readonly signal?: AbortSignal }): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      nonEmpty(input.reason, 'reason')
      nonEmpty(input.idempotencyKey, 'idempotencyKey')
      const result = await this.ray.stop(input.submissionId, input.signal)
      return success({
        submissionId: input.submissionId,
        stopped: result.stopped,
        previousStatus: result.previousStatus,
        reason: input.reason,
        idempotencyKey: input.idempotencyKey,
      }, result.stopped ? `Stopped Ray Job ${input.submissionId}.` : `Ray Job ${input.submissionId} was already stopping or stopped.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async pauseJob(input: {
    readonly submissionId: string
    readonly reason: string
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    if (!this.manifest.spec.capabilities.pauseResume) return this.unsupportedPause()
    let checkpointCreated = false
    try {
      nonEmpty(input.reason, 'reason')
      const entrypoint = this.manifest.spec.capabilities.checkpointEntrypoint
      if (entrypoint === undefined) throw new Error('project checkpoint entrypoint is missing')
      const job = await this.ray.get(input.submissionId, input.signal)
      if (job === undefined) throw new HttpServiceError('Ray Job does not exist', 'not-found', false, 404)
      if (job.metadata?.['project'] !== this.manifest.metadata.name) {
        throw new Error('Ray Job does not belong to the configured project')
      }
      if (job.status !== 'RUNNING') throw new Error(`Ray Job must be RUNNING to pause, not ${job.status}`)
      const expectedRole = executionRole(job.metadata?.['role'], 'Ray Job role')
      const executed = await this.process.run({
        projectRoot: this.projectRoot,
        argv: entrypoint,
        env: {
          GALATEA_SUBMISSION_ID: input.submissionId,
          GALATEA_PAUSE_REASON: input.reason,
        },
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      if (executed.exitCode !== 0) {
        throw new Error(`project checkpoint failed with exit code ${String(executed.exitCode)}`)
      }
      checkpointCreated = true
      const checkpoint = checkpointReference(JSON.parse(executed.stdout))
      const verified = await this.verifyCheckpoint({ checkpoint, expectedRole, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      const stopped = await this.ray.stop(input.submissionId, input.signal)
      return success({
        submissionId: input.submissionId,
        checkpoint,
        checkpointSize: verified.size,
        stopped: stopped.stopped,
        previousStatus: stopped.previousStatus,
        reason: input.reason,
      }, `Verified a durable checkpoint and requested stop for Ray Job ${input.submissionId}.`, {
        checkpointDigest: checkpoint.digest,
      })
    } catch (error: unknown) {
      return normalizeError(error, checkpointCreated)
    }
  }

  async planResume(input: {
    readonly originalSubmissionId: string
    readonly configPath: string
    readonly releaseManifestPath: string
    readonly checkpoint: { readonly runId: string; readonly path: string; readonly digest: string }
    readonly attempt: string
    readonly signal?: AbortSignal
  }): Promise<ToolResult<ResumePlan>> {
    if (!this.manifest.spec.capabilities.pauseResume) return this.unsupportedPause()
    try {
      nonEmpty(input.attempt, 'attempt')
      const checkpoint = checkpointReference(input.checkpoint)
      const original = await this.ray.get(input.originalSubmissionId, input.signal)
      if (original === undefined) throw new HttpServiceError('original Ray Job does not exist', 'not-found', false, 404)
      if (original.metadata?.['project'] !== this.manifest.metadata.name) {
        throw new Error('original Ray Job does not belong to the configured project')
      }
      if (original.status !== 'STOPPED' && original.status !== 'FAILED') {
        throw new Error(`original Ray Job must be STOPPED or FAILED before resume, not ${original.status}`)
      }
      const role = executionRole(original.metadata?.['role'], 'original Ray Job role')
      const planned = await this.planRun({
        configPath: input.configPath,
        releaseManifestPath: input.releaseManifestPath,
        role,
        attempt: input.attempt,
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      if (!planned.ok) return failure(planned.error)
      await this.verifyCheckpoint({ checkpoint, expectedRole: role, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      const identity = evidenceDigest({
        readinessIdentity: planned.data.identity,
        originalSubmissionId: input.originalSubmissionId,
        checkpoint,
      })
      const evidence: StageEvidence = {
        stage: 'readiness',
        artifactId: `${this.manifest.metadata.name}:${role}:${input.attempt}:resume:${input.originalSubmissionId}`,
        digest: evidenceDigest({
          project: this.manifest.metadata.name,
          role,
          attempt: input.attempt,
          originalSubmissionId: input.originalSubmissionId,
          checkpoint,
          releaseId: planned.data.releaseId,
          configPath: planned.data.configPath,
          identity,
        }),
      }
      return success({
        ...planned.data,
        role,
        identity,
        evidence,
        originalSubmissionId: input.originalSubmissionId,
        checkpoint,
      } as ResumePlan, `Prepared checkpoint resume readiness evidence for ${input.originalSubmissionId}.`, {
        evidenceDigest: evidence.digest,
      })
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async resumeJob(input: {
    readonly originalSubmissionId: string
    readonly configPath: string
    readonly releaseManifestPath: string
    readonly checkpoint: { readonly runId: string; readonly path: string; readonly digest: string }
    readonly attempt: string
    readonly approval?: ApprovalReference
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    if (!this.manifest.spec.capabilities.pauseResume) return this.unsupportedPause()
    const planned = await this.planResume(input)
    if (!planned.ok) return planned
    const transition = authorizeTransition({
      to: 'training-optimization',
      evidence: planned.data.evidence,
      ...(input.approval === undefined ? {} : { approval: input.approval }),
    })
    if (!transition.allowed) {
      return failure({
        category: 'approval-required',
        message: transition.reasons.join('; '),
        retryable: false,
        stateChanged: false,
        nextAction: 'Approve the current checkpoint resume readiness evidence before submitting a replacement Ray Job.',
      })
    }
    try {
      const entrypoint = this.manifest.spec.capabilities.resumeEntrypoint
      if (entrypoint === undefined) throw new Error('project resume entrypoint is missing')
      const checkpoint = planned.data.checkpoint
      const runtimeEnv = runtimeEnvWith(planned.data.runtimeEnv, {
        GALATEA_RESUMED_FROM_SUBMISSION_ID: input.originalSubmissionId,
        GALATEA_RESUME_RUN_ID: checkpoint.runId,
        GALATEA_RESUME_ARTIFACT_PATH: checkpoint.path,
        GALATEA_RESUME_ARTIFACT_DIGEST: checkpoint.digest,
        GALATEA_RESUME_ATTEMPT: input.attempt,
      })
      const submissionId = deterministicSubmissionId(
        this.manifest.metadata.name,
        `${planned.data.role}-resume`,
        planned.data.identity,
      )
      const result = await this.ray.submit({
        submissionId,
        idempotencyKey: planned.data.identity,
        entrypoint: renderEntrypoint(entrypoint, planned.data.configPath),
        runtimeEnv,
        metadata: {
          project: this.manifest.metadata.name,
          role: planned.data.role,
          attempt: input.attempt,
          release_id: planned.data.releaseId,
          evidence_digest: planned.data.evidence.digest,
          resumed_from_submission_id: input.originalSubmissionId,
          resumed_from_run_id: checkpoint.runId,
          checkpoint_path: checkpoint.path,
          checkpoint_digest: checkpoint.digest,
        },
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      return success({
        submissionId: result.submissionId,
        reused: result.reused,
        ...(result.status === undefined ? {} : { status: result.status }),
        originalSubmissionId: input.originalSubmissionId,
        checkpointRunId: checkpoint.runId,
        checkpointPath: checkpoint.path,
        checkpointDigest: checkpoint.digest,
        idempotencyKey: planned.data.identity,
        readinessEvidenceDigest: planned.data.evidence.digest,
      }, result.reused
        ? `Reused resumed Ray Job ${result.submissionId}.`
        : `Submitted resumed Ray Job ${result.submissionId}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  private comparable(run: MlflowRun): ComparableRun {
    const params = stringMap(run.data?.params)
    const tags = stringMap(run.data?.tags)
    const identity: Record<string, string> = {}
    for (const field of this.manifest.spec.compatibility) {
      const source = this.manifest.spec.runEvidence.compatibility[field]
      if (source === undefined) throw new Error(`Run evidence source is missing for ${field}`)
      const value = sourceValue(source, params, tags)
      if (value === undefined) throw new Error(`Run ${run.info.run_id} lacks compatibility evidence ${field}`)
      identity[field] = value
    }
    return { runId: run.info.run_id, identity: identity as RunIdentity, metrics: metricMap(run.data?.metrics) }
  }

  private runPreconditions(run: MlflowRun): string[] {
    const reasons: string[] = []
    const tags = stringMap(run.data?.tags)
    if (run.info.status !== 'FINISHED') reasons.push('Run status is not FINISHED')
    for (const [key, expected] of Object.entries(this.manifest.spec.runEvidence.requiredTags)) {
      if (tags[key] !== expected) reasons.push(`required tag ${key} does not equal ${expected}`)
    }
    return reasons
  }

  private async declaredExperiment(signal?: AbortSignal): Promise<MlflowExperiment> {
    const experiment = await this.mlflow.getExperimentByName(this.manifest.spec.mlflow.experimentName, signal)
    if (experiment === undefined) throw new HttpServiceError('MLflow Experiment does not exist', 'not-found', false, 404)
    return experiment
  }

  private ensureExperiment(run: MlflowRun, experimentId: string): string[] {
    const reasons: string[] = []
    if (run.info.experiment_id !== experimentId) reasons.push(`Run belongs to MLflow Experiment ${String(run.info.experiment_id ?? 'unknown')}, expected ${experimentId}`)
    return reasons
  }

  private async verifyCheckpoint(input: {
    readonly checkpoint: CheckpointReference
    readonly expectedRole: ExecutionRole
    readonly signal?: AbortSignal
  }): Promise<{ readonly size: number }> {
    const run = await this.mlflow.getRun(input.checkpoint.runId, input.signal)
    const experiment = await this.declaredExperiment(input.signal)
    const reasons = [
      ...this.runPreconditions(run),
      ...this.ensureExperiment(run, experiment.experiment_id),
    ]
    const role = stringMap(run.data?.tags)['run.role']
    if (role !== input.expectedRole) {
      reasons.push(`Checkpoint Run role ${String(role ?? 'unknown')} does not match Job role ${input.expectedRole}`)
    }
    if (reasons.length > 0) throw new Error(reasons.join('; '))
    const verified = await this.mlflow.verifyArtifact({
      runId: input.checkpoint.runId,
      path: input.checkpoint.path,
      expectedDigest: input.checkpoint.digest,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    })
    return { size: verified.size }
  }

  async compareRuns(input: { readonly referenceRunId: string; readonly signal?: AbortSignal }): Promise<ToolResult<RunComparisonOutput>> {
    try {
      const experiment = await this.declaredExperiment(input.signal)
      const runs = await this.mlflow.searchRuns({ experimentIds: [experiment.experiment_id], ...(input.signal === undefined ? {} : { signal: input.signal }) })
      const rejected: Array<{ runId: string; reasons: readonly string[] }> = []
      const candidates: ComparableRun[] = []
      for (const run of runs) {
        const reasons = this.runPreconditions(run)
        reasons.push(...this.ensureExperiment(run, experiment.experiment_id))
        try {
          const comparable = this.comparable(run)
          if (reasons.length === 0) candidates.push(comparable)
          else rejected.push({ runId: run.info.run_id, reasons })
        } catch (error: unknown) {
          rejected.push({ runId: run.info.run_id, reasons: [...reasons, error instanceof Error ? error.message : 'invalid Run evidence'] })
        }
      }
      const referenceIndex = candidates.findIndex(run => run.runId === input.referenceRunId)
      if (referenceIndex < 0) throw new Error('reference Run is missing or does not satisfy comparison preconditions')
      const [reference] = candidates.splice(referenceIndex, 1)
      candidates.unshift(reference!)
      const selected = selectBestRun(candidates, this.manifest.spec.objective)
      return success<RunComparisonOutput>({
        objective: this.manifest.spec.objective,
        ...(selected.best === undefined ? {} : { bestRunId: selected.best.runId }),
        rankedRunIds: selected.ranked.map(run => run.runId),
        rejected: [...rejected, ...selected.rejected],
      }, selected.best === undefined ? 'No comparable Run has the declared objective metric.' : `Best compatible Run is ${selected.best.runId}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async buildStageEvidence(input: {
    readonly runId: string
    readonly stage: 'training-optimization'
    readonly signal?: AbortSignal
  }): Promise<ToolResult<StageEvidenceOutput>> {
    try {
      const run = await this.mlflow.getRun(input.runId, input.signal)
      const reasons = this.runPreconditions(run)
      const experiment = await this.declaredExperiment(input.signal)
      reasons.push(...this.ensureExperiment(run, experiment.experiment_id))
      const comparable = this.comparable(run)
      if (comparable.identity.role !== 'trial') reasons.push('training-optimization evidence requires a trial Run')
      if (reasons.length > 0) throw new Error(reasons.join('; '))
      const artifacts: Record<string, JsonValue> = {}
      for (const path of this.manifest.spec.runEvidence.stageArtifacts['training-optimization']) {
        const verified = await this.mlflow.verifyArtifact({
          runId: input.runId,
          path,
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        })
        artifacts[path] = { digest: verified.digest, size: verified.size }
      }
      const evidencePackage = {
        project: this.manifest.metadata.name,
        runId: input.runId,
        runStatus: run.info.status ?? null,
        identity: comparable.identity,
        metrics: comparable.metrics,
        artifacts,
      }
      const evidence: StageEvidence = {
        stage: input.stage,
        artifactId: input.runId,
        digest: evidenceDigest(evidencePackage),
      }
      return success<StageEvidenceOutput>({
        runId: input.runId,
        stage: input.stage,
        identity: comparable.identity,
        metrics: comparable.metrics,
        artifacts,
        evidence,
      }, `Built training-optimization evidence for Run ${input.runId}.`, { evidenceDigest: evidence.digest })
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async verifyCandidate(input: { readonly runId: string; readonly signal?: AbortSignal }): Promise<ToolResult<CandidateVerificationOutput>> {
    try {
      const run = await this.mlflow.getRun(input.runId, input.signal)
      const reasons = this.runPreconditions(run)
      const experiment = await this.declaredExperiment(input.signal)
      reasons.push(...this.ensureExperiment(run, experiment.experiment_id))
      const comparable = this.comparable(run)
      if (comparable.identity.role !== 'champion') reasons.push('final validation requires a champion Run')
      const tags = stringMap(run.data?.tags)
      if (tags['test.evaluated'] !== 'true') reasons.push('final validation Run has not evaluated the final test split')
      if (reasons.length > 0) throw new Error(reasons.join('; '))
      const artifacts: Record<string, JsonValue> = {}
      for (const path of this.manifest.spec.runEvidence.stageArtifacts['final-validation']) {
        const verified = await this.mlflow.verifyArtifact({ runId: input.runId, path, ...(input.signal === undefined ? {} : { signal: input.signal }) })
        artifacts[path] = { digest: verified.digest, size: verified.size }
      }
      const metrics = comparable.metrics
      const gates = evaluateQualityGates(this.manifest.spec.qualityGates, { metrics, evidence: artifacts })
      const modelSource = this.manifest.spec.runEvidence.modelSource
      const expectedModelUri = `runs:/${input.runId}/${modelSource.artifactPath}`
      if (tags[modelSource.uriTag] !== expectedModelUri) throw new Error('Run model URI does not match the declared model Artifact')
      const evidencePackage = {
        project: this.manifest.metadata.name,
        runId: input.runId,
        runStatus: run.info.status ?? null,
        identity: comparable.identity,
        metrics,
        requiredTags: Object.fromEntries(Object.keys(this.manifest.spec.runEvidence.requiredTags).map(key => [key, tags[key] ?? null])),
        modelUri: expectedModelUri,
        artifacts,
        qualityGates: gates,
      }
      const evidence: StageEvidence = {
        stage: 'final-validation',
        artifactId: input.runId,
        digest: evidenceDigest(evidencePackage),
        qualityGatesPassed: gates.passed,
      }
      const qualityGates: JsonValue = {
        passed: gates.passed,
        results: gates.results.map(result => ({
          name: result.name,
          status: result.status,
          ...(result.actual === undefined ? {} : { actual: result.actual as JsonValue }),
          ...(result.expected === undefined ? {} : { expected: result.expected as JsonValue }),
          reason: result.reason,
        })),
      }
      return success<CandidateVerificationOutput>({
        runId: input.runId,
        metrics,
        artifacts,
        qualityGates,
        modelUri: expectedModelUri,
        evidence,
      }, `Verified final-validation evidence for Run ${input.runId}.`, { evidenceDigest: evidence.digest })
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async promoteModel(input: {
    readonly runId: string
    readonly alias: string
    readonly idempotencyKey: string
    readonly approval?: ApprovalReference
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    const verified = await this.verifyCandidate({ runId: input.runId, ...(input.signal === undefined ? {} : { signal: input.signal }) })
    if (!verified.ok) return verified
    const transition = authorizeTransition({
      to: 'promotion',
      evidence: verified.data.evidence,
      ...(input.approval === undefined ? {} : { approval: input.approval }),
    })
    if (!transition.allowed) {
      return failure({
        category: 'approval-required',
        message: transition.reasons.join('; '),
        retryable: false,
        stateChanged: false,
        nextAction: 'Approve the current final-validation evidence before promotion.',
      })
    }
    try {
      nonEmpty(input.alias, 'alias')
      nonEmpty(input.idempotencyKey, 'idempotencyKey')
      const name = this.manifest.spec.mlflow.registeredModelName
      if (name === undefined) throw new Error('project declaration does not define a Registered Model name')
      const modelUri = verified.data.modelUri
      if (await this.mlflow.getRegisteredModel(name, input.signal) === undefined) {
        await this.mlflow.createRegisteredModel(name, input.signal)
      }
      const marker = `galatea-idempotency:${input.idempotencyKey};evidence:${verified.data.evidence.digest}`
      const versions = await this.mlflow.searchModelVersions(name, input.signal)
      const sameKey = versions.find(version => version.description?.startsWith(`galatea-idempotency:${input.idempotencyKey};`) === true)
      if (sameKey !== undefined && (sameKey.run_id !== input.runId || sameKey.source !== modelUri || sameKey.description !== marker)) {
        return failure({ category: 'conflict', message: 'promotion idempotency key belongs to different evidence', retryable: false, stateChanged: false })
      }
      let version: MlflowModelVersion
      if (sameKey !== undefined) version = sameKey
      else {
        version = await this.mlflow.createModelVersion({
          name,
          source: modelUri,
          runId: input.runId,
          description: marker,
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        })
      }
      const alias = await this.mlflow.setRegisteredModelAlias({ name, alias: input.alias, version: version.version, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      return success({
        registeredModel: alias.name,
        version: alias.version,
        alias: alias.alias,
        runId: input.runId,
        evidenceDigest: verified.data.evidence.digest,
        idempotencyKey: input.idempotencyKey,
        reusedVersion: sameKey !== undefined,
      }, `Promoted ${name} version ${version.version} to alias ${input.alias}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }
}
