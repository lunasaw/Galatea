import type {
  IntegrityCheckDeclaration,
  IntegrityRole,
  MigrationLineageDeclaration,
  PreprocessingComparisonDeclaration,
  ProjectIntegrityDeclaration,
} from './project.ts'

export type IntegrityStatus = 'passed' | 'failed' | 'unknown' | 'not-applicable'

export interface IntegrityCheckResult {
  readonly [key: string]: import('../contracts/index.ts').JsonValue
  readonly id: string
  readonly status: IntegrityStatus
  readonly required: boolean
  readonly reason: string
}

export interface IntegritySectionResult {
  readonly [key: string]: import('../contracts/index.ts').JsonValue
  readonly status: IntegrityStatus
  readonly checks: IntegrityCheckResult[]
}

export interface PlanIntegrityEvidence {
  readonly [key: string]: import('../contracts/index.ts').JsonValue
  readonly reports?: {
    readonly preprocessing: string
    readonly migration?: string
  }
  readonly reportedStatus: IntegrityStatus
  readonly reportDigest?: string
}

export interface PlanIntegrityEvaluation {
  readonly [key: string]: import('../contracts/index.ts').JsonValue
  readonly preprocessing: IntegritySectionResult
  readonly migration: IntegritySectionResult
  readonly passed: boolean
  readonly advisories: string[]
  readonly evidence: PlanIntegrityEvidence
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function atPath(value: unknown, path: string): unknown {
  let current = value
  for (const segment of path.split('.')) {
    if (!isRecord(current) || !Object.prototype.hasOwnProperty.call(current, segment)) return undefined
    current = current[segment]
  }
  return current
}

/** Normalize a project-provided integrity status without inventing success. */
export function normalizeIntegrityStatus(value: unknown): IntegrityStatus {
  if (value === 'passed' || value === 'failed' || value === 'unknown' || value === 'not-applicable') return value
  return 'unknown'
}

function equivalent(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, index) => equivalent(value, right[index]))
  }
  if (!isRecord(left) || !isRecord(right)) return false
  const leftKeys = Object.keys(left).sort()
  const rightKeys = Object.keys(right).sort()
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index] && equivalent(left[key], right[key]))
}

function summarize(checks: readonly IntegrityCheckResult[], applies: boolean): IntegrityStatus {
  if (!applies) return 'not-applicable'
  if (checks.some(check => check.status === 'failed')) return 'failed'
  if (checks.some(check => check.status === 'unknown')) return 'unknown'
  if (checks.some(check => check.status === 'passed')) return 'passed'
  return 'not-applicable'
}

function applicable(roles: readonly IntegrityRole[], role: IntegrityRole): boolean {
  return roles.includes(role)
}

function reportedCheck(
  declaration: IntegrityCheckDeclaration,
  role: IntegrityRole,
  reported: unknown,
): IntegrityCheckResult {
  if (!applicable(declaration.roles, role)) {
    return { id: declaration.id, status: 'not-applicable', required: declaration.required, reason: `check does not apply to ${role}` }
  }
  const value = atPath(reported, declaration.checkPath)
  const reportedStatus = isRecord(value)
    ? normalizeIntegrityStatus(value['status'])
    : normalizeIntegrityStatus(value)
  if (reportedStatus === 'unknown' || reportedStatus === 'not-applicable') {
    return {
      id: declaration.id,
      status: 'unknown',
      required: declaration.required,
      reason: reportedStatus === 'not-applicable'
        ? 'project reported not-applicable for a role-applicable check'
        : 'project did not report a recognized check status',
    }
  }
  const reason = isRecord(value) && typeof value['reason'] === 'string' && value['reason'].trim() !== ''
    ? value['reason']
    : `project reported ${reportedStatus}`
  return { id: declaration.id, status: reportedStatus, required: declaration.required, reason }
}

function preprocessingCheck(
  declaration: PreprocessingComparisonDeclaration,
  role: IntegrityRole,
  reported: unknown,
  contexts: Readonly<Record<string, unknown>>,
): IntegrityCheckResult {
  const projectCheck = reportedCheck(declaration, role, reported)
  if (projectCheck.status === 'not-applicable' || projectCheck.status === 'failed') return projectCheck
  const left = contexts[declaration.leftContext]
  const right = contexts[declaration.rightContext]
  if (!isRecord(left) || !isRecord(right)) {
    return { id: declaration.id, status: 'unknown', required: declaration.required, reason: 'one or both preprocessing contexts are missing' }
  }
  const missing = declaration.fields.filter(field => atPath(left, field) === undefined || atPath(right, field) === undefined)
  if (missing.length > 0) {
    return {
      id: declaration.id,
      status: 'unknown',
      required: declaration.required,
      reason: `preprocessing fields are missing: ${missing.join(', ')}`,
    }
  }
  const mismatches = declaration.fields.filter(field => !equivalent(atPath(left, field), atPath(right, field)))
  if (mismatches.length > 0) {
    return {
      id: declaration.id,
      status: 'failed',
      required: declaration.required,
      reason: `preprocessing fields differ: ${mismatches.join(', ')}`,
    }
  }
  if (projectCheck.status === 'unknown') {
    return { id: declaration.id, status: 'unknown', required: declaration.required, reason: projectCheck.reason }
  }
  return { id: declaration.id, status: 'passed', required: declaration.required, reason: 'declared preprocessing fields match' }
}

