import type { Agent } from '@deepseek-ai/dsh-agent'
import { defineTool, type JsonValue as HarnessJsonValue, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import type {
  StageApprovalDecision,
  StageApprovalRequest,
  StageApprovalValidation,
} from '@deepseek-ai/dsh-user-approval'
import type { JsonValue, ToolResult } from '../contracts/index.ts'
import { failure, redactSecrets, success } from '../contracts/index.ts'
import type { ApprovalReference, ExecutionRole, LifecycleStage } from '../policies/lifecycle.ts'
import type { GalateaController, StageEvidence } from './controller.ts'

export const GALATEA_TOOL_NAMES = [
  'galatea_inspect_project',
  'galatea_patch_config',
  'galatea_plan_run',
  'galatea_submit_job',
  'galatea_observe_job',
  'galatea_stop_job',
  'galatea_pause_job',
  'galatea_resume_job',
  'galatea_compare_runs',
  'galatea_build_stage_evidence',
  'galatea_request_stage_approval',
  'galatea_verify_candidate',
  'galatea_promote_model',
] as const

export interface GalateaToolContext {
  readonly controller: GalateaController
  readonly approval: {
    requestStage(request: StageApprovalRequest): Promise<StageApprovalDecision>
  }
  readonly approvalFromSession: (
    agent: Agent,
    subject: { readonly stage: string; readonly artifactId: string; readonly evidenceDigest: string },
  ) => StageApprovalValidation
}

const output = {
  schema: { type: 'json' as const },
  render: (_args: unknown, value: HarnessJsonValue) => [{ type: 'text' as const, text: JSON.stringify(value) }],
}

function safeResult<T extends JsonValue>(result: ToolResult<T>): HarnessJsonValue {
  return redactSecrets(result) as HarnessJsonValue
}

function requireAgent(agent: Agent | undefined): ToolResult<never> | undefined {
  if (agent !== undefined) return undefined
  return failure({
    category: 'approval-required',
    message: 'a live Harness Agent is required to validate durable stage approval',
    retryable: false,
    stateChanged: false,
  })
}

function approvalReference(
  context: GalateaToolContext,
  agent: Agent,
  evidence: StageEvidence,
): ApprovalReference | undefined {
  const validation = context.approvalFromSession(agent, {
    stage: evidence.stage,
    artifactId: evidence.artifactId,
    evidenceDigest: evidence.digest,
  })
  return validation.valid
    ? {
      valid: true,
      stage: evidence.stage,
      artifactId: evidence.artifactId,
      evidenceDigest: evidence.digest,
    }
    : undefined
}

function missingApproval(evidence: StageEvidence): HarnessJsonValue {
  return safeResult(failure({
    category: 'approval-required',
    message: `no valid unexpired approval matches ${evidence.stage} evidence ${evidence.digest}`,
    retryable: false,
    stateChanged: false,
    nextAction: 'Request approval for the current evidence digest.',
  }))
}

/** Build the bounded model-facing surface over one stateless Controller. */
export function createGalateaTools(context: GalateaToolContext): ToolDefinition[] {
  const role = { type: 'string', enum: ['smoke', 'trial', 'champion'], required: true } as const
  const configPath = { type: 'string', required: true, description: 'Project-relative YAML path below configRoot.' } as const
  const releaseManifestPath = { type: 'string', required: true, description: 'Path below the configured immutable release root.' } as const
  const submissionId = { type: 'string', required: true } as const
  const tools: ToolDefinition[] = []

  tools.push(defineTool({
    name: 'galatea_inspect_project',
    description: 'Inspect the configured training project contract and declared capabilities.',
    parameters: {}, output,
    async execute() { return safeResult(await context.controller.inspectProject()) },
  }))
  tools.push(defineTool({
    name: 'galatea_patch_config',
    description: 'Apply structured YAML changes below configRoot, validate them through the project entrypoint, and roll back invalid changes.',
    parameters: {
      configPath,
      patches: {
        type: 'array', required: true, items: {
          type: 'object', additionalProperties: false,
          properties: {
            path: { type: 'array', items: { type: 'string' }, required: true },
            value: { type: 'json', required: true },
          },
        },
      },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      return safeResult(await context.controller.patchConfig({
        configPath: args.configPath,
        patches: args.patches as unknown as readonly { readonly path: readonly string[]; readonly value: JsonValue }[],
        signal: exec.signal,
      }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_plan_run',
    description: 'Run the declared read-only preflight and build readiness evidence without starting training.',
    parameters: { configPath, releaseManifestPath, role, attempt: { type: 'string', required: true } }, output,
    async execute(args, exec) {
      return safeResult(await context.controller.planRun({ ...args, role: args.role as ExecutionRole, signal: exec.signal }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_submit_job',
    description: 'Submit the fixed declared training entrypoint after readiness approval; Champion additionally requires approved training-optimization evidence.',
    parameters: {
      configPath,
      releaseManifestPath,
      role,
      attempt: { type: 'string', required: true },
      candidateRunId: {
        type: 'string',
        description: 'Selected Trial Run; required for role=champion and forbidden for other roles.',
      },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const agentFailure = requireAgent(exec.agent)
      if (agentFailure !== undefined) return safeResult(agentFailure)
      const planned = await context.controller.planRun({ ...args, role: args.role as ExecutionRole, signal: exec.signal })
      if (!planned.ok) return safeResult(planned)
      const approval = approvalReference(context, exec.agent!, planned.data.evidence)
      if (approval === undefined) return missingApproval(planned.data.evidence)
      let candidateApproval: ApprovalReference | undefined
      if (args.role === 'champion') {
        if (args.candidateRunId === undefined) {
          return safeResult(failure({
            category: 'approval-required',
            message: 'champion submission requires a selected Trial candidate Run',
            retryable: false,
            stateChanged: false,
            nextAction: 'Build and approve training-optimization evidence for the selected Trial Run.',
          }))
        }
        const candidate = await context.controller.buildStageEvidence({
          runId: args.candidateRunId,
          stage: 'training-optimization',
          signal: exec.signal,
        })
        if (!candidate.ok) return safeResult(candidate)
        candidateApproval = approvalReference(context, exec.agent!, candidate.data.evidence)
        if (candidateApproval === undefined) return missingApproval(candidate.data.evidence)
      } else if (args.candidateRunId !== undefined) {
        return safeResult(failure({
          category: 'invalid-input',
          message: 'candidateRunId applies only to champion submission',
          retryable: false,
          stateChanged: false,
        }))
      }
      return safeResult(await context.controller.submitJob({
        ...args,
        role: args.role as ExecutionRole,
        approval,
        ...(candidateApproval === undefined ? {} : { candidateApproval }),
        signal: exec.signal,
      }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_observe_job',
    description: 'Read a Ray Job status and optionally its bounded logs.',
    parameters: { submissionId, includeLogs: { type: 'boolean' } }, output,
    async execute(args, exec) { return safeResult(await context.controller.observeJob({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_stop_job',
    description: 'Stop one Ray Job with an explicit reason and idempotency identity.',
    parameters: {
      submissionId,
      reason: { type: 'string', required: true },
      idempotencyKey: { type: 'string', required: true },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) { return safeResult(await context.controller.stopJob({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_pause_job',
    description: 'Request a checkpoint-backed pause; unsupported projects fail without stopping the Job.',
    parameters: { submissionId, reason: { type: 'string', required: true } }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      return safeResult(await context.controller.pauseJob({ ...args, signal: exec.signal }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_resume_job',
    description: 'Create a lineage-linked replacement Job from a verified durable checkpoint when the project declares support.',
    parameters: {
      originalSubmissionId: { type: 'string', required: true }, configPath, releaseManifestPath,
      checkpoint: {
        type: 'object', additionalProperties: false, required: true,
        properties: {
          runId: { type: 'string', required: true },
          path: { type: 'string', required: true },
          digest: { type: 'string', required: true },
        },
      },
      attempt: { type: 'string', required: true },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const agentFailure = requireAgent(exec.agent)
      if (agentFailure !== undefined) return safeResult(agentFailure)
      const planned = await context.controller.planResume({ ...args, signal: exec.signal })
      if (!planned.ok) return safeResult(planned)
      const approval = approvalReference(context, exec.agent!, planned.data.evidence)
      if (approval === undefined) return missingApproval(planned.data.evidence)
      return safeResult(await context.controller.resumeJob({
        ...args,
        approval,
        signal: exec.signal,
      }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_compare_runs',
    description: 'Rank only compatible successful MLflow Runs by the declared objective and direction.',
    parameters: {
      referenceRunId: { type: 'string', required: true },
    }, output,
    async execute(args, exec) { return safeResult(await context.controller.compareRuns({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_build_stage_evidence',
    description: 'Re-read a successful trial Run and its declared Artifacts to build immutable training-optimization evidence.',
    parameters: {
      runId: { type: 'string', required: true },
      stage: { type: 'string', const: 'training-optimization', required: true },
    }, output,
    async execute(args, exec) { return safeResult(await context.controller.buildStageEvidence({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_request_stage_approval',
    description: 'Ask the human to approve, reject, or request changes for one exact evidence digest and persist the decision in the Harness Session.',
    parameters: {
      stage: { type: 'string', enum: ['readiness', 'training-optimization', 'final-validation', 'promotion'], required: true },
      artifactId: { type: 'string', required: true },
      evidenceDigest: { type: 'string', required: true },
      summary: { type: 'string', required: true },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const agentFailure = requireAgent(exec.agent)
      if (agentFailure !== undefined) return safeResult(agentFailure)
      const decision = await context.approval.requestStage({ ...args, agent: exec.agent!, signal: exec.signal })
      return safeResult(success({
        stage: args.stage as LifecycleStage,
        artifactId: args.artifactId,
        evidenceDigest: args.evidenceDigest,
        decision: decision as unknown as JsonValue,
      }, `Stage approval resolved as ${decision.outcome}.`))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_verify_candidate',
    description: 'Re-read a champion Run and declared Artifacts, evaluate quality gates, and build final-validation evidence.',
    parameters: { runId: { type: 'string', required: true } }, output,
    async execute(args, exec) { return safeResult(await context.controller.verifyCandidate({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_promote_model',
    description: 'Create or reuse a model version and set an alias only when current final-validation evidence has matching Session approval.',
    parameters: {
      runId: { type: 'string', required: true },
      alias: { type: 'string', required: true },
      idempotencyKey: { type: 'string', required: true },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const agentFailure = requireAgent(exec.agent)
      if (agentFailure !== undefined) return safeResult(agentFailure)
      const verified = await context.controller.verifyCandidate({ runId: args.runId, signal: exec.signal })
      if (!verified.ok) return safeResult(verified)
      const approval = approvalReference(context, exec.agent!, verified.data.evidence)
      if (approval === undefined) return missingApproval(verified.data.evidence)
      return safeResult(await context.controller.promoteModel({ ...args, approval, signal: exec.signal }))
    },
  }))
  return tools
}
