import type { Context } from '@deepseek-ai/cordis'
import schema from '@deepseek-ai/schemastery'
import { findStageApproval, type StageApprovalDecision } from '@deepseek-ai/dsh-user-approval'
import { answerStageApproval } from './approval/answerer.ts'
import { loadProjectManifest, resolveProjectPath } from './policies/project.ts'
import { MlflowService } from './services/mlflow.ts'
import { ProjectProcessService } from './services/project-process.ts'
import { RayJobsService } from './services/ray.ts'
import { GalateaController } from './tools/controller.ts'
import { createGalateaTools } from './tools/index.ts'

export const name = 'dsh-galatea'
export const inject = ['tools', 'approval', 'userQuestions']

export interface Config {
  readonly projectRoot: string
  readonly manifestPath: string
  readonly releaseRoot: string
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
}

export const Config = schema.object({
  projectRoot: schema.string().required(),
  manifestPath: schema.string().default('galatea.project.yaml'),
  releaseRoot: schema.string().required(),
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

/** Mount the stateless Galatea domain adapter into one Harness Cordis context. */
export async function apply(ctx: Context, config: Config): Promise<void> {
  const projectRoot = await resolveProjectPath(config.projectRoot, '.')
  const releaseRoot = await resolveProjectPath(config.releaseRoot, '.')
  const manifestPath = await resolveProjectPath(projectRoot, config.manifestPath)
  const manifest = await loadProjectManifest(manifestPath)
  const timeoutMs = config.requestTimeoutMs ?? 30_000
  const maxResponseBytes = config.maxResponseBytes ?? 2_000_000
  const rayToken = tokenFromEnvironment(config.rayTokenEnv, 'rayTokenEnv')
  const mlflowToken = tokenFromEnvironment(config.mlflowTokenEnv, 'mlflowTokenEnv')

  const controller = new GalateaController({
    projectRoot,
    releaseRoot,
    manifest,
    process: new ProjectProcessService({
      timeoutMs: config.projectProcessTimeoutMs ?? 60_000,
      maxOutputBytes: config.maxProcessOutputBytes ?? 1_000_000,
    }),
    ray: new RayJobsService({
      baseUrl: serviceUrl(config.rayBaseUrl, 'rayBaseUrl'),
      ...(rayToken === undefined ? {} : { token: rayToken }),
      timeoutMs,
      maxResponseBytes,
      maxLogChars: config.maxLogChars ?? 100_000,
    }),
    mlflow: new MlflowService({
      baseUrl: serviceUrl(config.mlflowBaseUrl, 'mlflowBaseUrl'),
      ...(mlflowToken === undefined ? {} : { token: mlflowToken }),
      timeoutMs,
      maxResponseBytes,
      maxArtifactBytes: config.maxArtifactBytes ?? 50_000_000,
    }),
  })

  for (const tool of createGalateaTools({
    controller,
    approval: ctx.approval,
    approvalFromSession: (agent, subject) => findStageApproval(agent.session.events, subject),
  })) {
    ctx.tools.register(tool)
  }

  ctx.on('approval/stage-request', async request => answerStageApproval({
    request,
    ask: ({ questions }) => ctx.userQuestions.ask({
      questions,
      agent: request.agent,
      ...(request.signal === undefined ? {} : { signal: request.signal }),
    }),
  }) as Promise<StageApprovalDecision>)
}

export default { name, inject, Config, apply }
