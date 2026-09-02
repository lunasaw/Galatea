import assert from 'node:assert/strict'
import { mkdtemp, mkdir, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import {
  adaptSingleProjectConfig,
  GalateaProjectRegistry,
  parseProjectEntries,
  parseProjectEntriesYaml,
  type GalateaControllerServices,
} from '../src/project-registry.ts'

const manifest = {
  apiVersion: 'galatea/v1',
  kind: 'TrainingProject',
  metadata: { name: 'digits' },
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
    mlflow: { experimentName: 'digits', trackingUriEnv: 'MLFLOW_TRACKING_URI' },
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
      requiredTags: { 'run.outcome': 'succeeded' },
      stageArtifacts: {
        'training-optimization': ['reports/model-selection.json'],
        'final-validation': ['reports/final-test-evaluation.json', 'model/MLmodel'],
      },
      modelSource: { artifactPath: 'model', uriTag: 'model.uri' },
    },
    qualityGates: [],
  },
} as const

async function projectFiles(): Promise<{ projectRoot: string; releaseRoot: string }> {
  const parent = await mkdtemp(join(tmpdir(), 'galatea-registry-'))
  const projectRoot = join(parent, 'project')
  const releaseRoot = join(parent, 'release')
  await mkdir(join(projectRoot, 'configs'), { recursive: true })
  await mkdir(releaseRoot)
  await writeFile(join(projectRoot, 'galatea.project.yaml'), JSON.stringify(manifest), 'utf8')
  return { projectRoot, releaseRoot }
}

test('parses entries, adapts legacy config, and rejects unsafe or duplicate ids', () => {
  const entry = adaptSingleProjectConfig({
    projectRoot: '/srv/projects/digits',
    releaseRoot: '/srv/releases/digits',
  }, 'digits')
  assert.deepEqual(entry, {
    id: 'digits',
    projectRoot: '/srv/projects/digits',
    releaseRoot: '/srv/releases/digits',
    manifestPath: 'galatea.project.yaml',
  })
  assert.deepEqual(parseProjectEntriesYaml(`projects:\n  - id: digits\n    projectRoot: /srv/projects/digits\n    releaseRoot: /srv/releases/digits\n`), [entry])
  assert.throws(() => parseProjectEntries([
    entry,
    { ...entry },
  ]), /duplicate id/)
  assert.throws(() => parseProjectEntries([{ ...entry, id: '../escape' }]), /id must/)
  assert.throws(() => parseProjectEntries([{ ...entry, manifestPath: '../secret.yaml' }]), /manifestPath/)
  assert.throws(() => parseProjectEntries([{ ...entry, projectRoot: 'relative' }]), /absolute path/)
})

test('resolves canonical roots, loads manifests, constructs controllers, and lists summaries', async () => {
  const files = await projectFiles()
  const registry = await GalateaProjectRegistry.create([
    { id: 'digits', ...files },
  ], () => ({}) as GalateaControllerServices)
  assert.deepEqual(registry.listSummaries(), [{
    id: 'digits',
    manifestName: 'digits',
    task: 'image-classification',
    objective: { metric: 'val_accuracy', direction: 'max' },
    experimentName: 'digits',
    projectRoot: files.projectRoot,
    releaseRoot: files.releaseRoot,
    manifestPath: join(files.projectRoot, 'galatea.project.yaml'),
  }])
  const controller = await registry.getController('digits')
  assert.equal(controller.projectRoot, files.projectRoot)
  assert.equal(await registry.getController('digits'), controller)
  await assert.rejects(() => registry.getController('missing'), /unknown Galatea project id/)
})

test('rejects a manifest symlink that escapes the project root', async () => {
  const files = await projectFiles()
  const outside = join(await mkdtemp(join(tmpdir(), 'galatea-registry-outside-')), 'outside.yaml')
  await writeFile(outside, JSON.stringify(manifest), 'utf8')
  await symlink(outside, join(files.projectRoot, 'escaped.yaml'))
  await assert.rejects(
    () => GalateaProjectRegistry.create([{ id: 'digits', ...files, manifestPath: 'escaped.yaml' }], () => ({}) as GalateaControllerServices),
    /outside project root/,
  )
})

test('stores project selection through the durable session store', async () => {
  const files = await projectFiles()
  const registry = await GalateaProjectRegistry.create([{ id: 'digits', ...files }], () => ({}) as GalateaControllerServices)
  const values = new Map<string, string | null>()
  const selector = registry.selector({
    read: sessionId => values.get(sessionId),
    write: (sessionId, projectId) => { values.set(sessionId, projectId) },
  })
  const session = { id: 'session-1' }
  assert.equal(await selector.selectedProject(session), undefined)
  assert.equal((await selector.selectProject(session, 'digits')).id, 'digits')
  assert.equal(values.get('session-1'), 'digits')
  assert.equal((await selector.selectedProject(session))?.manifestName, 'digits')
  await selector.clearProject(session)
  assert.equal(await selector.selectedProject(session), undefined)
  values.set('session-1', 'unknown')
  await assert.rejects(() => selector.selectedProject(session), /unknown Galatea project id/)
})
