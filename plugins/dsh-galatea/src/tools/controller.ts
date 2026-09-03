import { randomUUID } from 'node:crypto'
import { readFile, rename, stat, unlink, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative } from 'node:path'
import { parseDocument } from 'yaml'
import type { JsonValue, OperationStatus, ToolResult } from '../contracts/index.ts'
import { evidenceDigest, failure, success } from '../contracts/index.ts'
import { executionFromRay, operationStatus } from '../contracts/status.ts'
import { selectBestRun, type ComparableRun, type RunIdentity } from '../policies/comparability.ts'
import {
  authorizeDatasetAccess,
  authorizeTransition,
  type ApprovalReference,
  type ExecutionRole,
  type LifecycleStage,
} from '../policies/lifecycle.ts'
import { evaluatePlanIntegrity, deriveIntegrityAdvisories, type PlanIntegrityEvaluation } from '../policies/integrity.ts'
import {
  resolveProjectPath,
  validateProjectStructure,
  type IntegrityReportDeclaration,
  type IntegrityRole,
  type RunEvidenceSource,
  type TrainingProjectManifest,
} from '../policies/project.ts'
import { evaluateQualityGates } from '../policies/quality-gates.ts'
import { HttpServiceError } from '../services/http.ts'
import type { MlflowExperiment, MlflowModelVersion, MlflowRun, MlflowService } from '../services/mlflow.ts'
import type { ProjectProcessService } from '../services/project-process.ts'
import { deterministicSubmissionId, type RayJobsService } from '../services/ray.ts'

type ProcessClient = Pick<ProjectProcessService, 'run'>
type RayClient = Pick<RayJobsService, 'get' | 'list' | 'logs' | 'stop' | 'submit'> & Partial<Pick<RayJobsService, 'version'>>
type MlflowClient = Pick<MlflowService,
  | 'getExperimentByName'
  | 'searchRuns'
  | 'getRun'
  | 'getArtifact'
  | 'verifyArtifact'
  | 'getRegisteredModel'
  | 'createRegisteredModel'
  | 'searchModelVersions'
  | 'createModelVersion'
  | 'setRegisteredModelAlias'>

export type PostRunIntegrityReport = {
  readonly [key: string]: JsonValue
  readonly id: 'preprocessing' | 'migration'
  readonly artifactPath: string
  readonly roles: IntegrityRole[]
  readonly status: 'passed'
  readonly artifactDigest: string
  readonly contentDigest: string
  readonly size: number
  readonly runStatusSource: 'param' | 'tag'
  readonly runStatusKey: string
  readonly runDigestSource: 'param' | 'tag'
  readonly runDigestKey: string
}

export type PostRunIntegrityEvidence = {
  readonly [key: string]: JsonValue
  readonly role: IntegrityRole
  readonly reports: PostRunIntegrityReport[]
}

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
  readonly rankedRunIds: string[]
  readonly rejected: {
    readonly runId: string
    readonly reasons: string[]
  }[]
}

export type CandidateVerificationOutput = {
  readonly runId: string
  readonly metrics: Readonly<Record<string, number>>
  readonly artifacts: Readonly<Record<string, JsonValue>>
  readonly integrity: PostRunIntegrityEvidence
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
  readonly integrity: PostRunIntegrityEvidence
  readonly evidence: StageEvidence
}

