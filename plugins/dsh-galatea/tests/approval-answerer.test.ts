import assert from 'node:assert/strict'
import { test } from 'node:test'
import { answerStageApproval } from '../src/approval/answerer.ts'

const request = {
  stage: 'final-validation',
  artifactId: 'run-1',
  evidenceDigest: 'sha256:evidence',
  summary: 'All required quality gates passed.',
} as const

test('collects a complete approved decision with bounded validity', async () => {
  const decision = await answerStageApproval({
    request,
    now: () => 1_000,
    ask: async input => {
      assert.equal(input.questions.length, 4)
      assert.equal(input.questions[0]?.detail?.includes('sha256:evidence'), true)
      return {
        answers: [
          { id: 'decision', selected: ['Approve'] },
          { id: 'approver', selected: [], custom: 'reviewer@example.com' },
          { id: 'comment', selected: [], custom: 'Reviewed artifacts and metrics.' },
          { id: 'validity', selected: ['24 hours'] },
        ],
      }
    },
  })
  assert.deepEqual(decision, {
    outcome: 'approved',
    approver: 'reviewer@example.com',
    comment: 'Reviewed artifacts and metrics.',
    decidedAt: 1_000,
    expiresAt: 86_401_000,
  })
})

test('fails closed on missing decision metadata or malformed answers', async () => {
  const incomplete = await answerStageApproval({
    request,
    now: () => 2_000,
    ask: async () => ({
      answers: [
        { id: 'decision', selected: ['Approve'] },
        { id: 'approver', selected: [], custom: '' },
        { id: 'comment', selected: [], custom: 'ok' },
        { id: 'validity', selected: ['24 hours'] },
      ],
    }),
  })
  assert.deepEqual(incomplete, { outcome: 'unavailable', decidedAt: 2_000 })

  const malformed = await answerStageApproval({
    request,
    now: () => 3_000,
    ask: async () => ({ answers: [{ id: 'decision', selected: ['Something else'] }] }),
  })
  assert.deepEqual(malformed, { outcome: 'unavailable', decidedAt: 3_000 })
})

test('records rejection and requested changes without granting validity', async () => {
  for (const [label, outcome] of [
    ['Reject', 'rejected'],
    ['Request changes', 'changes-requested'],
  ] as const) {
    const decision = await answerStageApproval({
      request,
      now: () => 4_000,
      ask: async () => ({
        answers: [
          { id: 'decision', selected: [label] },
          { id: 'approver', selected: [], custom: 'reviewer' },
          { id: 'comment', selected: [], custom: 'Needs follow-up.' },
          { id: 'validity', selected: ['24 hours'] },
        ],
      }),
    })
    assert.deepEqual(decision, {
      outcome,
      approver: 'reviewer',
      comment: 'Needs follow-up.',
      decidedAt: 4_000,
    })
  }
})
