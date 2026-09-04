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

/** Evidence-bound authorization derived from the Session's effective full-access preset. */
export interface FullAccessAuthorization {
  readonly kind: 'full-access'
  readonly permissionPreset: 'danger-full-access'
  readonly stage: LifecycleStage
  readonly artifactId: string
  readonly evidenceDigest: string
}

export type GovernanceAuthorization = ApprovalReference | FullAccessAuthorization

function authorizationMismatchReasons(
  authorization: GovernanceAuthorization,
  evidence: StageEvidenceIdentity,
): string[] {
  const reasons: string[] = []
  const name = 'valid' in authorization ? 'approval' : 'full-access authorization'
  if ('valid' in authorization) {
    if (authorization.valid !== true) reasons.push('approval is not valid')
  } else if (authorization.kind !== 'full-access'
    || authorization.permissionPreset !== 'danger-full-access') {
    reasons.push('full-access authorization does not identify the full-access permission preset')
  }
  if (authorization.stage !== evidence.stage) reasons.push(`${name} stage does not match current evidence`)
  if (authorization.artifactId !== evidence.artifactId) reasons.push(`${name} artifact id does not match current evidence`)
  if (authorization.evidenceDigest !== evidence.digest) reasons.push(`${name} evidence digest does not match current evidence`)
  return reasons
}

export function authorizeTransition(input: {
  readonly to: LifecycleStage
  readonly evidence: StageEvidenceIdentity
  readonly authorization?: GovernanceAuthorization
  /** @deprecated Pass authorization. Retained for Controller API compatibility. */
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
    if (input.authorization !== undefined && input.approval !== undefined) {
      reasons.push('provide exactly one governance authorization')
    }
    const authorization = input.authorization ?? input.approval
    if (authorization === undefined) reasons.push('one-time approval or full-access authorization is required for the current action')
    else reasons.push(...authorizationMismatchReasons(authorization, input.evidence))
  }
  return { allowed: reasons.length === 0, reasons }
}