export interface GalateaControllerOptions {
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath?: string
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
  readonly files: Readonly<Record<string, {
    readonly sha256: string
    readonly size_bytes: number
    readonly key?: string
    readonly filename?: string
  }>> & {
    readonly working_dir: { readonly sha256: string; readonly size_bytes: number; readonly key?: string; readonly filename?: string }
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
  readonly integrity?: PlanIntegrityEvaluation
  readonly advisories?: string[]
  readonly operationStatus?: OperationStatus
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
  const output: Record<string, string> = {}
  for (const item of values ?? []) {
    if (Object.prototype.hasOwnProperty.call(output, item.key)) throw new Error(`Run contains duplicate string evidence key ${item.key}`)
    output[item.key] = item.value
  }
  return output
}

function metricMap(values: readonly { readonly key: string; readonly value: number }[] | undefined): Record<string, number> {
  const output: Record<string, number> = {}
  for (const item of values ?? []) {
    if (!Number.isFinite(item.value)) continue
    if (Object.prototype.hasOwnProperty.call(output, item.key)) throw new Error(`Run contains duplicate metric key ${item.key}`)
    output[item.key] = item.value
  }
  return output
}

function valueAtPath(value: unknown, path: string): unknown {
  let current = value
  for (const segment of path.split('.')) {
    if (current === null || typeof current !== 'object' || Array.isArray(current)
      || !Object.prototype.hasOwnProperty.call(current, segment)) return undefined
    current = (current as Record<string, unknown>)[segment]
  }
  return current
}

function parseArtifactJson(bytes: Uint8Array, path: string): Record<string, unknown> {
  let decoded: string
  try {
    decoded = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new Error(`integrity Artifact ${path} is not valid UTF-8`)
  }
  try {
    return object(JSON.parse(decoded), `integrity Artifact ${path}`)
  } catch (error: unknown) {
    if (error instanceof TypeError) throw error
    throw new Error(`integrity Artifact ${path} is not valid JSON`)
  }
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

const DEFAULT_PROVENANCE = {
  executionIdentity: { source: 'tag', key: 'galatea.execution.identity' },
  project: { source: 'tag', key: 'galatea.project' },
  release: { source: 'tag', key: 'galatea.release.id' },
  submission: { source: 'tag', key: 'galatea.submission.id' },
  readiness: { source: 'tag', key: 'galatea.readiness.digest' },
  executionMode: { source: 'tag', key: 'galatea.execution.mode' },
  promotable: { source: 'tag', key: 'galatea.promotable' },
} as const

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
  readonly manifestPath: string
  readonly manifest: TrainingProjectManifest
  private readonly process: ProcessClient
  private readonly ray: RayClient
  private readonly mlflow: MlflowClient

  constructor(options: GalateaControllerOptions) {
    this.projectRoot = options.projectRoot
    this.releaseRoot = options.releaseRoot
    this.manifestPath = options.manifestPath ?? 'galatea.project.yaml'
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
    if (isAbsolute(candidate)) {
      throw new TypeError(`configPath must be relative to projectRoot ${this.projectRoot}; for example configs/baseline.yaml`)
    }
    const configRoot = await resolveProjectPath(this.projectRoot, this.manifest.spec.configRoot)
    const config = await resolveProjectPath(this.projectRoot, candidate)
    const within = relative(configRoot, config)
    if (within === '..' || within.startsWith('../') || within.startsWith('..\\')) {
      throw new TypeError('config path must stay below the declared configRoot')
    }
    return relative(await resolveProjectPath(this.projectRoot, '.'), config).replaceAll('\\', '/')
  }

  private async release(candidate: string): Promise<ReleaseManifest> {
    if (isAbsolute(candidate)) {
      throw new TypeError(`releaseManifestPath must be relative to releaseRoot ${this.releaseRoot}; for example <release-id>/release.json`)
    }
    const path = await resolveProjectPath(this.releaseRoot, candidate)
    const value = object(JSON.parse(await readFile(path, 'utf8')), 'release manifest')
    if (value['schema_version'] !== 1) throw new TypeError('release manifest schema_version must be 1')
    if (value['project'] !== this.manifest.metadata.name) throw new TypeError('release manifest belongs to another project')
    const files = object(value['files'], 'release manifest files')
    const parsedFiles: Record<string, { sha256: string; size_bytes: number; key?: string; filename?: string }> = {}
    for (const [name, rawFile] of Object.entries(files)) {
      const file = object(rawFile, `release manifest files.${name}`)
      const size = file['size_bytes']
      if (!Number.isSafeInteger(size) || (size as number) < 1) throw new TypeError(`release ${name} size must be positive`)
      const sha256 = nonEmpty(file['sha256'], `${name}.sha256`)
      if (!/^[a-f0-9]{64}$/.test(sha256)) throw new TypeError(`release ${name} sha256 must be 64 lowercase hexadecimal characters`)
      const key = typeof file['key'] === 'string' ? nonEmpty(file['key'], `${name}.key`) : undefined
      if (key !== undefined) {
        const expectedPrefix = `ray-runtime/${this.manifest.metadata.name}/`
        if (!key.startsWith(expectedPrefix) || key.split('/').includes('..')) {
          throw new TypeError(`release ${name} key must stay below ${expectedPrefix}`)
        }
      }
      parsedFiles[name] = {
        sha256,
        size_bytes: size as number,
        ...(key === undefined ? {} : { key }),
        ...(typeof file['filename'] === 'string' ? { filename: nonEmpty(file['filename'], `${name}.filename`) } : {}),
      }
    }
    const workingDir = parsedFiles['working_dir']
    if (workingDir === undefined) throw new TypeError('release manifest files must include working_dir')
    const releaseId = nonEmpty(value['release_id'], 'release_id')
    if (!/^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$/.test(releaseId)) throw new TypeError('release_id contains unsupported characters')
    const runtimeEnv = jsonObject(value['runtime_env'], 'runtime_env')
    const workingDirUri = runtimeEnv['working_dir']
    if (workingDir.key !== undefined && (typeof workingDirUri !== 'string' || !workingDirUri.endsWith(`/${workingDir.key}`))) {
      throw new TypeError('runtime_env working_dir must reference the declared working_dir key')
    }
    return {
      schema_version: 1,
      project: value['project'],
      release_id: releaseId,
      runtime_env: runtimeEnv,
      files: { ...parsedFiles, working_dir: workingDir },
    }
  }

  async inspectProject(input: { readonly approvalPolicy?: string; readonly signal?: AbortSignal } = {}): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      const structure = await validateProjectStructure(this.projectRoot, this.manifest, this.manifestPath)
      const [experiment, ray] = await Promise.all([
        this.mlflow.getExperimentByName(this.manifest.spec.mlflow.experimentName, input.signal),
        this.ray.version === undefined
          ? Promise.resolve(undefined)
          : this.ray.version(input.signal).catch(() => undefined),
      ])
      return success({
        project: this.manifest.metadata.name,
        pathSemantics: {
          configPath: 'relative to projectRoot and below configRoot',
          releaseManifestPath: 'relative to releaseRoot',
        },
        task: this.manifest.spec.task,
        executionBackend: this.manifest.spec.executionBackend,
        structure,
        objective: this.manifest.spec.objective,
        pauseResume: this.manifest.spec.capabilities.pauseResume,
        experimentName: this.manifest.spec.mlflow.experimentName,
        experimentId: experiment?.experiment_id ?? null,
        services: {
          ray: { reachable: ray !== undefined, ...(ray === undefined ? {} : { version: ray.version, rayVersion: ray.ray_version }) },
          mlflow: { reachable: experiment !== undefined, experimentExists: experiment !== undefined },
        },
        approval: {
          policy: input.approvalPolicy ?? 'unknown',
          promptsEnabled: input.approvalPolicy === 'never' ? false : input.approvalPolicy === 'ask' ? true : null,
        },
        operationStatus: operationStatus('project', 'not-applicable', 'not-evaluated', 'not-required'),
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
        patches: input.patches.map(patch => ({ path: [...patch.path], value: patch.value })),
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
      const structure = await validateProjectStructure(this.projectRoot, this.manifest, this.manifestPath)
      const configPath = await this.configPath(input.configPath)
      const release = await this.release(input.releaseManifestPath)
      const checkArgv = this.manifest.spec.entrypoints.checkConfig.map(argument => argument === '{config}' ? configPath : argument)
      const checked = await this.process.run({ projectRoot: this.projectRoot, argv: checkArgv, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      if (checked.exitCode !== 0) throw new Error(`project config validation failed with exit code ${String(checked.exitCode)}`)
      const argv = this.manifest.spec.entrypoints.plan.map(argument => argument === '{config}' ? configPath : argument)
      const executed = await this.process.run({ projectRoot: this.projectRoot, argv, ...(input.signal === undefined ? {} : { signal: input.signal }) })
      if (executed.exitCode !== 0) throw new Error(`project plan failed with exit code ${String(executed.exitCode)}`)
      const plan = jsonObject(JSON.parse(executed.stdout), 'project plan')
      const config = object(plan['config'], 'project plan config')
      const run = object(config['run'], 'project plan config.run')
      if (run['role'] !== input.role) throw new Error(`requested role ${input.role} does not match resolved config role ${String(run['role'])}`)
      const evaluation = object(config['evaluation'], 'project plan config.evaluation')
      const rayConfig = object(config['ray'], 'project plan config.ray')
      if (Object.keys(rayConfig).length === 0) throw new Error('project plan must declare Ray configuration')
      const requestedResources = object(plan['requested_resources'], 'project plan requested_resources')
      if (Object.keys(requestedResources).length === 0) throw new Error('project plan must declare requested Ray resources')
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
      const integrity = evaluatePlanIntegrity(this.manifest.spec.integrity, input.role, plan)
      if (!integrity.passed) {
        throw new Error(`project plan failed required integrity checks: ${deriveIntegrityAdvisories(integrity).join('; ')}`)
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
          files: release.files,
          runtimeEnv: release.runtime_env,
        },
        integrity,
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
        plan: {
          configDigest: plan['config_digest'] ?? null,
          objective: plan['objective'] ?? null,
          dataset: plan['dataset'] ?? null,
          code: plan['code'] ?? null,
          requestedResources,
          tracking: plan['tracking'] ?? null,
          willTrain: plan['will_train'] ?? null,
          executionBackend: this.manifest.spec.executionBackend,
          projectStructure: structure,
        },
        integrity,
        advisories: deriveIntegrityAdvisories(integrity),
        evidence,
        operationStatus: operationStatus('plan', 'planned', 'not-evaluated', 'approval-required', {
          preprocessingParity: integrity.preprocessing.status,
          migrationContamination: integrity.migration.status,
        }),
      } as unknown as RunPlan, `Prepared ${input.role} Run readiness evidence.`, { evidenceDigest: evidence.digest })
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
      const executionIdentity = input.role === 'champion' && candidateEvidence !== undefined
        ? evidenceDigest({
            readinessIdentity: planned.data.identity,
            candidateRunId: input.candidateRunId,
            candidateEvidenceDigest: candidateEvidence.digest,
          })
        : planned.data.identity
      const submissionId = deterministicSubmissionId(this.manifest.metadata.name, input.role, executionIdentity)
      const result = await this.ray.submit({
        submissionId,
        idempotencyKey: executionIdentity,
        entrypoint: renderEntrypoint(this.manifest.spec.entrypoints.train, planned.data.configPath),
        runtimeEnv: planned.data.runtimeEnv,
        metadata: {
          project: this.manifest.metadata.name,
          role: input.role,
          attempt: input.attempt,
          release_id: planned.data.releaseId,
          evidence_digest: planned.data.evidence.digest,
          'galatea.execution.identity': executionIdentity,
          'galatea.project': this.manifest.metadata.name,
          'galatea.release.id': planned.data.releaseId,
          'galatea.submission.id': submissionId,
          'galatea.readiness.digest': planned.data.evidence.digest,
          'galatea.execution.mode': 'governed-ray-job',
          'galatea.promotable': 'true',
          ...(input.candidateRunId === undefined ? {} : { candidate_run_id: input.candidateRunId }),
          ...(candidateEvidence === undefined ? {} : { candidate_evidence_digest: candidateEvidence.digest }),
        },
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      })
      return success({
        submissionId: result.submissionId,
        reused: result.reused,
        ...(result.status === undefined ? {} : { status: result.status }),
        idempotencyKey: executionIdentity,
        readinessEvidenceDigest: planned.data.evidence.digest,
        operationStatus: operationStatus(
          'job',
          result.status === undefined ? 'queued' : executionFromRay(result.status),
          'not-evaluated',
          'approved-for-execution',
        ),
      }, result.reused ? `Reused Ray Job ${result.submissionId}.` : `Submitted Ray Job ${result.submissionId}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async observeJob(input: {
    readonly submissionId: string
    readonly includeLogs?: boolean
    readonly logCursor?: number
    readonly signal?: AbortSignal
  }): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      const job = await this.ray.get(input.submissionId, input.signal)
      if (job === undefined) return failure({ category: 'not-found', message: 'Ray Job does not exist', retryable: false, stateChanged: false })
      if (job.metadata?.['project'] !== this.manifest.metadata.name) {
        return failure({
          category: 'permission-denied',
          message: `Ray Job belongs to project ${String(job.metadata?.['project'] ?? 'unknown')}, not selected project ${this.manifest.metadata.name}`,
          retryable: false,
          stateChanged: false,
          nextAction: 'Select the project that owns this Ray Job and retry.',
        })
      }
      const logs = input.includeLogs === true
        ? await this.ray.logs(input.submissionId, input.logCursor ?? 0, input.signal)
        : undefined
      return success({
        submissionId: job.submission_id,
        status: job.status,
        rayStatus: job.status,
        metadata: job.metadata ?? {},
        operationStatus: operationStatus('job', executionFromRay(job.status), 'not-evaluated', 'unknown'),
        ...(job.message === undefined ? {} : { message: job.message }),
        ...(job.error_type === undefined ? {} : { errorType: job.error_type }),
        ...(logs === undefined ? {} : {
          logs: logs.logs,
          logsTruncated: logs.truncated,
          logCursor: logs.cursor,
          nextLogCursor: logs.nextCursor,
          logCursorReset: logs.reset,
        }),
      }, `Ray Job ${job.submission_id} is ${job.status}; this is execution state only, not model quality or governance verification.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }

  async stopJob(input: { readonly submissionId: string; readonly reason: string; readonly idempotencyKey: string; readonly signal?: AbortSignal }): Promise<ToolResult<Record<string, JsonValue>>> {
    try {
      nonEmpty(input.reason, 'reason')
      nonEmpty(input.idempotencyKey, 'idempotencyKey')
      const job = await this.ray.get(input.submissionId, input.signal)
      if (job === undefined) throw new HttpServiceError('Ray Job does not exist', 'not-found', false, 404)
      if (job.metadata?.['project'] !== this.manifest.metadata.name) {
        return failure({
          category: 'permission-denied',
          message: `Ray Job belongs to project ${String(job.metadata?.['project'] ?? 'unknown')}, not selected project ${this.manifest.metadata.name}`,
          retryable: false,
          stateChanged: false,
          nextAction: 'Select the project that owns this Ray Job and retry.',
        })
      }
      if (job.metadata?.['idempotency_key'] !== input.idempotencyKey) {
        return failure({
          category: 'conflict',
          message: 'idempotencyKey does not match the immutable Ray Job execution identity',
          retryable: false,
          stateChanged: false,
          nextAction: 'Use the idempotencyKey returned by galatea_submit_job for this exact Job.',
        })
      }
      const result = await this.ray.stop(input.submissionId, input.idempotencyKey, input.signal)
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
          'galatea.execution.identity': planned.data.identity,
          'galatea.project': this.manifest.metadata.name,
          'galatea.release.id': planned.data.releaseId,
          'galatea.submission.id': submissionId,
          'galatea.readiness.digest': planned.data.evidence.digest,
          'galatea.execution.mode': 'governed-ray-job',
          'galatea.promotable': 'true',
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
        operationStatus: operationStatus(
          'job',
          result.status === undefined ? 'queued' : executionFromRay(result.status),
          'not-evaluated',
          'approved-for-execution',
        ),
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
    const params = stringMap(run.data?.params)
    const tags = stringMap(run.data?.tags)
    if (run.info.status !== 'FINISHED') reasons.push('Run status is not FINISHED')
    for (const [key, expected] of Object.entries(this.manifest.spec.runEvidence.requiredTags)) {
      if (tags[key] !== expected) reasons.push(`required tag ${key} does not equal ${expected}`)
    }
    const declaration = this.manifest.spec.runEvidence.provenance ?? DEFAULT_PROVENANCE
    const provenance = Object.entries(declaration)
    const values = Object.fromEntries(provenance.map(([name, source]) => [name, sourceValue(source, params, tags)]))
    for (const [name, value] of Object.entries(values)) {
      if (value === undefined || value.trim() === '') reasons.push(`Run lacks governed provenance ${name}`)
    }
    if (values.project !== this.manifest.metadata.name) {
      reasons.push(`Run provenance project ${String(values.project ?? 'unknown')} does not match ${this.manifest.metadata.name}`)
    }
    if (values.executionMode !== 'governed-ray-job') {
      reasons.push(`Run provenance execution mode ${String(values.executionMode ?? 'unknown')} is not governed-ray-job`)
    }
    if (values.promotable !== 'true') reasons.push('Run provenance is not promotable')
    if (values.executionIdentity === undefined) reasons.push('Run provenance execution identity is missing')
    if (values.release === undefined) reasons.push('Run provenance release identity is missing')
    if (values.submission === undefined) reasons.push('Run provenance submission identity is missing')
    if (values.readiness === undefined) reasons.push('Run provenance readiness binding is missing')
    return reasons
  }

  private async declaredExperiment(signal?: AbortSignal): Promise<MlflowExperiment> {
    const experiment = await this.mlflow.getExperimentByName(this.manifest.spec.mlflow.experimentName, signal)
    if (experiment === undefined) throw new HttpServiceError('MLflow Experiment does not exist', 'not-found', false, 404)
    return experiment
  }

  private ensureExperiment(run: MlflowRun, experiment: MlflowExperiment): string[] {
    const reasons: string[] = []
    if (run.info.experiment_id !== experiment.experiment_id) {
      reasons.push([
        `Run ${run.info.run_id} belongs to MLflow Experiment ID ${String(run.info.experiment_id ?? 'unknown')}`,
        `but the selected Galatea project ${this.manifest.metadata.name} expects ${experiment.name} (ID ${experiment.experiment_id})`,
        'select the project that owns this Run and retry',
      ].join('; '))
    }
    return reasons
  }

  private async postRunIntegrity(
    run: MlflowRun,
    role: IntegrityRole,
    signal?: AbortSignal,
  ): Promise<PostRunIntegrityEvidence> {
    const integrity = this.manifest.spec.integrity
    if (integrity === undefined) throw new Error('project manifest does not declare required post-Run integrity evidence')
    const params = stringMap(run.data?.params)
    const tags = stringMap(run.data?.tags)
    const reports = Object.entries(integrity.reports) as Array<[
      'preprocessing' | 'migration',
      IntegrityReportDeclaration,
    ]>
    const normalized: PostRunIntegrityReport[] = []
    for (const [id, declaration] of reports) {
      if (!declaration.roles.includes(role)) {
        throw new Error(`integrity report ${id} is not declared for Run role ${role}`)
      }
      const artifact = await this.mlflow.getArtifact({
        runId: run.info.run_id,
        path: declaration.artifactPath,
        ...(signal === undefined ? {} : { signal }),
      })
      const report = parseArtifactJson(artifact.bytes, declaration.artifactPath)
      const exactFields = ['schema_version', 'report_id', 'role', 'status', 'payload', 'content_digest']
      const unknownFields = Object.keys(report).filter(key => !exactFields.includes(key))
      const missingFields = exactFields.filter(key => !Object.prototype.hasOwnProperty.call(report, key))
      if (unknownFields.length > 0 || missingFields.length > 0) {
        throw new Error(`integrity report ${id} has an invalid envelope`)
      }
      if (report['schema_version'] !== 'galatea/integrity/v1' || report['report_id'] !== id) {
        throw new Error(`integrity report ${id} has an invalid schema or report identity`)
      }
      if (report['role'] !== role) throw new Error(`integrity report ${id} role does not match Run role ${role}`)
      object(report['payload'], `integrity report ${id} payload`)
      const status = valueAtPath(report, declaration.statusPath)
      const declaredDigest = valueAtPath(report, declaration.digestPath)
      if (status !== 'passed') {
        throw new Error(`integrity report ${id} status must be passed, not ${String(status ?? 'missing')}`)
      }
      if (typeof declaredDigest !== 'string' || !/^[a-f0-9]{64}$/.test(declaredDigest)) {
        throw new Error(`integrity report ${id} digest is missing or malformed`)
      }
      if (declaration.digestPath !== 'content_digest') {
        throw new Error(`integrity report ${id} digestPath must identify the top-level content_digest field`)
      }
      const { content_digest: _digest, ...digestMaterial } = report
      if (evidenceDigest(digestMaterial) !== `sha256:${declaredDigest}`) {
        throw new Error(`integrity report ${id} content does not match its declared digest`)
      }
      const runStatus = declaration.statusSource.source === 'param'
        ? params[declaration.statusSource.key]
        : tags[declaration.statusSource.key]
      const runDigest = declaration.digestSource.source === 'param'
        ? params[declaration.digestSource.key]
        : tags[declaration.digestSource.key]
      if (runStatus !== status) {
        throw new Error(`Run ${declaration.statusSource.source} ${declaration.statusSource.key} does not match integrity report ${id}`)
      }
      if (runDigest !== artifact.digest) {
        throw new Error(`Run ${declaration.digestSource.source} ${declaration.digestSource.key} does not match integrity Artifact ${id}`)
      }
      normalized.push({
        id,
        artifactPath: declaration.artifactPath,
        roles: [...declaration.roles],
        status: 'passed',
        artifactDigest: artifact.digest,
        contentDigest: `sha256:${declaredDigest}`,
        size: artifact.size,
        runStatusSource: declaration.statusSource.source,
        runStatusKey: declaration.statusSource.key,
        runDigestSource: declaration.digestSource.source,
        runDigestKey: declaration.digestSource.key,
      })
    }
    if (integrity.migration.enabled && !normalized.some(report => report.id === 'migration')) {
      throw new Error('migration integrity is enabled but its post-Run report is missing')
    }
    return { role, reports: normalized }
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
      ...this.ensureExperiment(run, experiment),
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
        reasons.push(...this.ensureExperiment(run, experiment))
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
      return success<RunComparisonOutput & Record<string, JsonValue>>({
        objective: this.manifest.spec.objective,
        ...(selected.best === undefined ? {} : { bestRunId: selected.best.runId }),
        rankedRunIds: selected.ranked.map(run => run.runId),
        rejected: [...rejected, ...selected.rejected].map(item => ({ runId: item.runId, reasons: [...item.reasons] })),
        operationStatus: operationStatus('comparison', 'not-applicable', 'not-evaluated', 'not-required'),
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
      reasons.push(...this.ensureExperiment(run, experiment))
      const comparable = this.comparable(run)
      if (comparable.identity.role !== 'trial') reasons.push('training-optimization evidence requires a trial Run')
      if (reasons.length > 0) throw new Error(reasons.join('; '))
      const integrity = await this.postRunIntegrity(run, 'trial', input.signal)
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
        integrity,
        artifacts,
      }
      const evidence: StageEvidence = {
        stage: input.stage,
        artifactId: input.runId,
        digest: evidenceDigest(evidencePackage),
      }
      return success<StageEvidenceOutput & Record<string, JsonValue>>({
        runId: input.runId,
        stage: input.stage,
        identity: comparable.identity,
        metrics: comparable.metrics,
        artifacts,
        integrity,
        evidence,
        operationStatus: operationStatus('run', 'succeeded', 'not-evaluated', 'approval-required'),
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
      reasons.push(...this.ensureExperiment(run, experiment))
      const comparable = this.comparable(run)
      if (comparable.identity.role !== 'champion') reasons.push('final validation requires a champion Run')
      const tags = stringMap(run.data?.tags)
      if (tags['test.evaluated'] !== 'true') reasons.push('final validation Run has not evaluated the final test split')
      if (reasons.length > 0) throw new Error(reasons.join('; '))
      const integrity = await this.postRunIntegrity(run, 'champion', input.signal)
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
        integrity,
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
      return success<CandidateVerificationOutput & Record<string, JsonValue>>({
        runId: input.runId,
        metrics,
        artifacts,
        integrity,
        qualityGates,
        modelUri: expectedModelUri,
        evidence,
        operationStatus: operationStatus(
          'candidate',
          'succeeded',
          gates.passed ? 'passed' : 'failed',
          gates.passed ? 'approval-required' : 'blocked',
        ),
      }, gates.passed
        ? `Verified final-validation evidence for Run ${input.runId}; promotion still requires approval.`
        : `Run ${input.runId} finished, but required model quality gates failed.`, { evidenceDigest: evidence.digest })
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
        operationStatus: operationStatus('promotion', 'succeeded', 'passed', 'promoted'),
      }, `Promoted ${name} version ${version.version} to alias ${input.alias}.`)
    } catch (error: unknown) {
      return normalizeError(error)
    }
  }
}
