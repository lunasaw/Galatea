import assert from 'node:assert/strict'
import { mkdtemp, mkdir, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import {
  loadProjectManifest,
  resolveProjectPath,
  validateProjectManifest,
} from '../src/policies/project.ts'

const validManifest = {
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
    mlflow: { experimentName: 'demo-project', trackingUriEnv: 'MLFLOW_TRACKING_URI' },
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
        'final-validation': [
          'reports/final-test-evaluation.json',
          'model/MLmodel',
        ],
      },
      modelSource: { artifactPath: 'model', uriTag: 'model.uri' },
    },
    qualityGates: [{ name: 'accuracy', source: 'metric', key: 'test_accuracy', operator: 'gte', threshold: 0.9, required: true }],
  },
} as const

test('validates a framework-neutral project declaration', () => {
  const manifest = validateProjectManifest(validManifest)
  assert.equal(manifest.spec.objective.metric, 'val_accuracy')
  assert.equal(manifest.spec.objective.direction, 'max')
  assert.equal(manifest.spec.capabilities.pauseResume, false)
  assert.deepEqual(manifest.spec.runEvidence.compatibility.role, { source: 'tag', key: 'run.role' })
})

test('validates fixed checkpoint and resume entrypoints for resumable projects', () => {
  const manifest = validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      capabilities: {
        pauseResume: true,
        checkpointEntrypoint: ['python', 'scripts/checkpoint.py'],
        resumeEntrypoint: ['python', 'scripts/train.py', '--config', '{config}', '--resume'],
      },
    },
  })
  assert.deepEqual(manifest.spec.capabilities.checkpointEntrypoint, ['python', 'scripts/checkpoint.py'])
  assert.deepEqual(manifest.spec.capabilities.resumeEntrypoint, [
    'python', 'scripts/train.py', '--config', '{config}', '--resume',
  ])

  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      capabilities: {
        pauseResume: true,
        checkpointEntrypoint: ['python', 'scripts/checkpoint.py', '{submission_id}'],
        resumeEntrypoint: ['python', 'scripts/train.py', '--resume'],
      },
    },
  }), /checkpointEntrypoint|resumeEntrypoint/)
})

test('rejects shell strings, guessed objective direction, and unsafe paths', () => {
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, entrypoints: { ...validManifest.spec.entrypoints, train: 'python train.py' } },
  }), /entrypoints\.train/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, objective: { metric: 'loss' } },
  }), /objective\.direction/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, configRoot: '../outside' },
  }), /configRoot/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      runEvidence: {
        ...validManifest.spec.runEvidence,
        compatibility: {
          ...validManifest.spec.runEvidence.compatibility,
          role: { source: 'tag', key: 'authorization' },
        },
      },
    },
  }), /secret-like/i)
})

test('loads YAML and confines resolved paths to the project root, including symlinks', async () => {
  const root = await mkdtemp(join(tmpdir(), 'galatea-project-'))
  await mkdir(join(root, 'configs'))
  await writeFile(join(root, 'galatea.project.yaml'), [
    'apiVersion: galatea/v1',
    'kind: TrainingProject',
    'metadata:',
    '  name: demo-project',
    'spec:',
    '  task: image-classification',
    '  objective: {metric: val_accuracy, direction: max}',
    '  compatibility: [task, datasetDigest, splitDigest, preprocessingVersion, metricDefinition, evaluationProtocol, role]',
    '  capabilities: {pauseResume: false}',
    '  configRoot: configs',
    '  entrypoints:',
    '    checkConfig: [python, scripts/train.py, --config, "{config}", --check-config]',
    '    plan: [python, scripts/train.py, --config, "{config}", --plan]',
    '    train: [python, scripts/train.py, --config, "{config}"]',
    '  mlflow: {experimentName: demo-project, trackingUriEnv: MLFLOW_TRACKING_URI}',
    '  runEvidence:',
    '    compatibility:',
    '      task: {source: constant, value: image-classification}',
    '      datasetDigest: {source: param, key: data.content_sha256}',
    '      splitDigest: {source: param, key: data.split_sha256}',
    '      preprocessingVersion: {source: param, key: data.preprocessing_version}',
    '      metricDefinition: {source: constant, value: accuracy-v1}',
    '      evaluationProtocol: {source: constant, value: fixed-holdout-v1}',
    '      role: {source: tag, key: run.role}',
    '    requiredTags: {run.outcome: succeeded, artifact.roundtrip_verified: "true"}',
    '    stageArtifacts:',
    '      training-optimization: [reports/model-selection.json]',
    '      final-validation: [reports/final-test-evaluation.json, model/MLmodel]',
    '    modelSource: {artifactPath: model, uriTag: model.uri}',
    '  qualityGates: []',
  ].join('\n'))

  const loaded = await loadProjectManifest(join(root, 'galatea.project.yaml'))
  assert.equal(loaded.metadata.name, 'demo-project')
  assert.equal(await resolveProjectPath(root, 'configs'), join(root, 'configs'))
  await assert.rejects(resolveProjectPath(root, '../outside'), /outside project root/)
  await symlink(tmpdir(), join(root, 'configs', 'escape'))
  await assert.rejects(resolveProjectPath(root, 'configs/escape'), /outside project root/)
})
