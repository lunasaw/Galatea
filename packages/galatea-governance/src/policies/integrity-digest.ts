import { createHash } from 'node:crypto'

/** Hash a sorted JSON envelope without losing the producer's numeric spelling.
 * Python's canonical JSON preserves 0.0, -0.0 and exponent notation; parsing
 * these as JS numbers before hashing changes previously published evidence.
 */
export function sourceIntegrityDigest(bytes: Uint8Array): string {
  const numbers = new WeakMap<object, string>()
  const value: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes),
    (_key: string, item: unknown, context?: { source?: string }): unknown => {
      if (typeof item !== 'number') return item
      if (context?.source === undefined) throw new Error('JSON number source support is required')
      const marker = {}
      numbers.set(marker, context.source)
      return marker
    })
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Integrity envelope must be an object')
  }
  const material = { ...value } as Record<string, unknown>
  delete material['content_digest']
  const encode = (item: unknown): string => {
    if (item !== null && typeof item === 'object') {
      const source = numbers.get(item)
      if (source !== undefined) return source
      if (Array.isArray(item)) return `[${item.map(encode).join(',')}]`
      const object = item as Record<string, unknown>
      return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${encode(object[key])}`).join(',')}}`
    }
    return JSON.stringify(item)
  }
  return `sha256:${createHash('sha256').update(encode(material)).digest('hex')}`
}
