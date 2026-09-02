import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  compareRuns,
  selectBestRun,
  type ComparableRun,
} from '../src/policies/comparability.ts'
import { evaluateQualityGates } from '../src/policies/quality-gates.ts'
import {
  authorizeTransition,
  authorizeDatasetAccess,
} from '../src/policies/lifecycle.ts'

const identity = {
  task: 'image-classification',
  datasetDigest: 'sha256:data',
  splitDigest: 'sha256:split',
  preprocessingVersion: 'v1',
  metricDefinition: 'accuracy@sample',
  evaluationProtocol: 'fixed-v1',
  role: 'trial',
}

const runs: ComparableRun[] = [
  { runId: 'a', identity, metrics: { val_accuracy: 0.8 } },
  { runId: 'b', identity, metrics: { val_accuracy: 0.9 } },
]

test('Run comparison reports every incompatible identity field', () => {
  assert.deepEqual(compareRuns(runs[0]!, {
    runId: 'changed',
    identity: { ...identity, splitDigest: 'sha256:other', role: 'champion' },
    metrics: { val_accuracy: 1 },
  }), {
    comparable: false,
    reasons: ['splitDigest differs', 'role differs'],
  })
})

test('best Run selection sorts only compatible Runs in the declared direction', () => {
  const incompatible: ComparableRun = {
    runId: 'leaked-test',
    identity: { ...identity, role: 'champion' },
    metrics: { val_accuracy: 1 },
  }
  const result = selectBestRun([...runs, incompatible], { metric: 'val_accuracy', direction: 'max' })
  assert.equal(result.best?.runId, 'b')
  assert.deepEqual(result.ranked.map(run => run.runId), ['b', 'a'])
  assert.deepEqual(result.rejected, [{ runId: 'leaked-test', reasons: ['role differs'] }])
})

test('quality gates distinguish passed, failed, missing, and optional evidence', () => {
  const result = evaluateQualityGates([
    { name: 'accuracy', source: 'metric', key: 'test_accuracy', operator: 'gte', threshold: 0.9, required: true },
    { name: 'loss', source: 'metric', key: 'test_loss', operator: 'lt', threshold: 0.2, required: true },
    { name: 'report', source: 'evidence', key: 'reportDigest', operator: 'exists', required: true },
    { name: 'optional-roc', source: 'metric', key: 'roc_auc', operator: 'gte', threshold: 0.9, required: false },
  ], {
    metrics: { test_accuracy: 0.91, test_loss: 0.25 },
    evidence: { reportDigest: 'sha256:report' },
  })

  assert.equal(result.passed, false)
  assert.deepEqual(result.results.map(item => item.status), ['passed', 'failed', 'passed', 'skipped'])
})

test('Trial stages cannot read final test data and promotion requires matching final evidence approval', () => {
  assert.deepEqual(authorizeDatasetAccess('trial', 'test'), {
    allowed: false,
    reason: 'trial role cannot access the final test split',
  })
  assert.deepEqual(authorizeDatasetAccess('champion', 'test'), { allowed: true })

  assert.deepEqual(authorizeTransition({
    to: 'final-validation',
    evidence: { stage: 'readiness', artifactId: 'ready-9', digest: 'sha256:ready' },
    approval: { valid: true, stage: 'readiness', artifactId: 'ready-9', evidenceDigest: 'sha256:ready' },
  }), {
    allowed: false,
    reasons: ['final-validation requires training-optimization evidence'],
  })
  assert.deepEqual(authorizeTransition({
    to: 'promotion',
    evidence: { stage: 'final-validation', artifactId: 'evidence-9', digest: 'sha256:current', qualityGatesPassed: true },
    approval: { valid: true, stage: 'final-validation', artifactId: 'evidence-9', evidenceDigest: 'sha256:old' },
  }), {
    allowed: false,
    reasons: ['approval evidence digest does not match current evidence'],
  })
  assert.deepEqual(authorizeTransition({
    to: 'promotion',
    evidence: { stage: 'final-validation', artifactId: 'evidence-9', digest: 'sha256:current', qualityGatesPassed: true },
    approval: { valid: true, stage: 'final-validation', artifactId: 'evidence-9', evidenceDigest: 'sha256:current' },
  }), { allowed: true, reasons: [] })
})
