import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { ToolExecution, PreToolDecision } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-session-projection'
import type {} from '@deepseek-ai/dsh-system-prompt'
import schema from '@deepseek-ai/schemastery'
import {
  adaptSingleProjectConfig,
  GalateaProjectRegistry,
  type ConfiguredProjectEntry,
} from './project-registry.ts'
import {
  currentProjectId,
  galateaProjectSelectionProjection,
  GALATEA_PROJECT_SELECTION_KEY,
  type GalateaProjectSelectionState,
} from './session-selection.ts'
import { MlflowService } from './services/mlflow.ts'
import { ProjectProcessService } from './services/project-process.ts'
import { RayJobsService } from './services/ray.ts'
import { GalateaController } from './tools/controller.ts'
import { createGalateaTools } from './tools/index.ts'

export const name = 'dsh-galatea'
export const inject = ['tools', 'approval', 'sessionProjections', 'systemPrompt']

/** Return a deterministic denial reason for shell attempts that bypass Ray governance. */
export function trainingCommandViolation(toolName: string, argumentsValue: unknown): string | undefined {
  if (!['bash', 'pwsh', 'terminal-bash', 'terminal-pwsh'].includes(toolName)) return undefined
  if (argumentsValue === null || typeof argumentsValue !== 'object' || Array.isArray(argumentsValue)) return undefined
  const command = (argumentsValue as Record<string, unknown>)['command']
  if (typeof command !== 'string') return undefined
  if (/\bray\s+(?:job|jobs)\s+submit\b/i.test(command)
    || /(?:^|[\s/])job\/(?:cd|submit)\.py\b[^;&|]*\b(?:--mode\s+)?train\b/i.test(command)
    || /\bpython(?:3(?:\.\d+)?)?\s+-m\s+ray_[A-Za-z0-9_.]+(?:\.train|\.job_release)\b/i.test(command)) {
    return 'direct Ray Jobs submission is disabled; use galatea_plan_run then galatea_submit_job'
  }
  const trainingSegments = command.split(/&&|\|\||[;|]/).filter(segment => /scripts[\\/]train\.py\b/i.test(segment))
  if (trainingSegments.some(segment => !/--(?:check-config|plan)\b/i.test(segment))) {
    return 'formal training scripts may not run through a shell; use galatea_plan_run then galatea_submit_job'
  }
  return undefined
}

export interface ProjectConfig {
  readonly id: string
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath?: string
}

export interface Config {
  readonly projectRoot?: string
  readonly manifestPath?: string
  readonly releaseRoot?: string
  readonly projects?: ProjectConfig[]
  readonly defaultProject?: string
  readonly projectSelectorEnv?: string
  readonly releaseSelectorEnv?: string
  readonly rayBaseUrl: string
  readonly rayTokenEnv?: string
  readonly mlflowBaseUrl: string
  readonly mlflowTokenEnv?: string
  readonly requestTimeoutMs?: number
  readonly projectProcessTimeoutMs?: number
  readonly maxResponseBytes?: number
  readonly maxArtifactBytes?: number
  readonly maxLogChars?: number
  readonly maxProcessOutputBytes?: number
  readonly projectProcessInheritedEnv?: string[]
  readonly approvalPolicy?: 'ask' | 'never'
}

function configuredPath(value: string, selectorEnv: string | undefined, path: string): string {
  if (selectorEnv === undefined || selectorEnv === '') return value
  if (!ENVIRONMENT_NAME.test(selectorEnv)) throw new TypeError(`${path} must be an environment variable name`)
  const selected = process.env[selectorEnv]
  return selected === undefined || selected.trim() === '' ? value : selected
}

export const Config: ReturnType<typeof schema<Config>> = schema.object({
  projectRoot: schema.string(),
  manifestPath: schema.string().default('galatea.project.yaml'),
  releaseRoot: schema.string(),
  projects: schema.array(schema.object({
    id: schema.string().required(),
    projectRoot: schema.string().required(),
    releaseRoot: schema.string().required(),
    manifestPath: schema.string(),
  })),
  defaultProject: schema.string(),
  projectSelectorEnv: schema.string(),
  releaseSelectorEnv: schema.string(),
  rayBaseUrl: schema.string().required(),
  rayTokenEnv: schema.string(),
  mlflowBaseUrl: schema.string().required(),
  mlflowTokenEnv: schema.string(),
  requestTimeoutMs: schema.natural().min(1).default(30_000),
  projectProcessTimeoutMs: schema.natural().min(1).default(60_000),
  maxResponseBytes: schema.natural().min(1).default(2_000_000),
  maxArtifactBytes: schema.natural().min(1).default(50_000_000),
  maxLogChars: schema.natural().min(1).default(100_000),
  maxProcessOutputBytes: schema.natural().min(1).default(1_000_000),
  projectProcessInheritedEnv: schema.array(schema.string()),
  approvalPolicy: schema.union(['ask', 'never']),
})

