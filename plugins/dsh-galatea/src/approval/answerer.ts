export interface StageApprovalPromptRequest {
  readonly stage: string
  readonly artifactId: string
  readonly evidenceDigest: string
  readonly summary: string
}

export interface QuestionItem {
  readonly id: string
  readonly question: string
  readonly detail?: string
  readonly header?: string
  readonly options?: readonly { readonly label: string; readonly description?: string }[]
}

export interface QuestionAnswer {
  readonly answers: readonly {
    readonly id: string
    readonly selected: readonly string[]
    readonly custom?: string
  }[]
}

export type StageApprovalAnswer =
  | {
    readonly outcome: 'approved'
    readonly approver: string
    readonly comment: string
    readonly decidedAt: number
    readonly expiresAt: number
  }
  | {
    readonly outcome: 'rejected' | 'changes-requested'
    readonly approver: string
    readonly comment: string
    readonly decidedAt: number
  }
  | { readonly outcome: 'cancelled' | 'unavailable'; readonly decidedAt: number }

const VALIDITY_MILLIS: Readonly<Record<string, number>> = {
  '1 hour': 60 * 60 * 1_000,
  '24 hours': 24 * 60 * 60 * 1_000,
  '7 days': 7 * 24 * 60 * 60 * 1_000,
}

function answer(result: QuestionAnswer, id: string): QuestionAnswer['answers'][number] | undefined {
  return result.answers.find(item => item.id === id)
}

function textAnswer(result: QuestionAnswer, id: string): string | undefined {
  const value = answer(result, id)?.custom?.trim()
  return value === undefined || value === '' ? undefined : value
}

/** Convert the generic Harness question protocol into one closed stage decision. */
export async function answerStageApproval(input: {
  readonly request: StageApprovalPromptRequest
  readonly ask: (request: { readonly questions: QuestionItem[] }) => Promise<QuestionAnswer>
  readonly now?: () => number
}): Promise<StageApprovalAnswer> {
  const now = input.now ?? Date.now
  const decidedAt = now()
  let result: QuestionAnswer
  try {
    result = await input.ask({
      questions: [
        {
          id: 'decision',
          header: 'Stage decision',
          question: `Decide the ${input.request.stage} evidence.`,
          detail: [
            input.request.summary,
            `Artifact: ${input.request.artifactId}`,
            `Evidence digest: ${input.request.evidenceDigest}`,
          ].join('\n'),
          options: [
            { label: 'Approve', description: 'Permit the next lifecycle action while this evidence remains unchanged.' },
            { label: 'Request changes', description: 'Return the stage for correction without granting access.' },
            { label: 'Reject', description: 'Reject this evidence and stop the gated action.' },
          ],
        },
        {
          id: 'approver',
          header: 'Approver',
          question: 'Enter the accountable reviewer identity.',
        },
        {
          id: 'comment',
          header: 'Review note',
          question: 'Record the review rationale or required changes.',
        },
        {
          id: 'validity',
          header: 'Validity',
          question: 'Choose how long an approval remains valid.',
          options: Object.keys(VALIDITY_MILLIS).map(label => ({ label })),
        },
      ],
    })
  } catch {
    return { outcome: 'unavailable', decidedAt }
  }
  const decision = answer(result, 'decision')?.selected[0]
  const approver = textAnswer(result, 'approver')
  const comment = textAnswer(result, 'comment')
  if (approver === undefined || comment === undefined) return { outcome: 'unavailable', decidedAt }
  if (decision === 'Reject') return { outcome: 'rejected', approver, comment, decidedAt }
  if (decision === 'Request changes') return { outcome: 'changes-requested', approver, comment, decidedAt }
  if (decision !== 'Approve') return { outcome: 'unavailable', decidedAt }
  const validity = answer(result, 'validity')?.selected[0]
  const validityMillis = validity === undefined ? undefined : VALIDITY_MILLIS[validity]
  if (validityMillis === undefined) return { outcome: 'unavailable', decidedAt }
  return { outcome: 'approved', approver, comment, decidedAt, expiresAt: decidedAt + validityMillis }
}
