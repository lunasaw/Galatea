import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import AgentRegistry, { type Agent } from '@deepseek-ai/dsh-agent'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import ApprovalService from '@deepseek-ai/dsh-user-approval'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import GalateaPlugin from '../src/index.ts'
import { GALATEA_TOOL_NAMES } from '../src/tools/index.ts'

const roots: string[] = []

afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })))
})

async function projectConfig() {
  const root = await mkdtemp(join(tmpdir(), 'dsh-galatea-plugin-'))
  roots.push(root)
  const projectRoot = join(root, 'project')
  const releaseRoot = join(root, 'releases')
  await mkdir(join(projectRoot, 'configs'), { recursive: true })
  await mkdir(releaseRoot, { recursive: true })
  await writeFile(join(projectRoot, 'configs', 'baseline.yaml'), 'run:\n  role: trial\n')
  await writeFile(join(projectRoot, 'galatea.project.yaml'), `
apiVersion: galatea/v1
kind: TrainingProject
metadata:
  name: harness-test
spec:
  task: image-classification
  objective: { metric: val_accuracy, direction: max }
  compatibility:
    - task
    - datasetDigest
    - splitDigest
    - preprocessingVersion
    - metricDefinition
    - evaluationProtocol
    - role
  capabilities: { pauseResume: false }
  configRoot: configs
  entrypoints:
    checkConfig: [python, scripts/train.py, --config, "{config}", --check-config]
    plan: [python, scripts/train.py, --config, "{config}", --plan]
    train: [python, scripts/train.py, --config, "{config}"]
  mlflow:
    experimentName: harness-test
    trackingUriEnv: MLFLOW_TRACKING_URI
    registeredModelName: harness-test
  runEvidence:
    compatibility:
      task: { source: constant, value: image-classification }
      datasetDigest: { source: param, key: data.content_sha256 }
      splitDigest: { source: param, key: data.split_sha256 }
      preprocessingVersion: { source: param, key: data.preprocessing_version }
      metricDefinition: { source: constant, value: accuracy-v1 }
      evaluationProtocol: { source: constant, value: fixed-holdout-v1 }
      role: { source: tag, key: run.role }
    requiredTags:
      run.outcome: succeeded
      artifact.roundtrip_verified: "true"
    stageArtifacts:
      training-optimization: [reports/model-selection.json]
      final-validation: [reports/final-test-evaluation.json, model/MLmodel]
    modelSource: { artifactPath: model, uriTag: model.uri }
  qualityGates:
    - { name: final accuracy, source: metric, key: test_accuracy, operator: gte, threshold: 0.9, required: true }
`)
  return {
    projectRoot,
    releaseRoot,
    manifestPath: 'galatea.project.yaml',
    rayBaseUrl: 'http://127.0.0.1:8265',
    mlflowBaseUrl: 'http://127.0.0.1:5000',
  }
}

async function harness() {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(ToolRuntime, { mode: 'native' })
  await ctx.plugin(SessionStore)
  await ctx.plugin(AgentRegistry)
  await ctx.plugin(ApprovalService, { policy: 'ask' })
  await ctx.plugin(UserQuestionService)
  return ctx
}

function liveAgent(ctx: Context, id: string): Agent {
  const sessionId = SessionId(id)
  const session = ctx.sessions.create(sessionId)
  session.append('turn/start', { turn: 1 })
  const agent = { id: sessionId, session, ctx } as unknown as Agent
  ctx.agents.enter(agent, undefined)
  return agent
}

describe('dsh-galatea Cordis plugin', () => {
  it('registers all tools and removes them with its fiber', async () => {
    const ctx = await harness()
    const fiber = await ctx.plugin(GalateaPlugin, await projectConfig())

    expect(GALATEA_TOOL_NAMES.every(name => ctx.tools.get(name) !== undefined)).toBe(true)

    await fiber.dispose()
    expect(GALATEA_TOOL_NAMES.every(name => ctx.tools.get(name) === undefined)).toBe(true)
  })

  it('answers stage approval through userQuestions with the live agent and signal', async () => {
    const ctx = await harness()
    await ctx.plugin(GalateaPlugin, await projectConfig())
    const agent = liveAgent(ctx, 'galatea-approval')
    const signal = new AbortController().signal
    const ask = vi.fn(async request => ({
      answers: [
        { id: 'decision', selected: ['Approve'] },
        { id: 'approver', selected: [], custom: 'reviewer@example.com' },
        { id: 'comment', selected: [], custom: 'Evidence reviewed.' },
        { id: 'validity', selected: ['24 hours'] },
      ],
    }))
    ctx.userQuestions.registerProvider({ ask })

    const decision = await ctx.approval.requestStage({
      agent,
      stage: 'final-validation',
      artifactId: 'candidate-7',
      evidenceDigest: 'sha256:abc',
      summary: 'All required quality gates passed.',
      signal,
    })

    expect(decision).toMatchObject({
      outcome: 'approved',
      approver: 'reviewer@example.com',
      comment: 'Evidence reviewed.',
    })
    expect(ask).toHaveBeenCalledOnce()
    expect(ask.mock.calls[0]?.[0].agent).toBe(agent)
    expect(ask.mock.calls[0]?.[0].signal).toBe(signal)
    expect(ask.mock.calls[0]?.[0].questions).toHaveLength(4)
  })

  it('fails closed for absent providers, cancelled requests, and incomplete reviewer identity', async () => {
    const ctx = await harness()
    await ctx.plugin(GalateaPlugin, await projectConfig())
    const agent = liveAgent(ctx, 'galatea-fail-closed')
    const request = {
      agent,
      stage: 'readiness',
      artifactId: 'ready-1',
      evidenceDigest: 'sha256:ready',
      summary: 'Readiness evidence.',
    }

    await expect(ctx.approval.requestStage(request)).resolves.toMatchObject({ outcome: 'unavailable' })

    ctx.userQuestions.registerProvider({
      async ask() {
        return {
          answers: [
            { id: 'decision', selected: ['Approve'] },
            { id: 'comment', selected: [], custom: 'Missing approver.' },
            { id: 'validity', selected: ['1 hour'] },
          ],
        }
      },
    })
    await expect(ctx.approval.requestStage(request)).resolves.toMatchObject({ outcome: 'unavailable' })
    await expect(ctx.approval.requestStage({
      ...request,
      signal: AbortSignal.abort(),
    })).resolves.toMatchObject({ outcome: 'cancelled' })
  })
})
