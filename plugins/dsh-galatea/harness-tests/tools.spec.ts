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

function context() {
  const requested: unknown[] = []
  return {
    requested,
    approval: {
      async request(input: unknown) {
        requested.push(input)
        return 'allowed-once'
      },
    },
  }
}

const signal = new AbortController().signal
const agent = { session: { events: [] } } as never
const callId = 'call-galatea-1'

describe('dsh-galatea Harness tools', () => {
  it('defines the complete bounded surface through Harness defineTool', () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
    })
    expect(tools.map(tool => tool.name)).toEqual(GALATEA_TOOL_NAMES)
    expect(new Set(tools.map(tool => tool.name)).size).toBe(tools.length)
  })

  it('scopes Run comparison to the manifest Experiment instead of model-supplied IDs', async () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
    })
    await tools.find(tool => tool.name === 'galatea_compare_runs')!.execute({
      referenceRunId: 'trial-1',
    }, { signal, agent } as never)
  })

  it('requests one-time approval before each state-changing action', async () => {
    const ctx = context()
    const tools = createGalateaTools({
      controller: controller() as never,
      approval: ctx.approval as never,
    })
    await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
      configPath: 'configs/trial.yaml', releaseManifestPath: 'release/release.json', role: 'trial', attempt: 'a1',
    }, { signal, agent, callId } as never)
    await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
      configPath: 'configs/champion.yaml',
      releaseManifestPath: 'release/release.json',
      role: 'champion',
      attempt: 'champion-1',
      candidateRunId: 'trial-2',
    }, { signal, agent, callId } as never)
    await tools.find(tool => tool.name === 'galatea_promote_model')!.execute({
      runId: 'champion-1', alias: 'champion', idempotencyKey: 'promote-1',
    }, { signal, agent, callId } as never)
    await tools.find(tool => tool.name === 'galatea_resume_job')!.execute({
      originalSubmissionId: 'job-1',
      configPath: 'configs/trial.yaml',
      releaseManifestPath: 'release/release.json',
      checkpoint: { runId: 'trial-1', path: 'checkpoints/state.json', digest: 'sha256:checkpoint' },
      attempt: 'resume-1',
    }, { signal, agent, callId } as never)
    const requests = ctx.requested as {
      agent?: unknown
      toolName?: string
      callId?: string
      reason?: string
      signal?: AbortSignal
    }[]
    expect(requests.map(request => request.toolName)).toEqual([
      'galatea_submit_job',
      'galatea_submit_job',
      'galatea_submit_job',
      'galatea_promote_model',
      'galatea_resume_job',
    ])
    for (const request of requests) {
      expect(request.agent).toBe(agent)
      expect(request.callId).toBe(callId)
      expect(request.signal).toBe(signal)
      expect(request.reason).toMatch(/Evidence digest: sha256:/)
    }
  })

  it('fails closed for every non-grant outcome and approval errors', async () => {
    for (const outcome of ['rejected', 'cancelled', 'unavailable', 'error'] as const) {
      const ctx = context()
      ctx.approval.request = outcome === 'error'
        ? async () => { throw new Error('answerer failed') }
        : async () => outcome
      const tools = createGalateaTools({
        controller: controller() as never,
        approval: ctx.approval as never,
      })
      const result = await tools.find(tool => tool.name === 'galatea_submit_job')!.execute({
        configPath: 'configs/trial.yaml', releaseManifestPath: 'release/release.json', role: 'trial', attempt: 'a1',
      }, { signal, agent, callId } as never) as { ok: boolean; error?: { category?: string } }
      expect(result, outcome).toMatchObject({ ok: false, error: { category: 'approval-required' } })
    }
  })
})
