import { readFile, readdir, realpath, stat } from 'node:fs/promises'
import { basename, dirname, isAbsolute, relative, resolve } from 'node:path'
import { parse } from 'yaml'

export type ObjectiveDirection = 'max' | 'min'
export type ProjectEntrypoint = readonly string[]
export type IntegrityRole = 'smoke' | 'trial' | 'champion'

export interface IntegrityCheckDeclaration {
  readonly id: string
  readonly roles: readonly IntegrityRole[]
  readonly checkPath: string
  readonly required: boolean
}

export interface PreprocessingContextDeclaration {
  readonly id: string
  readonly roles: readonly IntegrityRole[]
  readonly outputPath: string
}

export interface PreprocessingComparisonDeclaration extends IntegrityCheckDeclaration {
  readonly leftContext: string
  readonly rightContext: string
  readonly fields: readonly string[]
}

export interface MigrationLineageDeclaration {
  readonly roles: readonly IntegrityRole[]
  readonly outputPath: string
  readonly allowed: readonly string[]
  readonly required: boolean
}

export interface ImprovementBacklogDeclaration {
  readonly id: string
  readonly roles: readonly IntegrityRole[]
  readonly outputPath: string
  readonly blocking: false
}

export interface IntegrityRunSource {
  readonly source: 'param' | 'tag'
  readonly key: string
}

export interface IntegrityReportDeclaration {
  readonly artifactPath: string
  readonly roles: readonly IntegrityRole[]
  readonly statusPath: string
  readonly digestPath: string
  readonly statusSource: IntegrityRunSource
  readonly digestSource: IntegrityRunSource
}

export interface ProjectIntegrityDeclaration {
  readonly planOutputPath: string
  readonly reports: {
    readonly preprocessing: IntegrityReportDeclaration
    readonly migration: IntegrityReportDeclaration
  }
  readonly preprocessing: {
    readonly contexts: readonly PreprocessingContextDeclaration[]
    readonly comparisons: readonly PreprocessingComparisonDeclaration[]
  }
  readonly migration: {
    readonly enabled: boolean
    readonly lineage: MigrationLineageDeclaration
    readonly contaminationChecks: readonly IntegrityCheckDeclaration[]
  }
  readonly improvementBacklog?: readonly ImprovementBacklogDeclaration[]
}

export interface QualityGateDeclaration {
  readonly name: string
  readonly source: 'metric' | 'evidence'
  readonly key: string
  readonly operator: 'exists' | 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte'
  readonly threshold?: string | number | boolean
  readonly required: boolean
}

export type RunEvidenceSource =
  | { readonly source: 'constant'; readonly value: string }
  | { readonly source: 'param' | 'tag'; readonly key: string }

/** Stable, framework-neutral Run provenance bindings declared by a project. */
export interface RunProvenanceDeclaration {
  readonly executionIdentity: RunEvidenceSource
  readonly project: RunEvidenceSource
  readonly release: RunEvidenceSource
  readonly submission: RunEvidenceSource
  readonly readiness: RunEvidenceSource
  readonly executionMode: RunEvidenceSource
  readonly promotable: RunEvidenceSource
}

export interface RunEvidenceDeclaration {
  readonly compatibility: Readonly<Record<string, RunEvidenceSource>>
  readonly provenance?: RunProvenanceDeclaration
  readonly requiredTags: Readonly<Record<string, string>>
  readonly stageArtifacts: {
    readonly 'training-optimization': readonly string[]
    readonly 'final-validation': readonly string[]
  }
  readonly modelSource: {
    readonly artifactPath: string
    readonly uriTag: string
  }
}

export interface TrainingProjectManifest {
  readonly apiVersion: 'galatea/v1'
  readonly kind: 'TrainingProject'
  readonly metadata: { readonly name: string }
  readonly spec: {
    readonly task: string
    /** Formal training is intentionally restricted to the Ray Jobs backend. */
    readonly executionBackend: 'ray'
    readonly packageName: string
    readonly objective: { readonly metric: string; readonly direction: ObjectiveDirection }
    readonly compatibility: readonly string[]
    readonly capabilities: {
      readonly pauseResume: boolean
      readonly checkpointEntrypoint?: ProjectEntrypoint
      readonly resumeEntrypoint?: ProjectEntrypoint
    }
    readonly configRoot: string
    readonly entrypoints: {
      readonly checkConfig: ProjectEntrypoint
      readonly plan: ProjectEntrypoint
      readonly train: ProjectEntrypoint
    }
    readonly mlflow: {
      readonly experimentName: string
      readonly trackingUriEnv: string
      readonly registeredModelName?: string
    }
    readonly runEvidence: RunEvidenceDeclaration
    readonly qualityGates: readonly QualityGateDeclaration[]
    readonly integrity?: ProjectIntegrityDeclaration
  }
}

