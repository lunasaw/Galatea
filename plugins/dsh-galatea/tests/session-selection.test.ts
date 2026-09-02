import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import {
  applyProjectSelectionEvent,
  currentProjectId,
  galateaProjectSelectionProjection,
  projectIdFromArguments,
  type GalateaProjectSelectionState,
} from '../src/session-selection.ts'

const initial: GalateaProjectSelectionState = { projectId: null, pendingNative: [] }

function event(type: string, data: unknown): Pick<SessionEvent, 'type' | 'data'> {
  return { type, data } as Pick<SessionEvent, 'type' | 'data'>
}

function nativeCall(callId: string, projectId: string): Pick<SessionEvent, 'type' | 'data'> {
  return event('tool/call', {
    callId,
    name: 'galatea_select_project',
    arguments: JSON.stringify({ projectId }),
  })
}

function nativeResult(callId: string, isError = false): Pick<SessionEvent, 'type' | 'data'> {
  return event('tool/result', {
    message: {
      source: { kind: 'tool', callId },
      content: [{ type: 'tool-result', toolCallId: callId, content: [], isError }],
    },
  })
}

test('replays successful native project selections with last-write-wins semantics', () => {
  let state = initial
  state = applyProjectSelectionEvent(state, nativeCall('call-1', 'cats'))
  assert.equal(state.projectId, null)
  state = applyProjectSelectionEvent(state, nativeResult('call-1'))
  assert.equal(state.projectId, 'cats')
  assert.deepEqual(state.pendingNative, [])

  state = applyProjectSelectionEvent(state, nativeCall('call-2', 'digits'))
  state = applyProjectSelectionEvent(state, nativeResult('call-2'))
  assert.equal(state.projectId, 'digits')
})

test('replays successful PTC selections from first-party dispatch events', () => {
  const state = applyProjectSelectionEvent(initial, event('tool/code-dispatch', {
    name: 'galatea_select_project',
    arguments: { projectId: 'digits' },
    isError: false,
  }))
  assert.equal(state.projectId, 'digits')
})

test('ignores failed and malformed selections without breaking replay', () => {
  let state = applyProjectSelectionEvent(initial, nativeCall('call-ok', 'cats'))
  state = applyProjectSelectionEvent(state, nativeResult('call-ok'))

  state = applyProjectSelectionEvent(state, nativeCall('call-failed', 'digits'))
  state = applyProjectSelectionEvent(state, nativeResult('call-failed', true))
  assert.equal(state.projectId, 'cats')
  assert.deepEqual(state.pendingNative, [])

  const malformed = [
    event('tool/call', { callId: 'bad-json', name: 'galatea_select_project', arguments: '{' }),
    event('tool/call', { callId: 'empty', name: 'galatea_select_project', arguments: '{"projectId":""}' }),
    event('tool/result', { message: null }),
    event('tool/code-dispatch', { name: 'galatea_select_project', arguments: { projectId: 1 }, isError: false }),
    event('tool/code-dispatch', { name: 'galatea_select_project', arguments: { projectId: 'digits' }, isError: true }),
    event('unknown/external', { projectId: 'digits' }),
  ]
  for (const candidate of malformed) {
    const next = applyProjectSelectionEvent(state, candidate)
    assert.equal(next, state)
  }
})

test('falls back when a durable selection is stale or absent', () => {
  const known = new Set(['cats', 'digits'])
  assert.equal(currentProjectId({ projectId: 'digits', pendingNative: [] }, id => known.has(id)), 'digits')
  assert.equal(currentProjectId({ projectId: 'removed', pendingNative: [] }, id => known.has(id)), undefined)
  assert.equal(currentProjectId(undefined, id => known.has(id)), undefined)
})

test('projection definition reproduces the pure replay fold', () => {
  let state = galateaProjectSelectionProjection.init({} as never, 0 as never)
  state = galateaProjectSelectionProjection.apply(state, nativeCall('call-1', 'cats') as SessionEvent)
  state = galateaProjectSelectionProjection.apply(state, nativeResult('call-1') as SessionEvent)
  assert.equal(state.projectId, 'cats')
  assert.equal(projectIdFromArguments({ projectId: 'cats' }), 'cats')
  assert.equal(projectIdFromArguments({ projectId: ' ' }), undefined)
})
