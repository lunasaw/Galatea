import type { Agent } from '@deepseek-ai/dsh-agent'
import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import type { JsonValue as HarnessJsonValue } from '@deepseek-ai/dsh-util-values'
import type { ApprovalOutcome, ApprovalRequest } from '@deepseek-ai/dsh-user-approval'
import type { JsonValue, ToolResult } from '../contracts/index.ts'
import { failure, redactSecrets } from '../contracts/index.ts'
import type { ApprovalReference, ExecutionRole } from '../policies/lifecycle.ts'
import type { GalateaController, StageEvidence } from './controller.ts'

export const GALATEA_TOOL_NAMES = [
  'galatea_list_projects',
  'galatea_select_project',
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
  'galatea_verify_candidate',
  'galatea_promote_model',
] as const

export interface GalateaToolContext {
  readonly controller: GalateaController
  readonly controllerFor?: (agent: Agent | undefined) => Promise<GalateaController>
  readonly listProjects?: (agent: Agent | undefined) => Promise<JsonValue>
  readonly selectProject?: (agent: Agent | undefined, projectId: string) => Promise<JsonValue>
  readonly approvalPolicy?: (agent: Agent | undefined) => string
  readonly approval: {
    request(request: ApprovalRequest): Promise<ApprovalOutcome>
  }
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
    message: 'a live Harness Agent is required to request approval',
    retryable: false,
    stateChanged: false,
  })
}

function approvalReference(evidence: StageEvidence): ApprovalReference {
  return {
    valid: true,
    stage: evidence.stage,
    artifactId: evidence.artifactId,
    evidenceDigest: evidence.digest,
  }
}

function approvalFailure(outcome: ApprovalOutcome, evidence: StageEvidence): ToolResult<never> {
  const reason = outcome === 'cancelled'
    ? 'approval was cancelled'
    : outcome === 'unavailable'
      ? 'no approval answerer is available'
      : 'approval was rejected'
  return failure({
    category: 'approval-required',
    message: `${reason} for ${evidence.stage} evidence ${evidence.digest}`,
    retryable: false,
    stateChanged: false,
    nextAction: 'Retry the operation to request a new one-time approval for the current evidence.',
  })
}

async function requestApproval(
  context: GalateaToolContext,
  agent: Agent,
  evidence: StageEvidence,
  toolName: string,
  action: string,
  callId: ApprovalRequest['callId'],
  signal: AbortSignal | undefined,
): Promise<ApprovalReference | ToolResult<never>> {
  const request: ApprovalRequest = {
    agent,
    toolName,
    ...(callId === undefined ? {} : { callId }),
    reason: [
      `One-time approval is required before ${action}.`,
      `Stage: ${evidence.stage}`,
      `Artifact: ${evidence.artifactId}`,
      `Evidence digest: ${evidence.digest}`,
    ].join('\n'),
    ...(signal === undefined ? {} : { signal }),
  }
  let outcome: ApprovalOutcome
  try {
    outcome = await context.approval.request(request)
  } catch {
    outcome = 'unavailable'
  }
  return outcome === 'allowed-once' ? approvalReference(evidence) : approvalFailure(outcome, evidence)
}

