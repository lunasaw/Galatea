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

  export interface StageApprovalRequest {
    readonly agent: Agent
    readonly stage: string
    readonly artifactId: string
    readonly evidenceDigest: string
    readonly summary: string
    readonly signal?: AbortSignal
  }

  export type StageApprovalDecision = {
    readonly outcome: 'approved' | 'rejected' | 'changes-requested' | 'cancelled' | 'unavailable'
    readonly approver?: string
    readonly comment?: string
    readonly decidedAt?: number
    readonly expiresAt?: number
  }

  export type StageApprovalValidation =
    | { readonly valid: true; readonly decision: StageApprovalDecision }
    | {
      readonly valid: false
      readonly reason: 'not-found' | 'expired' | 'rejected' | 'changes-requested' | 'cancelled' | 'unavailable'
      readonly decision?: StageApprovalDecision
    }

  export function findStageApproval(
    events: readonly unknown[],
    subject: { readonly stage: string; readonly artifactId: string; readonly evidenceDigest: string },
    now?: number,
  ): StageApprovalValidation
}

declare module '@deepseek-ai/dsh-user-questions' {
  import type { Agent } from '@deepseek-ai/dsh-agent'

  export interface AskUserQuestionItem {
    readonly id: string
    readonly question: string
    readonly detail?: string
    readonly header?: string
    readonly options?: readonly { readonly label: string; readonly description?: string }[]
  }

  export interface AskUserQuestionAnswer {
    readonly answers: readonly {
      readonly id: string
      readonly selected: readonly string[]
      readonly custom?: string
    }[]
  }

  export interface AskUserQuestionRequest {
    readonly questions: readonly AskUserQuestionItem[]
    readonly agent?: Agent
    readonly signal?: AbortSignal
  }
}

declare module '@deepseek-ai/cordis' {
  import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
  import type {
    StageApprovalDecision,
    StageApprovalRequest,
  } from '@deepseek-ai/dsh-user-approval'
  import type {
    AskUserQuestionAnswer,
    AskUserQuestionRequest,
  } from '@deepseek-ai/dsh-user-questions'

  export interface Context {
    readonly tools: { register(definition: ToolDefinition): () => void }
    readonly approval: {
      requestStage(request: StageApprovalRequest): Promise<StageApprovalDecision>
    }
    readonly userQuestions: {
      ask(request: AskUserQuestionRequest): Promise<AskUserQuestionAnswer>
    }
    on(
      event: 'approval/stage-request',
      callback: (request: StageApprovalRequest) => Promise<StageApprovalDecision>,
    ): () => void
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
