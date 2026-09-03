import assert from 'node:assert/strict'
import { test } from 'node:test'
import { deriveIntegrityAdvisories, evaluatePlanIntegrity } from '../src/policies/integrity.ts'
import type { ProjectIntegrityDeclaration } from '../src/policies/project.ts'

const declaration: ProjectIntegrityDeclaration = {
  planOutputPath: 'readiness.integrity',
  reports: {
    preprocessing: {
      artifactPath: 'reports/preprocessing.json', roles: ['trial', 'champion'],
      statusPath: 'status', digestPath: 'content_digest',
      statusSource: { source: 'tag', key: 'integrity.preprocessing.status' },
      digestSource: { source: 'param', key: 'integrity.preprocessing_artifact_digest' },
    },
    migration: {
      artifactPath: 'reports/migration.json', roles: ['trial', 'champion'],
      statusPath: 'status', digestPath: 'content_digest',
      statusSource: { source: 'tag', key: 'integrity.migration.status' },
      digestSource: { source: 'param', key: 'integrity.migration_artifact_digest' },
    },
  },
  preprocessing: {
    contexts: [
      { id: 'train', roles: ['trial', 'champion'], outputPath: 'preprocessing.contexts.train' },
      { id: 'evaluate', roles: ['champion'], outputPath: 'preprocessing.contexts.evaluate' },
    ],
    comparisons: [{
      id: 'train-evaluate', roles: ['champion'], checkPath: 'preprocessing.checks.trainEvaluate',
      leftContext: 'train', rightContext: 'evaluate', fields: ['dtype', 'shape.channels', 'range.minimum', 'range.maximum'], required: true,
    }],
  },
  migration: {
    enabled: true,
    lineage: { roles: ['trial', 'champion'], outputPath: 'migration.lineage', allowed: ['clean-room'], required: true },
    contaminationChecks: [{ id: 'legacy-semantics', roles: ['trial', 'champion'], checkPath: 'migration.checks.legacySemantics', required: true }],
  },
  improvementBacklog: [{ id: 'platform-debt', roles: ['smoke', 'trial', 'champion'], outputPath: 'advisories', blocking: false }],
}

function plan(input?: { readonly maximum?: number; readonly lineage?: string; readonly contamination?: string }): unknown {
  return {
    readiness: {
      integrity: {
        status: 'passed',
        reportDigest: `sha256:${'a'.repeat(64)}`,
        preprocessing: {
          contexts: {
            train: { dtype: 'float32', shape: { channels: 1 }, range: { minimum: 0, maximum: 1 } },
            evaluate: { dtype: 'float32', shape: { channels: 1 }, range: { minimum: 0, maximum: input?.maximum ?? 1 } },
          },
          checks: { trainEvaluate: { status: 'passed' } },
        },
        migration: {
          lineage: input?.lineage ?? 'clean-room',
          checks: { legacySemantics: { status: input?.contamination ?? 'passed', reason: 'manifest scan completed' } },
        },
        advisories: ['reload the optional GUI plugin after training'],
      },
    },
  }
}

test('manifests without integrity fail governed readiness closed', () => {
  const result = evaluatePlanIntegrity(undefined, 'trial', {})
  assert.equal(result.preprocessing.status, 'unknown')
  assert.equal(result.migration.status, 'unknown')
  assert.equal(result.passed, false)
  assert.match(result.advisories[0] ?? '', /does not declare/)
})

test('normalizes passing project-reported plan integrity and advisories', () => {
  const result = evaluatePlanIntegrity(declaration, 'champion', plan())
  assert.equal(result.preprocessing.status, 'passed')
  assert.equal(result.migration.status, 'passed')
  assert.equal(result.passed, true)
  assert.equal(result.evidence.reports?.preprocessing, 'reports/preprocessing.json')
  assert.equal(result.evidence.reports?.migration, 'reports/migration.json')
  assert.match(result.evidence.reportDigest ?? '', /^sha256:/)
  assert.deepEqual(result.advisories, ['reload the optional GUI plugin after training'])
  assert.deepEqual(deriveIntegrityAdvisories(result), result.advisories)
})

test('fails required parity, lineage, and contamination checks', () => {
  const result = evaluatePlanIntegrity(declaration, 'champion', plan({ maximum: 255, lineage: 'copied-unknown', contamination: 'failed' }))
  assert.equal(result.preprocessing.status, 'failed')
  assert.equal(result.migration.status, 'failed')
  assert.equal(result.passed, false)
  assert.match(result.preprocessing.checks[0]?.reason ?? '', /range.maximum/)
  const advisories = deriveIntegrityAdvisories(result)
  assert.equal(advisories.some(value => value.includes('migration-lineage')), true)
  assert.equal(advisories.some(value => value.includes('legacy-semantics')), true)
})

test('does not accept missing fields, required not-applicable, or an overall reported failure', () => {
  const missingFields = plan() as { readiness: { integrity: Record<string, unknown> } }
  const preprocessing = missingFields.readiness.integrity['preprocessing'] as { contexts: { train: Record<string, unknown>; evaluate: Record<string, unknown> }; checks: { trainEvaluate: Record<string, unknown> } }
  preprocessing.contexts.train = {}
  preprocessing.contexts.evaluate = {}
  assert.equal(evaluatePlanIntegrity(declaration, 'champion', missingFields).passed, false)

  preprocessing.contexts.train = { dtype: 'float32', shape: { channels: 1 }, range: { minimum: 0, maximum: 1 } }
  preprocessing.contexts.evaluate = { dtype: 'float32', shape: { channels: 1 }, range: { minimum: 0, maximum: 1 } }
  preprocessing.checks.trainEvaluate['status'] = 'not-applicable'
  assert.equal(evaluatePlanIntegrity(declaration, 'champion', missingFields).passed, false)

  preprocessing.checks.trainEvaluate['status'] = 'passed'
  missingFields.readiness.integrity['status'] = 'failed'
  assert.equal(evaluatePlanIntegrity(declaration, 'champion', missingFields).passed, false)
})

test('compares structured preprocessing values by content', () => {
  const structured = plan() as { readiness: { integrity: Record<string, unknown> } }
  const preprocessing = structured.readiness.integrity['preprocessing'] as { contexts: { train: Record<string, unknown>; evaluate: Record<string, unknown> } }
  preprocessing.contexts.train['shape'] = { channels: 1, dimensions: [32, 32] }
  preprocessing.contexts.evaluate['shape'] = { channels: 1, dimensions: [32, 32] }
  const withShape: ProjectIntegrityDeclaration = {
    ...declaration,
    preprocessing: {
      ...declaration.preprocessing,
      comparisons: [{ ...declaration.preprocessing.comparisons[0]!, fields: ['dtype', 'shape'] }],
    },
  }
  assert.equal(evaluatePlanIntegrity(withShape, 'champion', structured).preprocessing.status, 'passed')
})

test('returns role-aware not-applicable and unknown statuses without guessing', () => {
  const smoke = evaluatePlanIntegrity(declaration, 'smoke', plan())
  assert.equal(smoke.preprocessing.status, 'not-applicable')
  assert.equal(smoke.migration.status, 'not-applicable')
  assert.equal(smoke.passed, true)

  const missing = evaluatePlanIntegrity(declaration, 'champion', {})
  assert.equal(missing.preprocessing.status, 'unknown')
  assert.equal(missing.migration.status, 'unknown')
  assert.equal(missing.passed, false)
})
