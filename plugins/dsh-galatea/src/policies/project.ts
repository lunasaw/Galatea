import { readFile, realpath } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { parse } from 'yaml'

export type ObjectiveDirection = 'max' | 'min'
export type ProjectEntrypoint = readonly string[]

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

export interface RunEvidenceDeclaration {
  readonly compatibility: Readonly<Record<string, RunEvidenceSource>>
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

const SECRET_LIKE_KEY = /(?:^|[._-])(?:authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key)(?:$|[._-])/i

function safeEvidenceKey(value: unknown, path: string): string {
  const key = text(value, path)
  if (SECRET_LIKE_KEY.test(key)) throw new TypeError(`${path} must not reference a secret-like field`)
  return key
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

function runEvidence(value: unknown, compatibilityFields: readonly string[]): RunEvidenceDeclaration {
  const input = record(value, 'spec.runEvidence')
  const sources = record(input['compatibility'], 'spec.runEvidence.compatibility')
  const compatibility: Record<string, RunEvidenceSource> = {}
  for (const field of compatibilityFields) {
    const source = record(sources[field], `spec.runEvidence.compatibility.${field}`)
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
  const modelSource = record(input['modelSource'], 'spec.runEvidence.modelSource')
  return {
    compatibility,
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

function gate(value: unknown, index: number): QualityGateDeclaration {
  const item = record(value, `spec.qualityGates[${index}]`)
  const source = text(item['source'], `spec.qualityGates[${index}].source`)
  if (source !== 'metric' && source !== 'evidence') throw new TypeError(`spec.qualityGates[${index}].source is invalid`)
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
  const direction = text(objective['direction'], 'spec.objective.direction')
  if (direction !== 'max' && direction !== 'min') throw new TypeError('spec.objective.direction must be max or min')
  if (!Array.isArray(spec['compatibility'])) throw new TypeError('spec.compatibility must be an array')
  const compatibility = spec['compatibility'].map((item, index) => text(item, `spec.compatibility[${index}]`))
  for (const field of REQUIRED_COMPATIBILITY) {
    if (!compatibility.includes(field)) throw new TypeError(`spec.compatibility must include ${field}`)
  }
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
  return {
    apiVersion: 'galatea/v1',
    kind: 'TrainingProject',
    metadata: { name: text(metadata['name'], 'metadata.name') },
    spec: {
      task: text(spec['task'], 'spec.task'),
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
      qualityGates: spec['qualityGates'].map(gate),
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
