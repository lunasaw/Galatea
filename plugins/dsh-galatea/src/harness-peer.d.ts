declare module '@deepseek-ai/dsh-agent' {
  export interface Agent {
    readonly session: { readonly events: readonly unknown[] }
  }
}

declare module '@deepseek-ai/dsh-tools' {
  import type { Agent } from '@deepseek-ai/dsh-agent'

  export type JsonValue =
    | string
    | number
    | boolean
    | null
    | readonly JsonValue[]
    | { readonly [key: string]: JsonValue }

  export interface ToolRunContext {
    readonly signal: AbortSignal
    readonly agent?: Agent
    readonly callId?: string
  }

  export interface ToolDefinition {
    readonly name: string
    readonly description: string
    readonly parameters: Readonly<Record<string, unknown>>
    readonly output: unknown
    execute(args: any, exec: ToolRunContext): Promise<JsonValue>
  }

  export function defineTool(options: {
    readonly name: string
    readonly description: string
    readonly parameters: Readonly<Record<string, unknown>>
    readonly output: {
      readonly schema: Readonly<Record<string, unknown>>
      render(args: any, value: JsonValue): readonly { readonly type: 'text'; readonly text: string }[]
    }
    readonly timeoutMs?: number
    isConcurrencySafe?(args: any): boolean
    execute(args: any, exec: ToolRunContext): Promise<JsonValue>
  }): ToolDefinition
}

declare module '@deepseek-ai/dsh-user-approval' {
  import type { Agent } from '@deepseek-ai/dsh-agent'

  export type ApprovalOutcome = 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'
  export interface ApprovalRequest {
    readonly agent: Agent
    readonly toolName: string
    readonly callId?: string
    readonly reason?: string
    readonly signal?: AbortSignal
  }
}

declare module '@deepseek-ai/cordis' {
  import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
  import type { ApprovalOutcome, ApprovalRequest } from '@deepseek-ai/dsh-user-approval'

  export interface Context {
    readonly tools: { register(definition: ToolDefinition): () => void }
    readonly approval: {
      request(request: ApprovalRequest): Promise<ApprovalOutcome>
    }
  }
}

declare module '@deepseek-ai/schemastery' {
  export interface Schema<T = unknown> {
    default(value: T): Schema<T>
    min(value: number): Schema<T>
    required(value?: boolean): Schema<T>
  }

  export interface Schemastery {
    object<T extends Record<string, Schema>>(value: T): Schema
    string(): Schema<string>
    natural(): Schema<number>
  }

  const schema: Schemastery
  export default schema
}
