import type { ObjectiveDirection } from './project.ts'

export interface RunIdentity {
  readonly task: string
  readonly datasetDigest: string
  readonly splitDigest: string
  readonly preprocessingVersion: string
  readonly metricDefinition: string
  readonly evaluationProtocol: string
  readonly role: string
  readonly [key: string]: string
}

export interface ComparableRun {
  readonly runId: string
  readonly identity: RunIdentity
  readonly metrics: Readonly<Record<string, number>>
}

export interface ComparisonResult {
  readonly comparable: boolean
  readonly reasons: readonly string[]
}

export function compareRuns(left: ComparableRun, right: ComparableRun): ComparisonResult {
  const fields = new Set([...Object.keys(left.identity), ...Object.keys(right.identity)])
  const reasons: string[] = []
  for (const field of fields) {
    if (left.identity[field] !== right.identity[field]) reasons.push(`${field} differs`)
  }
  return { comparable: reasons.length === 0, reasons }
}

export function selectBestRun(
  runs: readonly ComparableRun[],
  objective: { readonly metric: string; readonly direction: ObjectiveDirection },
): {
  readonly best?: ComparableRun
  readonly ranked: readonly ComparableRun[]
  readonly rejected: readonly { readonly runId: string; readonly reasons: readonly string[] }[]
} {
  if (runs.length === 0) return { ranked: [], rejected: [] }
  const reference = runs[0]!
  const ranked: ComparableRun[] = []
  const rejected: { runId: string; reasons: readonly string[] }[] = []
  for (const run of runs) {
    const comparison = compareRuns(reference, run)
    const metric = run.metrics[objective.metric]
    const reasons = [...comparison.reasons]
    if (metric === undefined || !Number.isFinite(metric)) reasons.push(`metric ${objective.metric} is missing`)
    if (reasons.length > 0) rejected.push({ runId: run.runId, reasons })
    else ranked.push(run)
  }
  const multiplier = objective.direction === 'max' ? -1 : 1
  ranked.sort((left, right) => multiplier * (left.metrics[objective.metric]! - right.metrics[objective.metric]!) || left.runId.localeCompare(right.runId))
  return { ...(ranked[0] === undefined ? {} : { best: ranked[0] }), ranked, rejected }
}
