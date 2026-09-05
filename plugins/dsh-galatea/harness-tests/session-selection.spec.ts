import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { Session, SessionId, SessionSeq, type SessionEvent } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import {
  currentProjectId,
  galateaProjectSelectionProjection,
  GALATEA_PROJECT_SELECTION_KEY,
} from '../src/session-selection.ts'

function dispatch(argumentsValue: unknown, isError = false): SessionEvent {
  return {
    type: 'tool/code-dispatch',
    seq: SessionSeq(0),
    time: 1,
    data: {
      rootCallId: 'root',
      parentCallId: 'parent',
      subCallId: 'sub',
      name: 'galatea_select_project',
      arguments: argumentsValue,
      isError,
      content: [],
    },
  } as SessionEvent
}

describe('Galatea Session project selection projection', () => {
  it('cold-replays persisted first-party Tool events', async () => {
    const ctx = new Context()
    await ctx.plugin(SessionProjectionRegistry)
    ctx.sessionProjections.register(galateaProjectSelectionProjection)
    const resumed = Session.create(SessionId('galatea-selection-replay'), [dispatch({ projectId: 'digits' })])

    const state = ctx.sessionProjections.stateOf(resumed, GALATEA_PROJECT_SELECTION_KEY)
    expect(state?.projectId).toBe('digits')
    expect(currentProjectId(state, id => id === 'digits')).toBe('digits')
  })

  it('ignores malformed, failed, and stale replay selections', async () => {
    const ctx = new Context()
    await ctx.plugin(SessionProjectionRegistry)
    ctx.sessionProjections.register(galateaProjectSelectionProjection)

    for (const [id, replay] of [
      ['malformed', dispatch({ projectId: 1 })],
      ['failed', dispatch({ projectId: 'digits' }, true)],
    ] as const) {
      const resumed = Session.create(SessionId(`galatea-selection-${id}`), [replay])
      expect(ctx.sessionProjections.stateOf(resumed, GALATEA_PROJECT_SELECTION_KEY)?.projectId).toBeNull()
    }

    const stale = Session.create(SessionId('galatea-selection-stale'), [dispatch({ projectId: 'removed' })])
    const state = ctx.sessionProjections.stateOf(stale, GALATEA_PROJECT_SELECTION_KEY)
    expect(currentProjectId(state, id => id === 'digits')).toBeUndefined()
  })
})
