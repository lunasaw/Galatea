import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import SessionStore from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import ApprovalService from '@deepseek-ai/dsh-user-approval'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import * as GalateaPlugin from '../src/index.ts'
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
  await ctx.plugin(SessionProjectionRegistry)
  await ctx.plugin(ApprovalService, { policy: 'ask' })
  return ctx
}

describe('dsh-galatea Cordis plugin', () => {
  it('survives the real Loader export path, registers every tool, and disposes them', async () => {
    expect('default' in GalateaPlugin).toBe(false)
    const loader = Object.create(Loader.prototype) as Loader
    const unwrapped = loader.unwrapExports(GalateaPlugin) as Parameters<Context['plugin']>[0]
    expect(unwrapped).toBe(GalateaPlugin)
    expect(GalateaPlugin.inject).toEqual(['tools', 'approval', 'sessionProjections', 'systemPrompt'])

    const ctx = await harness()
    const fiber = await ctx.plugin(unwrapped, await projectConfig())

    expect(GALATEA_TOOL_NAMES.every(name => ctx.tools.get(name) !== undefined)).toBe(true)

    await fiber.dispose()
    expect(GALATEA_TOOL_NAMES.every(name => ctx.tools.get(name) === undefined)).toBe(true)
  })
})