function lineageCheck(
  declaration: MigrationLineageDeclaration,
  role: IntegrityRole,
  reported: unknown,
): IntegrityCheckResult {
  const id = 'migration-lineage'
  if (!applicable(declaration.roles, role)) {
    return { id, status: 'not-applicable', required: declaration.required, reason: `lineage does not apply to ${role}` }
  }
  const lineage = atPath(reported, declaration.outputPath)
  if (typeof lineage !== 'string' || lineage.trim() === '') {
    return { id, status: 'unknown', required: declaration.required, reason: 'project did not report migration lineage' }
  }
  const passed = declaration.allowed.includes(lineage)
  return {
    id,
    status: passed ? 'passed' : 'failed',
    required: declaration.required,
    reason: passed ? 'migration lineage is allowed' : `migration lineage ${lineage} is not allowed`,
  }
}

function advisoriesFor(
  declaration: ProjectIntegrityDeclaration,
  role: IntegrityRole,
  plan: unknown,
  reported: unknown,
): string[] {
  const advisories: string[] = []
  for (const item of declaration.improvementBacklog ?? []) {
    if (!applicable(item.roles, role)) continue
    const value = atPath(reported, item.outputPath) ?? atPath(plan, item.outputPath)
    if (typeof value === 'string' && value.trim() !== '') advisories.push(value)
    else if (Array.isArray(value)) {
      for (const entry of value) if (typeof entry === 'string' && entry.trim() !== '') advisories.push(entry)
    }
  }
  return [...new Set(advisories)]
}

/** Normalize and evaluate project-reported read-only plan integrity for one lifecycle role. */
export function evaluatePlanIntegrity(
  declaration: ProjectIntegrityDeclaration | undefined,
  role: IntegrityRole,
  plan: unknown,
): PlanIntegrityEvaluation {
  if (declaration === undefined) {
    return {
      preprocessing: { status: 'unknown', checks: [] },
      migration: { status: 'unknown', checks: [] },
      passed: false,
      advisories: ['project manifest does not declare required plan integrity evidence'],
      evidence: { reportedStatus: 'unknown' },
    }
  }
  const reported = atPath(plan, declaration.planOutputPath)
  const reportedRecord = isRecord(reported) ? reported : {}
  const contexts: Record<string, unknown> = {}
  for (const context of declaration.preprocessing.contexts) {
    if (!applicable(context.roles, role)) continue
    contexts[context.id] = atPath(reportedRecord, context.outputPath)
  }
  const preprocessingChecks = declaration.preprocessing.comparisons.map(comparison => (
    preprocessingCheck(comparison, role, reportedRecord, contexts)
  ))
  const migrationChecks = declaration.migration.enabled
    ? [
        lineageCheck(declaration.migration.lineage, role, reportedRecord),
        ...declaration.migration.contaminationChecks.map(check => reportedCheck(check, role, reportedRecord)),
      ]
    : []
  const preprocessing: IntegritySectionResult = {
    status: summarize(preprocessingChecks, declaration.preprocessing.comparisons.some(check => applicable(check.roles, role))),
    checks: preprocessingChecks,
  }
  const migration: IntegritySectionResult = {
    status: summarize(migrationChecks, declaration.migration.enabled),
    checks: migrationChecks,
  }
  const reportDigest = reportedRecord['reportDigest']
  const reportedStatus = normalizeIntegrityStatus(reportedRecord['status'])
  const requiredChecks = [...preprocessingChecks, ...migrationChecks].filter(check => check.required)
  return {
    preprocessing,
    migration,
    passed: reportedStatus !== 'failed'
      && requiredChecks.every(check => check.status === 'passed' || check.status === 'not-applicable'),
    advisories: advisoriesFor(declaration, role, plan, reportedRecord),
    evidence: {
      reports: {
        preprocessing: declaration.reports.preprocessing.artifactPath,
        ...(declaration.reports.migration === undefined
          ? {}
          : { migration: declaration.reports.migration.artifactPath }),
      },
      reportedStatus,
      ...(typeof reportDigest === 'string' && reportDigest.trim() !== '' ? { reportDigest } : {}),
    },
  }
}

/** Derive non-blocking operator guidance independently from controller state. */
export function deriveIntegrityAdvisories(evaluation: PlanIntegrityEvaluation): readonly string[] {
  const generated = [
    ...evaluation.preprocessing.checks,
    ...evaluation.migration.checks,
  ].filter(check => check.status === 'failed' || check.status === 'unknown')
    .map(check => `${check.required ? 'required' : 'optional'} integrity check ${check.id}: ${check.reason}`)
  return [...new Set([...evaluation.advisories, ...generated])]
}