export interface ProjectStructureReport {
  readonly [key: string]: import('../contracts/index.ts').JsonValue
  readonly projectRoot: string
  readonly projectDirectory: string
  readonly packageName: string
  readonly executionBackend: 'ray'
  readonly requiredPaths: string[]
}

const REQUIRED_PROJECT_FILES = ['README.md'] as const
const REQUIRED_PROJECT_DIRECTORIES = ['configs', 'src', 'tests', 'scripts'] as const

async function isDirectory(path: string): Promise<boolean> {
  try { return (await stat(path)).isDirectory() } catch { return false }
}

async function isFile(path: string): Promise<boolean> {
  try { return (await stat(path)).isFile() } catch { return false }
}

/**
 * Check the repository-owned workload layout before a project can be planned.
 * This is deliberately filesystem based; a prompt or a manifest alone cannot
 * prevent an agent from reusing an unrelated workload directory.
 */
export async function validateProjectStructure(
  projectRoot: string,
  manifest: TrainingProjectManifest,
  manifestPath = 'galatea.project.yaml',
): Promise<ProjectStructureReport> {
  const root = await realpath(projectRoot)
  const projectDirectory = basename(root)
  const parentDirectory = basename(dirname(root))
  if (parentDirectory === 'train-model' && projectDirectory !== manifest.metadata.name) {
    throw new TypeError(`project directory ${projectDirectory} must match manifest metadata.name ${manifest.metadata.name}`)
  }
  const missing: string[] = []
  if (!(await isFile(resolve(root, manifestPath)))) missing.push(manifestPath)
  for (const file of REQUIRED_PROJECT_FILES) if (!(await isFile(resolve(root, file)))) missing.push(file)
  for (const directory of REQUIRED_PROJECT_DIRECTORIES) if (!(await isDirectory(resolve(root, directory)))) missing.push(`${directory}/`)
  const configRoot = resolve(root, manifest.spec.configRoot)
  if (!(await isDirectory(configRoot))) missing.push(`${manifest.spec.configRoot}/`)
  let configs: string[] = []
  try { configs = (await readdir(configRoot)).filter(name => name.endsWith('.yaml') || name.endsWith('.yml')) } catch { /* reported above */ }
  if (configs.length === 0) missing.push(`${manifest.spec.configRoot}/*.yaml`)
  const packageRoot = resolve(root, 'src')
  let packages: string[] = []
  try {
    packages = (await readdir(packageRoot, { withFileTypes: true }))
      .filter(entry => entry.isDirectory() && /^[A-Za-z_][A-Za-z0-9_]*$/.test(entry.name))
      .map(entry => entry.name)
  } catch { /* reported above */ }
  const validPackages = []
  for (const packageName of packages) {
    if (await isFile(resolve(packageRoot, packageName, '__init__.py'))) validPackages.push(packageName)
  }
  if (validPackages.length === 0) missing.push('src/<python-package>/__init__.py')
  if (validPackages.length > 1) {
    throw new TypeError(`src must contain exactly one top-level Python package; found ${validPackages.join(', ')}`)
  }
  const expectedPackage = manifest.spec.packageName
  if (validPackages.length > 0 && !validPackages.includes(expectedPackage)) {
    throw new TypeError(`src package must match project directory ${projectDirectory} (expected ${expectedPackage})`)
  }
  const train = manifest.spec.entrypoints.train
  const scriptArgument = train.find((argument, index) => index > 0 && argument.startsWith('scripts/'))
  if (scriptArgument === undefined || !(await isFile(resolve(root, scriptArgument)))) {
    missing.push('scripts/<formal-training-entrypoint>')
  }
  if (!(await isFile(resolve(root, 'conda.yaml'))) && !(await isFile(resolve(root, 'pyproject.toml')))) {
    missing.push('conda.yaml or pyproject.toml')
  }
  if (missing.length > 0) throw new TypeError(`project structure is incomplete: missing ${missing.join(', ')}`)
  return {
    projectRoot: root,
    projectDirectory,
    packageName: expectedPackage,
    executionBackend: 'ray',
    requiredPaths: [manifestPath, ...REQUIRED_PROJECT_FILES, ...REQUIRED_PROJECT_DIRECTORIES.map(value => `${value}/`)],
  }
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${path} must be an object`)
  }
  return value as Record<string, unknown>
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new TypeError(`${path} must be a non-empty string`)
  return value
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') throw new TypeError(`${path} must be a boolean`)
  return value
}

function safeRelativePath(value: unknown, path: string): string {
  const candidate = text(value, path)
  const normalized = candidate.replaceAll('\\', '/')
  if (isAbsolute(candidate) || normalized.split('/').includes('..')) {
    throw new TypeError(`${path} must stay below the project root`)
  }
  return candidate
}

function safeArtifactPath(value: unknown, path: string): string {
  const candidate = text(value, path)
  if (candidate.includes('\\') || isAbsolute(candidate)
    || candidate.split('/').some(segment => segment === '' || segment === '.' || segment === '..'
      || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(segment))) {
    throw new TypeError(`${path} must be a safe relative Artifact path`)
  }
  return candidate
}

function argv(value: unknown, path: string): string[] {
  if (!Array.isArray(value) || value.length === 0) throw new TypeError(`${path} must be a non-empty argv array`)
  return value.map((item, index) => text(item, `${path}[${index}]`))
}

function argvTemplate(value: unknown, path: string, configPlaceholders: number): string[] {
  const result = argv(value, path)
  let found = 0
  for (const argument of result) {
    if (argument === '{config}') {
      found += 1
      continue
    }
    if (argument.includes('{config}') || /\{[^}]+\}/.test(argument)) {
      throw new TypeError(`${path} contains an unsupported template placeholder`)
    }
  }
  if (found !== configPlaceholders) {
    throw new TypeError(`${path} must contain exactly ${configPlaceholders} complete {config} argument${configPlaceholders === 1 ? '' : 's'}`)
  }
  return result
}

function optionalArgvTemplate(
  value: unknown,
  path: string,
  configPlaceholders: number,
): string[] | undefined {
  return value === undefined ? undefined : argvTemplate(value, path, configPlaceholders)
}

const REQUIRED_COMPATIBILITY = [
  'task', 'datasetDigest', 'splitDigest', 'preprocessingVersion',
  'metricDefinition', 'evaluationProtocol', 'role',
] as const

const SECRET_LIKE_KEY = /(?:^|[._-])(?:authorization|cookie|password|secret|api[_-]?key|access[_-]?key|secret[_-]?key)(?:$|[._-])/i

function safeEvidenceKey(value: unknown, path: string): string {
  const key = text(value, path)
  if (SECRET_LIKE_KEY.test(key) || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(key)) {
    throw new TypeError(`${path} must be a safe, non-secret-like evidence key`)
  }
  return key
}

function exactFields(value: Record<string, unknown>, allowed: readonly string[], path: string): void {
  const unknown = Object.keys(value).filter(field => !allowed.includes(field))
  if (unknown.length > 0) throw new TypeError(`${path} contains unknown fields: ${unknown.join(', ')}`)
}

function safeDottedPath(value: unknown, path: string): string {
  const candidate = text(value, path)
  if (!/^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$/.test(candidate)
    || SECRET_LIKE_KEY.test(candidate)) {
    throw new TypeError(`${path} must be a safe dotted path`)
  }
  return candidate
}

function roles(value: unknown, path: string): IntegrityRole[] {
  if (!Array.isArray(value) || value.length === 0) throw new TypeError(`${path} must be a non-empty roles array`)
  const result = value.map((item, index): IntegrityRole => {
    const role = text(item, `${path}[${index}]`)
    if (role !== 'smoke' && role !== 'trial' && role !== 'champion') {
      throw new TypeError(`${path}[${index}] must be smoke, trial, or champion`)
    }
    return role
  })
  if (new Set(result).size !== result.length) throw new TypeError(`${path} must not contain duplicate roles`)
  return result
}

function uniqueStrings(value: unknown, path: string, parseItem: (item: unknown, path: string) => string): string[] {
  if (!Array.isArray(value)) throw new TypeError(`${path} must be an array`)
  const result = value.map((item, index) => parseItem(item, `${path}[${index}]`))
  if (new Set(result).size !== result.length) throw new TypeError(`${path} must not contain duplicates`)
  return result
}

function stringRecord(value: unknown, path: string): Record<string, string> {
  const input = record(value, path)
  const output: Record<string, string> = {}
  for (const [key, child] of Object.entries(input)) {
    if (SECRET_LIKE_KEY.test(key)) throw new TypeError(`${path} must not contain a secret-like field`)
    output[key] = text(child, `${path}.${key}`)
  }
  return output
}

function artifactPaths(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) throw new TypeError(`${path} must be an array`)
  const paths = value.map((item, index) => safeRelativePath(item, `${path}[${index}]`))
  if (new Set(paths).size !== paths.length) throw new TypeError(`${path} must not contain duplicate paths`)
  return paths
}

function provenanceSource(value: unknown, path: string): RunEvidenceSource {
  const input = record(value, path)
  const source = text(input['source'], `${path}.source`)
  exactFields(input, ['source', 'key'], path)
  if (source !== 'param' && source !== 'tag') {
    throw new TypeError(`${path}.source must be param or tag; provenance cannot rely on constants`)
  }
  return { source, key: safeEvidenceKey(input['key'], `${path}.key`) }
}

function provenance(value: unknown): RunProvenanceDeclaration | undefined {
  if (value === undefined) return undefined
  const input = record(value, 'spec.runEvidence.provenance')
  const fields = ['executionIdentity', 'project', 'release', 'submission', 'readiness', 'executionMode', 'promotable'] as const
  exactFields(input, fields, 'spec.runEvidence.provenance')
  return {
    executionIdentity: provenanceSource(input['executionIdentity'], 'spec.runEvidence.provenance.executionIdentity'),
    project: provenanceSource(input['project'], 'spec.runEvidence.provenance.project'),
    release: provenanceSource(input['release'], 'spec.runEvidence.provenance.release'),
    submission: provenanceSource(input['submission'], 'spec.runEvidence.provenance.submission'),
    readiness: provenanceSource(input['readiness'], 'spec.runEvidence.provenance.readiness'),
    executionMode: provenanceSource(input['executionMode'], 'spec.runEvidence.provenance.executionMode'),
    promotable: provenanceSource(input['promotable'], 'spec.runEvidence.provenance.promotable'),
  }
}

function runEvidence(value: unknown, compatibilityFields: readonly string[]): RunEvidenceDeclaration {
  const input = record(value, 'spec.runEvidence')
  exactFields(input, ['compatibility', 'provenance', 'requiredTags', 'stageArtifacts', 'modelSource'], 'spec.runEvidence')
  const sources = record(input['compatibility'], 'spec.runEvidence.compatibility')
  const compatibility: Record<string, RunEvidenceSource> = {}
  for (const field of compatibilityFields) {
    const source = record(sources[field], `spec.runEvidence.compatibility.${field}`)
    exactFields(source, source['source'] === 'constant' ? ['source', 'value'] : ['source', 'key'], `spec.runEvidence.compatibility.${field}`)
    if (source['source'] === 'constant') {
      compatibility[field] = {
        source: 'constant',
        value: text(source['value'], `spec.runEvidence.compatibility.${field}.value`),
      }
    } else if (source['source'] === 'param' || source['source'] === 'tag') {
      compatibility[field] = {
        source: source['source'],
        key: safeEvidenceKey(source['key'], `spec.runEvidence.compatibility.${field}.key`),
      }
    } else {
      throw new TypeError(`spec.runEvidence.compatibility.${field}.source is invalid`)
    }
  }
  const unknownSources = Object.keys(sources).filter(field => !compatibilityFields.includes(field))
  if (unknownSources.length > 0) {
    throw new TypeError(`spec.runEvidence.compatibility contains unknown fields: ${unknownSources.join(', ')}`)
  }
  const stages = record(input['stageArtifacts'], 'spec.runEvidence.stageArtifacts')
  exactFields(stages, ['training-optimization', 'final-validation'], 'spec.runEvidence.stageArtifacts')
  const modelSource = record(input['modelSource'], 'spec.runEvidence.modelSource')
  exactFields(modelSource, ['artifactPath', 'uriTag'], 'spec.runEvidence.modelSource')
  const provenanceDeclaration = provenance(input['provenance'])
  return {
    compatibility,
    ...(provenanceDeclaration === undefined ? {} : { provenance: provenanceDeclaration }),
    requiredTags: stringRecord(input['requiredTags'], 'spec.runEvidence.requiredTags'),
    stageArtifacts: {
      'training-optimization': artifactPaths(
        stages['training-optimization'],
        'spec.runEvidence.stageArtifacts.training-optimization',
      ),
      'final-validation': artifactPaths(
        stages['final-validation'],
        'spec.runEvidence.stageArtifacts.final-validation',
      ),
    },
    modelSource: {
      artifactPath: safeRelativePath(input['modelSource'] === undefined ? undefined : modelSource['artifactPath'], 'spec.runEvidence.modelSource.artifactPath'),
      uriTag: safeEvidenceKey(modelSource['uriTag'], 'spec.runEvidence.modelSource.uriTag'),
    },
  }
}

function integrityRunSource(value: unknown, path: string): IntegrityRunSource {
  const input = record(value, path)
  exactFields(input, ['source', 'key'], path)
  if (input['source'] !== 'param' && input['source'] !== 'tag') {
    throw new TypeError(`${path}.source must be param or tag`)
  }
  return { source: input['source'], key: safeEvidenceKey(input['key'], `${path}.key`) }
}

function integrityReport(value: unknown, path: string): IntegrityReportDeclaration {
  const input = record(value, path)
  exactFields(input, [
    'artifactPath', 'roles', 'statusPath', 'digestPath', 'statusSource', 'digestSource',
  ], path)
  const statusPath = safeDottedPath(input['statusPath'], `${path}.statusPath`)
  const digestPath = safeDottedPath(input['digestPath'], `${path}.digestPath`)
  if (statusPath !== 'status') throw new TypeError(`${path}.statusPath must be status`)
  if (digestPath !== 'content_digest') throw new TypeError(`${path}.digestPath must be content_digest`)
  return {
    artifactPath: safeArtifactPath(input['artifactPath'], `${path}.artifactPath`),
    roles: roles(input['roles'], `${path}.roles`),
    statusPath,
    digestPath,
    statusSource: integrityRunSource(input['statusSource'], `${path}.statusSource`),
    digestSource: integrityRunSource(input['digestSource'], `${path}.digestSource`),
  }
}

function integrity(value: unknown): ProjectIntegrityDeclaration | undefined {
  if (value === undefined) return undefined
  const input = record(value, 'spec.integrity')
  exactFields(input, [
    'planOutputPath', 'reports', 'preprocessing', 'migration', 'improvementBacklog',
  ], 'spec.integrity')
  const reportInput = record(input['reports'], 'spec.integrity.reports')
  exactFields(reportInput, ['preprocessing', 'migration'], 'spec.integrity.reports')
  const reports = {
    preprocessing: integrityReport(reportInput['preprocessing'], 'spec.integrity.reports.preprocessing'),
    migration: integrityReport(reportInput['migration'], 'spec.integrity.reports.migration'),
  }
  if (new Set(Object.values(reports).map(report => report.artifactPath)).size !== Object.keys(reports).length) {
    throw new TypeError('spec.integrity.reports Artifact paths must be unique')
  }

  const preprocessing = record(input['preprocessing'], 'spec.integrity.preprocessing')
  exactFields(preprocessing, ['contexts', 'comparisons'], 'spec.integrity.preprocessing')
  if (!Array.isArray(preprocessing['contexts'])) {
    throw new TypeError('spec.integrity.preprocessing.contexts must be an array')
  }
  const contexts = preprocessing['contexts'].map((value, index): PreprocessingContextDeclaration => {
    const path = `spec.integrity.preprocessing.contexts[${index}]`
    const item = record(value, path)
    exactFields(item, ['id', 'roles', 'outputPath'], path)
    return {
      id: safeEvidenceKey(item['id'], `${path}.id`),
      roles: roles(item['roles'], `${path}.roles`),
      outputPath: safeDottedPath(item['outputPath'], `${path}.outputPath`),
    }
  })
  rejectDuplicateIds(contexts, 'spec.integrity.preprocessing.contexts')

  if (!Array.isArray(preprocessing['comparisons'])) {
    throw new TypeError('spec.integrity.preprocessing.comparisons must be an array')
  }
  const comparisons = preprocessing['comparisons'].map((value, index): PreprocessingComparisonDeclaration => {
    const path = `spec.integrity.preprocessing.comparisons[${index}]`
    const item = record(value, path)
    exactFields(item, ['id', 'roles', 'checkPath', 'leftContext', 'rightContext', 'fields', 'required'], path)
    const result = {
      id: safeEvidenceKey(item['id'], `${path}.id`),
      roles: roles(item['roles'], `${path}.roles`),
      checkPath: safeDottedPath(item['checkPath'], `${path}.checkPath`),
      leftContext: safeEvidenceKey(item['leftContext'], `${path}.leftContext`),
      rightContext: safeEvidenceKey(item['rightContext'], `${path}.rightContext`),
      fields: uniqueStrings(item['fields'], `${path}.fields`, safeDottedPath),
      required: boolean(item['required'], `${path}.required`),
    }
    if (result.fields.length === 0) throw new TypeError(`${path}.fields must not be empty`)
    return result
  })
  rejectDuplicateIds(comparisons, 'spec.integrity.preprocessing.comparisons')
  const contextById = new Map(contexts.map(context => [context.id, context]))
  for (const comparison of comparisons) {
    const left = contextById.get(comparison.leftContext)
    const right = contextById.get(comparison.rightContext)
    if (left === undefined || right === undefined) {
      throw new TypeError(`spec.integrity preprocessing comparison ${comparison.id} references an unknown context`)
    }
    const uncoveredRoles = comparison.roles.filter(role => !left.roles.includes(role) || !right.roles.includes(role))
    if (uncoveredRoles.length > 0) {
      throw new TypeError(`spec.integrity preprocessing comparison ${comparison.id} contexts do not cover roles: ${uncoveredRoles.join(', ')}`)
    }
  }

  const migration = record(input['migration'], 'spec.integrity.migration')
  exactFields(migration, ['enabled', 'lineage', 'contaminationChecks'], 'spec.integrity.migration')
  const enabled = boolean(migration['enabled'], 'spec.integrity.migration.enabled')
  const lineageInput = record(migration['lineage'], 'spec.integrity.migration.lineage')
  exactFields(lineageInput, ['roles', 'outputPath', 'allowed', 'required'], 'spec.integrity.migration.lineage')
  const lineage: MigrationLineageDeclaration = {
    roles: roles(lineageInput['roles'], 'spec.integrity.migration.lineage.roles'),
    outputPath: safeDottedPath(lineageInput['outputPath'], 'spec.integrity.migration.lineage.outputPath'),
    allowed: uniqueStrings(lineageInput['allowed'], 'spec.integrity.migration.lineage.allowed', safeEvidenceKey),
    required: boolean(lineageInput['required'], 'spec.integrity.migration.lineage.required'),
  }
  if (!Array.isArray(migration['contaminationChecks'])) {
    throw new TypeError('spec.integrity.migration.contaminationChecks must be an array')
  }
  const contaminationChecks = migration['contaminationChecks'].map((value, index): IntegrityCheckDeclaration => {
    const path = `spec.integrity.migration.contaminationChecks[${index}]`
    const item = record(value, path)
    exactFields(item, ['id', 'roles', 'checkPath', 'required'], path)
    return {
      id: safeEvidenceKey(item['id'], `${path}.id`),
      roles: roles(item['roles'], `${path}.roles`),
      checkPath: safeDottedPath(item['checkPath'], `${path}.checkPath`),
      required: boolean(item['required'], `${path}.required`),
    }
  })
  rejectDuplicateIds(contaminationChecks, 'spec.integrity.migration.contaminationChecks')
  const checkIds = [
    ...comparisons.map(comparison => comparison.id),
    ...contaminationChecks.map(check => check.id),
    ...(enabled ? ['migration-lineage'] : []),
  ]
  if (new Set(checkIds).size !== checkIds.length) {
    throw new TypeError('spec.integrity check IDs must be unique across preprocessing, lineage, and migration')
  }

  let improvementBacklog: ImprovementBacklogDeclaration[] | undefined
  if (input['improvementBacklog'] !== undefined) {
    if (!Array.isArray(input['improvementBacklog'])) {
      throw new TypeError('spec.integrity.improvementBacklog must be an array')
    }
    improvementBacklog = input['improvementBacklog'].map((value, index): ImprovementBacklogDeclaration => {
      const path = `spec.integrity.improvementBacklog[${index}]`
      const item = record(value, path)
      exactFields(item, ['id', 'roles', 'outputPath', 'blocking'], path)
      const blocking = boolean(item['blocking'], `${path}.blocking`)
      if (blocking !== false) throw new TypeError(`${path}.blocking must be false`)
      return {
        id: safeEvidenceKey(item['id'], `${path}.id`),
        roles: roles(item['roles'], `${path}.roles`),
        outputPath: safeDottedPath(item['outputPath'], `${path}.outputPath`),
        blocking: false,
      }
    })
    rejectDuplicateIds(improvementBacklog, 'spec.integrity.improvementBacklog')
  }

  return {
    planOutputPath: safeDottedPath(input['planOutputPath'], 'spec.integrity.planOutputPath'),
    reports,
    preprocessing: { contexts, comparisons },
    migration: { enabled, lineage, contaminationChecks },
    ...(improvementBacklog === undefined ? {} : { improvementBacklog }),
  }
}

function rejectDuplicateIds(values: readonly { readonly id: string }[], path: string): void {
  const ids = values.map(value => value.id)
  if (new Set(ids).size !== ids.length) throw new TypeError(`${path} must not contain duplicate IDs`)
}

function gate(value: unknown, index: number): QualityGateDeclaration {
  const item = record(value, `spec.qualityGates[${index}]`)
  const source = text(item['source'], `spec.qualityGates[${index}].source`)
  if (source !== 'metric' && source !== 'evidence') throw new TypeError(`spec.qualityGates[${index}].source is invalid`)
  exactFields(item, ['name', 'source', 'key', 'operator', 'threshold', 'required'], `spec.qualityGates[${index}]`)
  const operator = text(item['operator'], `spec.qualityGates[${index}].operator`)
  if (!['exists', 'eq', 'neq', 'gt', 'gte', 'lt', 'lte'].includes(operator)) {
    throw new TypeError(`spec.qualityGates[${index}].operator is invalid`)
  }
  const threshold = item['threshold']
  if (operator !== 'exists' && !['string', 'number', 'boolean'].includes(typeof threshold)) {
    throw new TypeError(`spec.qualityGates[${index}].threshold is required`)
  }
  return {
    name: text(item['name'], `spec.qualityGates[${index}].name`),
    source,
    key: text(item['key'], `spec.qualityGates[${index}].key`),
    operator: operator as QualityGateDeclaration['operator'],
    ...(threshold === undefined ? {} : { threshold: threshold as string | number | boolean }),
    required: boolean(item['required'], `spec.qualityGates[${index}].required`),
  }
}

/** Validate and detach an untrusted project declaration. */
export function validateProjectManifest(value: unknown): TrainingProjectManifest {
  const root = record(value, 'manifest')
  if (root['apiVersion'] !== 'galatea/v1') throw new TypeError('apiVersion must be galatea/v1')
  if (root['kind'] !== 'TrainingProject') throw new TypeError('kind must be TrainingProject')
  const metadata = record(root['metadata'], 'metadata')
  const spec = record(root['spec'], 'spec')
  const objective = record(spec['objective'], 'spec.objective')
  exactFields(objective, ['metric', 'direction'], 'spec.objective')
  const direction = text(objective['direction'], 'spec.objective.direction')
  if (direction !== 'max' && direction !== 'min') throw new TypeError('spec.objective.direction must be max or min')
  if (!Array.isArray(spec['compatibility'])) throw new TypeError('spec.compatibility must be an array')
  const compatibility = spec['compatibility'].map((item, index) => text(item, `spec.compatibility[${index}]`))
  if (new Set(compatibility).size !== compatibility.length) {
    throw new TypeError('spec.compatibility must not contain duplicate fields')
  }
  for (const field of REQUIRED_COMPATIBILITY) {
    if (!compatibility.includes(field)) throw new TypeError(`spec.compatibility must include ${field}`)
  }
  const executionBackend = text(spec['executionBackend'], 'spec.executionBackend')
  if (executionBackend !== 'ray') throw new TypeError('spec.executionBackend must be ray')
  const packageName = text(spec['packageName'], 'spec.packageName')
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(packageName)) throw new TypeError('spec.packageName must be a Python package name')
  const capabilities = record(spec['capabilities'], 'spec.capabilities')
  const pauseResume = boolean(capabilities['pauseResume'], 'spec.capabilities.pauseResume')
  const checkpointEntrypoint = optionalArgvTemplate(
    capabilities['checkpointEntrypoint'],
    'spec.capabilities.checkpointEntrypoint',
    0,
  )
  const resumeEntrypoint = optionalArgvTemplate(
    capabilities['resumeEntrypoint'],
    'spec.capabilities.resumeEntrypoint',
    1,
  )
  if (pauseResume && (checkpointEntrypoint === undefined || resumeEntrypoint === undefined)) {
    throw new TypeError('pauseResume requires checkpointEntrypoint and resumeEntrypoint')
  }
  const entrypoints = record(spec['entrypoints'], 'spec.entrypoints')
  const mlflow = record(spec['mlflow'], 'spec.mlflow')
  if (!Array.isArray(spec['qualityGates'])) throw new TypeError('spec.qualityGates must be an array')
  const qualityGates = spec['qualityGates'].map(gate)
  const qualityGateNames = qualityGates.map(gate => gate.name)
  if (new Set(qualityGateNames).size !== qualityGateNames.length) {
    throw new TypeError('spec.qualityGates must not contain duplicate names')
  }
  const integrityDeclaration = integrity(spec['integrity'])
  return {
    apiVersion: 'galatea/v1',
    kind: 'TrainingProject',
    metadata: { name: text(metadata['name'], 'metadata.name') },
    spec: {
      task: text(spec['task'], 'spec.task'),
      executionBackend,
      packageName,
      objective: { metric: text(objective['metric'], 'spec.objective.metric'), direction },
      compatibility,
      capabilities: {
        pauseResume,
        ...(checkpointEntrypoint === undefined ? {} : { checkpointEntrypoint }),
        ...(resumeEntrypoint === undefined ? {} : { resumeEntrypoint }),
      },
      configRoot: safeRelativePath(spec['configRoot'], 'spec.configRoot'),
      entrypoints: {
        checkConfig: argvTemplate(entrypoints['checkConfig'], 'spec.entrypoints.checkConfig', 1),
        plan: argvTemplate(entrypoints['plan'], 'spec.entrypoints.plan', 1),
        train: argvTemplate(entrypoints['train'], 'spec.entrypoints.train', 1),
      },
      mlflow: {
        experimentName: text(mlflow['experimentName'], 'spec.mlflow.experimentName'),
        trackingUriEnv: text(mlflow['trackingUriEnv'], 'spec.mlflow.trackingUriEnv'),
        ...(mlflow['registeredModelName'] === undefined
          ? {}
          : { registeredModelName: text(mlflow['registeredModelName'], 'spec.mlflow.registeredModelName') }),
      },
      runEvidence: runEvidence(spec['runEvidence'], compatibility),
      qualityGates,
      ...(integrityDeclaration === undefined ? {} : { integrity: integrityDeclaration }),
    },
  }
}

export async function loadProjectManifest(path: string): Promise<TrainingProjectManifest> {
  return validateProjectManifest(parse(await readFile(path, 'utf8')))
}

/** Resolve an existing project path and reject lexical or symlink escapes. */
export async function resolveProjectPath(projectRoot: string, candidate: string): Promise<string> {
  const root = await realpath(projectRoot)
  const unresolved = resolve(root, candidate)
  const lexical = relative(root, unresolved)
  if (lexical === '..' || lexical.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) || isAbsolute(lexical)) {
    throw new Error(`path resolves outside project root: ${candidate}`)
  }
  const target = await realpath(unresolved)
  const within = relative(root, target)
  if (within === '..' || within.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) || isAbsolute(within)) {
    throw new Error(`path resolves outside project root: ${candidate}`)
  }
  return target
}