const ENVIRONMENT_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

function serviceUrl(value: string, path: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new TypeError(`${path} must be an absolute HTTP URL`)
  }
  if ((url.protocol !== 'http:' && url.protocol !== 'https:')
    || url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '') {
    throw new TypeError(`${path} must be an HTTP URL without credentials, query, or fragment`)
  }
  return url.toString().replace(/\/$/, '')
}

function tokenFromEnvironment(name: string | undefined, path: string): string | undefined {
  if (name === undefined || name === '') return undefined
  if (!ENVIRONMENT_NAME.test(name)) throw new TypeError(`${path} must be an environment variable name`)
  const value = process.env[name]
  if (value === undefined || value.trim() === '') {
    throw new Error(`${path} names an environment variable that is not set`)
  }
  return value
}

function approvalPolicy(agent: Agent | undefined, configured: Config['approvalPolicy']): string {
  if (agent === undefined) return configured ?? 'unknown'
  const events = agent.session.snapshotEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event?.type !== 'approval/policy') continue
    const data = event.data
    if (data !== null && typeof data === 'object' && !Array.isArray(data)) {
      const value = (data as Record<string, unknown>)['policy']
      if (value === 'ask' || value === 'never') return value
    }
  }
  return configured ?? 'unknown'
}

function configuredProjects(config: Config): readonly ConfiguredProjectEntry[] {
  if (config.projects !== undefined && config.projects.length > 0) return config.projects
  if (config.projectRoot === undefined || config.releaseRoot === undefined) {
    throw new TypeError('configure projects or both legacy projectRoot and releaseRoot')
  }
  return [adaptSingleProjectConfig({
    projectRoot: configuredPath(config.projectRoot, config.projectSelectorEnv, 'projectSelectorEnv'),
    releaseRoot: configuredPath(config.releaseRoot, config.releaseSelectorEnv, 'releaseSelectorEnv'),
    manifestPath: config.manifestPath ?? 'galatea.project.yaml',
  }, config.defaultProject ?? 'default')]
}

