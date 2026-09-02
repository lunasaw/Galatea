import { describe, expect, it } from 'vitest'
import { createGalateaTools, GALATEA_TOOL_NAMES } from '../src/tools/index.ts'

function controller() {
  return {
    async inspectProject() { return { ok: true, data: { project: 'demo' }, summary: 'ok' } },
    async patchConfig() { return { ok: true, data: { changed: true }, summary: 'ok' } },
    async planRun() {
      return {
        ok: true,
        data: { evidence: { stage: 'readiness', artifactId: 'ready-1', digest: 'sha256:ready' } },
        summary: 'ok',
      }
    },
    async submitJob(input: { approval?: unknown; candidateApproval?: unknown }) {
      expect(input.approval).toEqual({
        valid: true,
        stage: 'readiness',
        artifactId: 'ready-1',
        evidenceDigest: 'sha256:ready',
      })
      if (input.candidateApproval !== undefined) {
        expect(input.candidateApproval).toEqual({
          valid: true,
          stage: 'training-optimization',
          artifactId: 'trial-2',
          evidenceDigest: 'sha256:candidate',
        })
      }
      return { ok: true, data: { submissionId: 'job-1' }, summary: 'ok' }
    },
    async observeJob() { return { ok: true, data: { status: 'RUNNING' }, summary: 'ok' } },
    async stopJob() { return { ok: true, data: { stopped: true }, summary: 'ok' } },
    async pauseJob() { return { ok: false, error: { category: 'unsupported', message: 'unsupported', retryable: false, stateChanged: false } } },
    async planResume() {
      return {
        ok: true,
        data: { evidence: { stage: 'readiness', artifactId: 'resume-1', digest: 'sha256:resume' } },
        summary: 'ok',
      }
    },
    async resumeJob(input: { approval?: unknown }) {
      expect(input.approval).toEqual({
        valid: true,
        stage: 'readiness',
        artifactId: 'resume-1',
        evidenceDigest: 'sha256:resume',
      })
      return { ok: true, data: { submissionId: 'job-2' }, summary: 'ok' }
    },
    async compareRuns(input: { referenceRunId: string; experimentIds?: unknown }) {
      expect(input).toEqual({ referenceRunId: 'trial-1', signal })
      return { ok: true, data: { rankedRunIds: [] }, summary: 'ok' }
    },
    async buildStageEvidence() {
      return {
        ok: true,
        data: { evidence: { stage: 'training-optimization', artifactId: 'trial-2', digest: 'sha256:candidate' } },
        summary: 'ok',
      }
    },
    async verifyCandidate() {
      return {
        ok: true,
        data: { evidence: { stage: 'final-validation', artifactId: 'champion-1', digest: 'sha256:final', qualityGatesPassed: true } },
        summary: 'ok',
      }
    },
    async promoteModel(input: { approval?: unknown }) {
      expect(input.approval).toEqual({
        valid: true,
        stage: 'final-validation',
        artifactId: 'champion-1',
        evidenceDigest: 'sha256:final',
      })
      return { ok: true, data: { version: '1' }, summary: 'ok' }
    },
  }
}

function context(valid = true) {
  const requested: unknown[] = []
  return {
    requested,
    approval: {
      async requestStage(input: unknown) {
        requested.push(input)
        return { outcome: 'approved', approver: 'reviewer', comment: 'ok', decidedAt: 1, expiresAt: 2 }
      },
    },
    approvalFromSession: () => valid
      ? { valid: true as const, decision: { outcome: 'approved' as const, expiresAt: 2 } }
      : { valid: false as const, reason: 'not-found' as const },
  }
}

const signal = new AbortController().signal
const agent = { session: { events: [] } } as never

describe('dsh-galatea Harness tools', () => {
  it('defines the complete bounded surface through Harness defineTool', () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
      approvalFromSession: ctx.approvalFromSession as never,
    })
    expect(tools.map(tool => tool.name)).toEqual(GALATEA_TOOL_NAMES)
    expect(new Set(tools.map(tool => tool.name)).size).toBe(tools.length)
  })

  it('scopes Run comparison to the manifest Experiment instead of model-supplied IDs', async () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
      approvalFromSession: ctx.approvalFromSession as never,
    })
    await tools.find(tool => tool.name === 'galatea_compare_runs')!.execute({
      referenceRunId: 'trial-1',
    }, { signal, agent } as never)
  })

  it('derives state-changing approval from Session replay', async () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
      approvalFromSession: ctx.approvalFromSession as never,
    })
    await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
      configPath: 'configs/trial.yaml', releaseManifestPath: 'release/release.json', role: 'trial', attempt: 'a1',
    }, { signal, agent } as never)
    await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
      configPath: 'configs/champion.yaml',
      releaseManifestPath: 'release/release.json',
      role: 'champion',
      attempt: 'champion-1',
      candidateRunId: 'trial-2',
    }, { signal, agent } as never)
    await tools.find(tool => tool.name === 'galatea_promote_model')!.execute({
      runId: 'champion-1', alias: 'champion', idempotencyKey: 'promote-1',
    }, { signal, agent } as never)
    await tools.find(tool => tool.name === 'galatea_resume_job')!.execute({
      originalSubmissionId: 'job-1',
      configPath: 'configs/trial.yaml',
      releaseManifestPath: 'release/release.json',
      checkpoint: { runId: 'trial-1', path: 'checkpoints/state.json', digest: 'sha256:checkpoint' },
      attempt: 'resume-1',
    }, { signal, agent } as never)
  })

  it('fails closed without a matching Session approval', async () => {
    const ctx = context(false)
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
      approvalFromSession: ctx.approvalFromSession as never,
    })
    const result = await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
      configPath: 'configs/trial.yaml', releaseManifestPath: 'release/release.json', role: 'trial', attempt: 'a1',
    }, { signal, agent } as never) as { ok: boolean; error?: { category?: string } }
    expect(result).toMatchObject({ ok: false, error: { category: 'approval-required' } })
  })
})