/** Build the bounded model-facing surface over one stateless Controller. */
export function createGalateaTools(context: GalateaToolContext): ToolDefinition[] {
  const controller = async (agent: Agent | undefined) => context.controllerFor === undefined
    ? context.controller
    : await context.controllerFor(agent)
  const role = { type: 'string', enum: ['smoke', 'trial', 'champion'], required: true } as const
  const configPath = { type: 'string', required: true, description: 'Project-relative YAML path below configRoot.' } as const
  const releaseManifestPath = { type: 'string', required: true, description: 'Path below the configured immutable release root.' } as const
  const submissionId = { type: 'string', required: true } as const
  const tools: ToolDefinition[] = []

  tools.push(defineTool({
    name: 'galatea_list_projects',
    description: 'List the administrator-configured Galatea projects and the current Session selection.',
    parameters: {}, output,
    async execute(_args, exec) {
      if (context.listProjects === undefined) {
        return safeResult(failure({ category: 'unsupported', message: 'multi-project registry is not configured', retryable: false, stateChanged: false }))
      }
      return safeResult({ ok: true, data: await context.listProjects(exec.agent), summary: 'Listed configured Galatea projects.' })
    },
  }))
  tools.push(defineTool({
    name: 'galatea_select_project',
    description: 'Select one administrator-configured Galatea project for this Harness Session.',
    parameters: { projectId: { type: 'string', required: true } }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      if (exec.agent === undefined) return safeResult(failure({ category: 'precondition-failed', message: 'a live Harness Agent is required for Session project selection', retryable: false, stateChanged: false }))
      if (context.selectProject === undefined) {
        return safeResult(failure({ category: 'unsupported', message: 'multi-project registry is not configured', retryable: false, stateChanged: false }))
      }
      return safeResult({ ok: true, data: await context.selectProject(exec.agent, args.projectId), summary: `Selected Galatea project ${args.projectId} for this Session.` })
    },
  }))
  tools.push(defineTool({
    name: 'galatea_inspect_project',
    description: 'Inspect the selected training project, service identities, approval policy, and declared capabilities.',
    parameters: {}, output,
    async execute(_args, exec) {
      const selected = await controller(exec.agent)
      const policy = context.approvalPolicy?.(exec.agent)
      return safeResult(await selected.inspectProject({
        ...(policy === undefined ? {} : { approvalPolicy: policy }),
        signal: exec.signal,
      }))
    },
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
      return safeResult(await (await controller(exec.agent)).patchConfig({
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
      return safeResult(await (await controller(exec.agent)).planRun({ ...args, role: args.role as ExecutionRole, signal: exec.signal }))
    },
  }))
  tools.push(defineTool({
    name: 'galatea_submit_job',
    description: 'Submit the fixed declared training entrypoint after one-time readiness approval; Champion also requires one-time approval of the selected training-optimization evidence.',
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
      const selectedController = await controller(exec.agent)
      const planned = await selectedController.planRun({ ...args, role: args.role as ExecutionRole, signal: exec.signal })
      if (!planned.ok) return safeResult(planned)
      const requested = await requestApproval(
        context,
        exec.agent!,
        planned.data.evidence,
        'galatea_submit_job',
        'submit a Ray Job',
        exec.callId,
        exec.signal,
      )
      if (!('valid' in requested)) return safeResult(requested)
      const approval = requested
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
        const candidate = await selectedController.buildStageEvidence({
          runId: args.candidateRunId,
          stage: 'training-optimization',
          signal: exec.signal,
        })
        if (!candidate.ok) return safeResult(candidate)
        const candidateRequested = await requestApproval(
          context,
          exec.agent!,
          candidate.data.evidence,
          'galatea_submit_job',
          'submit a Champion Ray Job from the selected Trial',
          exec.callId,
          exec.signal,
        )
        if (!('valid' in candidateRequested)) return safeResult(candidateRequested)
        candidateApproval = candidateRequested
      } else if (args.candidateRunId !== undefined) {
        return safeResult(failure({
          category: 'invalid-input',
          message: 'candidateRunId applies only to champion submission',
          retryable: false,
          stateChanged: false,
        }))
      }
      return safeResult(await selectedController.submitJob({
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
    parameters: {
      submissionId,
      includeLogs: { type: 'boolean' },
      logCursor: {
        type: 'number',
        description: 'Non-negative integer character offset returned as nextLogCursor by the previous observation.',
      },
    }, output,
    async execute(args, exec) { return safeResult(await (await controller(exec.agent)).observeJob({ ...args, signal: exec.signal })) },
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
    async execute(args, exec) { return safeResult(await (await controller(exec.agent)).stopJob({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_pause_job',
    description: 'Request a checkpoint-backed pause; unsupported projects fail without stopping the Job.',
    parameters: { submissionId, reason: { type: 'string', required: true } }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      return safeResult(await (await controller(exec.agent)).pauseJob({ ...args, signal: exec.signal }))
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
      const selectedController = await controller(exec.agent)
      const planned = await selectedController.planResume({ ...args, signal: exec.signal })
      if (!planned.ok) return safeResult(planned)
      const requested = await requestApproval(
        context,
        exec.agent!,
        planned.data.evidence,
        'galatea_resume_job',
        'resume a Ray Job',
        exec.callId,
        exec.signal,
      )
      if (!('valid' in requested)) return safeResult(requested)
      const approval = requested
      return safeResult(await selectedController.resumeJob({
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
    async execute(args, exec) { return safeResult(await (await controller(exec.agent)).compareRuns({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_build_stage_evidence',
    description: 'Re-read a successful trial Run and its declared Artifacts to build immutable training-optimization evidence.',
    parameters: {
      runId: { type: 'string', required: true },
      stage: { type: 'string', const: 'training-optimization', required: true },
    }, output,
    async execute(args, exec) { return safeResult(await (await controller(exec.agent)).buildStageEvidence({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_verify_candidate',
    description: 'Re-read a champion Run and declared Artifacts, evaluate quality gates, and build final-validation evidence.',
    parameters: { runId: { type: 'string', required: true } }, output,
    async execute(args, exec) { return safeResult(await (await controller(exec.agent)).verifyCandidate({ ...args, signal: exec.signal })) },
  }))
  tools.push(defineTool({
    name: 'galatea_promote_model',
    description: 'Create or reuse a model version and set an alias only after one-time approval of the current final-validation evidence.',
    parameters: {
      runId: { type: 'string', required: true },
      alias: { type: 'string', required: true },
      idempotencyKey: { type: 'string', required: true },
    }, output,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const agentFailure = requireAgent(exec.agent)
      if (agentFailure !== undefined) return safeResult(agentFailure)
      const selectedController = await controller(exec.agent)
      const verified = await selectedController.verifyCandidate({ runId: args.runId, signal: exec.signal })
      if (!verified.ok) return safeResult(verified)
      const requested = await requestApproval(
        context,
        exec.agent!,
        verified.data.evidence,
        'galatea_promote_model',
        'promote the model',
        exec.callId,
        exec.signal,
      )
      if (!('valid' in requested)) return safeResult(requested)
      const approval = requested
      return safeResult(await selectedController.promoteModel({ ...args, approval, signal: exec.signal }))
    },
  }))
  return tools
}
