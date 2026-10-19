import type { QualityGateDeclaration } from './project.ts'

export interface QualityGateResult {
  readonly name: string
  readonly status: 'passed' | 'failed' | 'skipped'
  readonly actual?: unknown
  readonly expected?: unknown
  readonly reason: string
}

function compare(operator: QualityGateDeclaration['operator'], actual: unknown, expected: unknown): boolean {
  if (operator === 'exists') return actual !== undefined && actual !== null
  if (operator === 'eq') return actual === expected
  if (operator === 'neq') return actual !== expected
  if (typeof actual !== 'number' || typeof expected !== 'number'
    || !Number.isFinite(actual) || !Number.isFinite(expected)) return false
  if (operator === 'gt') return actual > expected
  if (operator === 'gte') return actual >= expected
  if (operator === 'lt') return actual < expected
  return actual <= expected
}

export function evaluateQualityGates(
  gates: readonly QualityGateDeclaration[],
  values: {
    readonly metrics: Readonly<Record<string, number>>
    readonly evidence: Readonly<Record<string, unknown>>
  },
): { readonly passed: boolean; readonly results: readonly QualityGateResult[] } {
  const results = gates.map((gate): QualityGateResult => {
    const actual = gate.source === 'metric' ? values.metrics[gate.key] : values.evidence[gate.key]
    if (actual === undefined) {
      return gate.required
        ? { name: gate.name, status: 'failed', reason: `required ${gate.source} ${gate.key} is missing` }
        : { name: gate.name, status: 'skipped', reason: `optional ${gate.source} ${gate.key} is missing` }
    }
    const passed = compare(gate.operator, actual, gate.threshold)
    return {
      name: gate.name,
      status: passed ? 'passed' : 'failed',
      actual,
      ...(gate.operator === 'exists' ? {} : { expected: gate.threshold }),
      reason: passed ? 'gate passed' : `gate ${gate.operator} comparison failed`,
    }
  })
  return { passed: results.every(result => result.status !== 'failed'), results }
}

export default evaluateQualityGates