/** Mount the Galatea domain adapter and its administrator-configured project registry. */
export async function apply(ctx: Context, config: Config): Promise<void> {
  const timeoutMs = config.requestTimeoutMs ?? 30_000
  const maxResponseBytes = config.maxResponseBytes ?? 2_000_000
  const rayToken = tokenFromEnvironment(config.rayTokenEnv, 'rayTokenEnv')
  const mlflowToken = tokenFromEnvironment(config.mlflowTokenEnv, 'mlflowTokenEnv')
  const process = new ProjectProcessService({
    timeoutMs: config.projectProcessTimeoutMs ?? 60_000,
    maxOutputBytes: config.maxProcessOutputBytes ?? 1_000_000,
    ...(config.projectProcessInheritedEnv === undefined ? {} : { inheritedEnv: config.projectProcessInheritedEnv }),
  })
  const ray = new RayJobsService({
    baseUrl: serviceUrl(config.rayBaseUrl, 'rayBaseUrl'),
    ...(rayToken === undefined ? {} : { token: rayToken }),
    timeoutMs,
    maxResponseBytes,
    maxLogChars: config.maxLogChars ?? 100_000,
  })
  const mlflow = new MlflowService({
    baseUrl: serviceUrl(config.mlflowBaseUrl, 'mlflowBaseUrl'),
    ...(mlflowToken === undefined ? {} : { token: mlflowToken }),
    timeoutMs,
    maxResponseBytes,
    maxArtifactBytes: config.maxArtifactBytes ?? 50_000_000,
  })
  const registry = await GalateaProjectRegistry.create(configuredProjects(config), () => ({ process, ray, mlflow }))
  const summaries = registry.listSummaries()
  const defaultProject = config.defaultProject ?? summaries[0]?.id
  if (defaultProject === undefined || registry.getSummary(defaultProject) === undefined) {
    throw new TypeError('defaultProject must name one configured project')
  }
  ctx.sessionProjections.register(galateaProjectSelectionProjection)
  const liveSelections = new WeakMap<Agent['session'], GalateaProjectSelectionState>()
  ctx.on('tools/result', (exec, result) => {
    if (exec.agent === undefined || exec.name !== 'galatea_select_project' || result.isError) return
    const projectId = (result.value as { readonly data?: { readonly selectedProjectId?: unknown } }).data?.selectedProjectId
    if (typeof projectId !== 'string' || registry.getSummary(projectId) === undefined) return
    liveSelections.set(exec.agent.session, { projectId, pendingNative: [] })
  })
  const selectedFor = (agent: Agent | undefined): string | undefined => {
    if (agent === undefined) return undefined
    return currentProjectId(
      liveSelections.get(agent.session)
      ?? ctx.sessionProjections.stateOf(agent.session, GALATEA_PROJECT_SELECTION_KEY),
      projectId => registry.getSummary(projectId) !== undefined,
    )
  }
  const controllerFor = async (agent: Agent | undefined): Promise<GalateaController> => {
    return await registry.getController(selectedFor(agent) ?? defaultProject)
  }
  const controller = await registry.getController(defaultProject)

  // The Harness tool pipeline is the last executable boundary available to a
  // plugin. Deny the known bypasses here so the prompt is backed by an
  // enforceable policy even when the model chooses a generic shell tool.
  ctx.on('tools/pre-execute', (exec: ToolExecution, next: () => Promise<PreToolDecision>) => {
    const reason = trainingCommandViolation(exec.name, exec.arguments)
    return reason === undefined ? next() : Promise.resolve({ kind: 'deny', reason } as const)
  }, { prepend: true })

  ctx.systemPrompt.section({
    name: 'tool:galatea',
    order: ctx.systemPrompt.getSectionOrder('TOOL_CORDIS') + 1,
    text: [
      '## Galatea training execution',
      'Start by listing and selecting the administrator-configured project, then inspect it before planning.',
      'A new algorithm, dataset, or task is a new workload: use a separate train-model/<project-name> root with its own README.md, configs/, src/<matching-python-package>/, scripts/, tests/, environment file, and galatea.project.yaml. Never repurpose another project (especially ray-cats-and-dogs) for a different workload.',
      'The project registry is administrator-owned. If no registered project matches the requested workload, stop before editing or training and ask the administrator to provision/register a new project; do not use a near match as a substitute.',
      'Choose execution by task scope: bounded quick checks and low-risk exploratory experiments may run locally; formal Trial/Champion runs, long-running or resource-intensive training, and any run intended for durable MLflow/Kaggle evidence must use the declared Ray Job path. A local result must never be presented as a governed Ray result or as final evidence.',
      'For a declared Ray project, use galatea_plan_run followed by galatea_submit_job for formal execution. Before any training starts, validate the project structure, fixed entrypoint, config, dependencies, release manifest, data/split identity, and plan evidence; a mismatch is a blocking failure, not an advisory. Do not use an ad-hoc shell command to bypass a failed preflight.',
      'Before submitting, confirm the selected project task, executionBackend, project structure, config, release manifest, and plan evidence. A governed result is incomplete without the Ray submission ID, MLflow Run ID, readiness/evidence digest, and artifact evidence.',
      'Paths are relative: configPath is below projectRoot and releaseManifestPath is below releaseRoot.',
      'Prioritize the requested training objective; record nonblocking platform improvements for later instead of interrupting training.',
      'Treat Ray execution, model quality, integrity evidence, and governance approval as independent states.',
      'Before Champion, require the project plan to prove declared preprocessing parity and contamination checks.',
      'Use status-only Job observations after the first log read and continue with nextLogCursor; fetch full logs only on failure or terminal evidence collection.',
      'Approval-disabled sessions cannot submit, resume, or promote through governed tools. Never promote automatically.',
    ].join('\n'),
  })

  for (const tool of createGalateaTools({
    controller,
    controllerFor,
    listProjects: async (agent) => ({
      selectedProjectId: selectedFor(agent) ?? defaultProject,
      projects: summaries.map(summary => ({
        id: summary.id,
        manifestName: summary.manifestName,
        task: summary.task,
        objective: summary.objective,
        experimentName: summary.experimentName,
      })),
      selectionScope: 'session',
    }),
    selectProject: async (agent, projectId) => {
      if (agent === undefined) throw new Error('a live Harness Agent is required')
      const selected = registry.getSummary(projectId)
      if (selected === undefined) throw new Error(`unknown Galatea project id: ${projectId}`)
      return {
        selectedProjectId: projectId,
        project: {
          id: selected.id,
          manifestName: selected.manifestName,
          task: selected.task,
          objective: selected.objective,
          experimentName: selected.experimentName,
        },
        selectionScope: 'session',
      }
    },
    approvalPolicy: agent => approvalPolicy(agent, config.approvalPolicy),
    approval: ctx.approval,
  })) {
    ctx.tools.register(tool)
  }
}
