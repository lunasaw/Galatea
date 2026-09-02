export type ExecutionRole = 'smoke' | 'trial' | 'champion'
export type DatasetSplit = 'train' | 'validation' | 'test'
export type LifecycleStage = 'readiness' | 'training-optimization' | 'final-validation' | 'promotion'

export function authorizeDatasetAccess(
  role: ExecutionRole,
  split: DatasetSplit,
): { readonly allowed: true } | { readonly allowed: false; readonly reason: string } {
  if (split === 'test' && role !== 'champion') {
    return { allowed: false, reason: `${role} role cannot access the final test split` }
  }
  return { allowed: true }
}

export interface StageEvidenceIdentity {
  readonly stage: LifecycleStage
  readonly artifactId: string
  readonly digest: string
  readonly qualityGatesPassed?: boolean
}

export interface ApprovalReference {
  readonly valid: true
  readonly stage: LifecycleStage
  readonly artifactId: string
  readonly evidenceDigest: string
}

export function authorizeTransition(input: {
  readonly to: LifecycleStage
  readonly evidence: StageEvidenceIdentity
  readonly approval?: ApprovalReference
}): { readonly allowed: boolean; readonly reasons: readonly string[] } {
  const reasons: string[] = []
  if (input.to === 'final-validation' && input.evidence.stage !== 'training-optimization') {
    reasons.push('final-validation requires training-optimization evidence')
  }
  if (input.to === 'promotion') {
    if (input.evidence.stage !== 'final-validation') reasons.push('promotion requires final-validation evidence')
    if (input.evidence.qualityGatesPassed !== true) reasons.push('required quality gates have not passed')
  }
  if (input.to !== 'readiness') {
    if (input.approval === undefined) reasons.push('one-time approval is required for the current action')
    else {
      if (input.approval.stage !== input.evidence.stage) reasons.push('approval stage does not match current evidence')
      if (input.approval.artifactId !== input.evidence.artifactId) reasons.push('approval artifact id does not match current evidence')
      if (input.approval.evidenceDigest !== input.evidence.digest) reasons.push('approval evidence digest does not match current evidence')
    }
  }
  return { allowed: reasons.length === 0, reasons }
}
