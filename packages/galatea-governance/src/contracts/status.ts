import type { JsonValue } from './index.ts'

export type ExecutionStatus =
  | 'not-applicable'
  | 'not-started'
  | 'planned'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'stopped'
  | 'cancelled'
  | 'unknown'

export type QualityStatus =
  | 'not-evaluated'
  | 'pending'
  | 'passed'
  | 'failed'
  | 'inconclusive'
  | 'blocked'
  | 'unknown'

export type GovernanceStatus =
  | 'not-required'
  | 'approval-required'
  | 'approved-for-execution'
  | 'approved-for-promotion'
  | 'rejected'
  | 'blocked'
  | 'promoted'
  | 'unknown'

export type IntegrityStatus = 'passed' | 'failed' | 'unknown' | 'not-applicable'
export type OperationScope = 'project' | 'plan' | 'job' | 'run' | 'candidate' | 'promotion' | 'comparison'

export interface OperationStatus {
  readonly [key: string]: JsonValue
  readonly scope: OperationScope
  readonly statuses: {
    readonly execution: ExecutionStatus
    readonly quality: QualityStatus
    readonly governance: GovernanceStatus
  }
  readonly integrity: {
    readonly preprocessingParity: IntegrityStatus
    readonly migrationContamination: IntegrityStatus
  }
}

export function operationStatus(
  scope: OperationScope,
  execution: ExecutionStatus,
  quality: QualityStatus,
  governance: GovernanceStatus,
  integrity: OperationStatus['integrity'] = {
    preprocessingParity: 'unknown',
    migrationContamination: 'unknown',
  },
): OperationStatus {
  return { scope, statuses: { execution, quality, governance }, integrity }
}

export function executionFromRay(status: string): ExecutionStatus {
  switch (status) {
    case 'PENDING': return 'queued'
    case 'RUNNING': return 'running'
    case 'SUCCEEDED': return 'succeeded'
    case 'FAILED': return 'failed'
    case 'STOPPED': return 'stopped'
    default: return 'unknown'
  }
}
