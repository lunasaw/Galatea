import assert from 'node:assert/strict'
import { mkdtemp, mkdir, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import {
  loadProjectManifest,
  resolveProjectPath,
  validateProjectStructure,
  validateProjectManifest,
} from '../src/policies/project.ts'

const validManifest = {
  apiVersion: 'galatea/v1',
  kind: 'TrainingProject',
  metadata: { name: 'demo-project' },
  spec: {
    task: 'image-classification',
    executionBackend: 'ray',
    packageName: 'demo_project',
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

test('requires an explicit Ray execution backend', () => {
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, executionBackend: undefined },
  }), /executionBackend/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, executionBackend: 'local' },
  }), /executionBackend must be ray/)
})

test('rejects incomplete or cross-purpose workload roots', async () => {
  const root = await mkdtemp(join(tmpdir(), 'galatea-structure-'))
  await mkdir(join(root, 'configs'))
  await mkdir(join(root, 'src', 'demo_project'), { recursive: true })
  await mkdir(join(root, 'tests'))
  await mkdir(join(root, 'scripts'))
  await writeFile(join(root, 'README.md'), '# demo\n')
  await writeFile(join(root, 'galatea.project.yaml'), 'placeholder\n')
  await writeFile(join(root, 'conda.yaml'), 'name: demo\n')
  await writeFile(join(root, 'src', 'demo_project', '__init__.py'), '')
  await writeFile(join(root, 'configs', 'baseline.yaml'), 'run: {}\n')
  await writeFile(join(root, 'tests', 'test_project.py'), '')
  await writeFile(join(root, 'scripts', 'train.py'), '')
  const report = await validateProjectStructure(root, validateProjectManifest(validManifest))
  assert.equal(report.packageName, 'demo_project')
  await mkdir(join(root, 'src', 'wrong_package'), { recursive: true })
  await writeFile(join(root, 'src', 'wrong_package', '__init__.py'), '')
  await assert.rejects(
    () => validateProjectStructure(root, { ...validateProjectManifest(validManifest), metadata: { name: 'other-project' } }),
    /exactly one top-level Python package/,
  )
})

test('accepts old manifests with unknown optional integrity', () => {
  const manifest = validateProjectManifest(validManifest)
  assert.equal(manifest.spec.integrity, undefined)
})

test('validates framework-neutral integrity declarations defensively', () => {
  const integrity = {
    planOutputPath: 'readiness.integrity',
    reports: {
      preprocessing: {
        artifactPath: 'reports/preprocessing.json', roles: ['smoke', 'trial', 'champion'],
        statusPath: 'status', digestPath: 'content_digest',
        statusSource: { source: 'tag', key: 'integrity.preprocessing.status' },
        digestSource: { source: 'param', key: 'integrity.preprocessing_artifact_digest' },
      },
      migration: {
        artifactPath: 'reports/migration.json', roles: ['smoke', 'trial', 'champion'],
        statusPath: 'status', digestPath: 'content_digest',
        statusSource: { source: 'tag', key: 'integrity.migration.status' },
        digestSource: { source: 'param', key: 'integrity.migration_artifact_digest' },
      },
    },
    preprocessing: {
      contexts: [
        { id: 'train', roles: ['smoke', 'trial', 'champion'], outputPath: 'preprocessing.contexts.train' },
        { id: 'evaluate', roles: ['champion'], outputPath: 'preprocessing.contexts.evaluate' },
      ],
      comparisons: [{
        id: 'train-evaluate', roles: ['champion'], checkPath: 'preprocessing.checks.trainEvaluate',
        leftContext: 'train', rightContext: 'evaluate', fields: ['dtype', 'range.minimum', 'range.maximum'], required: true,
      }],
    },
    migration: {
      enabled: true,
      lineage: { roles: ['trial', 'champion'], outputPath: 'migration.lineage', allowed: ['clean-room', 'demo-template'], required: true },
      contaminationChecks: [{ id: 'old-task-names', roles: ['trial', 'champion'], checkPath: 'migration.checks.oldTaskNames', required: true }],
    },
    improvementBacklog: [{ id: 'platform-debt', roles: ['smoke', 'trial', 'champion'], outputPath: 'advisories', blocking: false }],
  } as const
  const manifest = validateProjectManifest({ ...validManifest, spec: { ...validManifest.spec, integrity } })
  assert.equal(manifest.spec.integrity?.migration.enabled, true)
  assert.equal(manifest.spec.integrity?.preprocessing.comparisons[0]?.required, true)
  assert.equal(manifest.spec.integrity?.improvementBacklog?.[0]?.blocking, false)

  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, integrity: { ...integrity, extra: true } },
  }), /unknown fields/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: { ...integrity, preprocessing: { ...integrity.preprocessing, contexts: [...integrity.preprocessing.contexts, integrity.preprocessing.contexts[0]] } },
    },
  }), /duplicate IDs/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, integrity: { ...integrity, improvementBacklog: [{ ...integrity.improvementBacklog[0], blocking: true }] } },
  }), /blocking must be false/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, integrity: { ...integrity, planOutputPath: '../secrets' } },
  }), /safe dotted path/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        reports: { ...integrity.reports, preprocessing: { ...integrity.reports.preprocessing, artifactPath: '../integrity.json' } },
      },
    },
  }), /Artifact path/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        reports: {
          ...integrity.reports,
          preprocessing: {
            ...integrity.reports.preprocessing,
            statusSource: { source: 'tag', key: 'authorization.token' },
          },
        },
      },
    },
  }), /secret-like evidence key/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, integrity: { ...integrity, reports: { preprocessing: integrity.reports.preprocessing } } },
  }), /reports\.migration/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        reports: { ...integrity.reports, migration: { ...integrity.reports.migration, artifactPath: 'reports/preprocessing.json' } },
      },
    },
  }), /must be unique/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        preprocessing: {
          ...integrity.preprocessing,
          comparisons: [{ ...integrity.preprocessing.comparisons[0], roles: ['production'] }],
        },
      },
    },
  }), /smoke, trial, or champion/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        preprocessing: {
          ...integrity.preprocessing,
          comparisons: [{ ...integrity.preprocessing.comparisons[0], id: 'old-task-names' }],
        },
      },
    },
  }), /check IDs must be unique/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: {
      ...validManifest.spec,
      integrity: {
        ...integrity,
        preprocessing: {
          contexts: integrity.preprocessing.contexts.map(context => context.id === 'evaluate'
            ? { ...context, roles: ['trial'] }
            : context),
          comparisons: integrity.preprocessing.comparisons,
        },
      },
    },
  }), /contexts do not cover roles/)
})

test('rejects duplicate compatibility fields and quality gate names', () => {
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, compatibility: [...validManifest.spec.compatibility, 'role'] },
  }), /duplicate fields/)
  assert.throws(() => validateProjectManifest({
    ...validManifest,
    spec: { ...validManifest.spec, qualityGates: [...validManifest.spec.qualityGates, validManifest.spec.qualityGates[0]] },
  }), /duplicate names/)
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
    '  executionBackend: ray',
    '  packageName: demo_project',
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
