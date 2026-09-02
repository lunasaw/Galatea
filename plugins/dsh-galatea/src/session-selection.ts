import type { SessionEvent } from '@deepseek-ai/dsh-session'
import type { ProjectionDefinition } from '@deepseek-ai/dsh-session-projection'
import { z } from 'zod'

/** Host-only projection key for the Galatea project selected by successful tool calls. */
export const GALATEA_PROJECT_SELECTION_KEY = 'galateaProjectSelection'

/** Durable tool whose successful result changes the selected Galatea project. */
export const GALATEA_SELECT_PROJECT_TOOL = 'galatea_select_project'

interface PendingNativeSelection {
  readonly callId: string
  readonly projectId: string
}

/** Fold state derived exclusively from first-party Harness Tool events. */
export interface GalateaProjectSelectionState {
  readonly projectId: string | null
  readonly pendingNative: readonly PendingNativeSelection[]
}

declare module '@deepseek-ai/dsh-session-projection/types' {
  interface SessionProjectionStateMap {
    galateaProjectSelection: GalateaProjectSelectionState
  }
}

const selectionStateSchema = z.object({
  projectId: z.string().nullable(),
  pendingNative: z.array(z.object({
    callId: z.string(),
    projectId: z.string(),
  }).strict()),
}).strict()

function record(value: unknown): Readonly<Record<string, unknown>> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Readonly<Record<string, unknown>>
    : undefined
}

/** Read a non-empty project id from already parsed Tool arguments. */
export function projectIdFromArguments(value: unknown): string | undefined {
  const projectId = record(value)?.['projectId']
  if (typeof projectId !== 'string' || projectId.trim() === '') return undefined
  return projectId
}

function projectIdFromNativeArguments(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    return undefined
  }
  return projectIdFromArguments(parsed)
}

function nativeResultCallId(value: unknown): string | undefined {
  const message = record(value)?.['message']
  const source = record(record(message)?.['source'])
  return source?.['kind'] === 'tool' && typeof source['callId'] === 'string'
    ? source['callId']
    : undefined
}

function nativeResultSucceeded(value: unknown): boolean {
  const message = record(value)?.['message']
  const content = record(message)?.['content']
  if (!Array.isArray(content) || content.length !== 1) return false
  const block = record(content[0])
  if (block?.['type'] !== 'tool-result') return false
  return block['isError'] === undefined || block['isError'] === false
}

/** Apply one committed Harness Session event without throwing on irrelevant or malformed records. */
export function applyProjectSelectionEvent(
  state: GalateaProjectSelectionState,
  event: Pick<SessionEvent, 'type' | 'data'>,
): GalateaProjectSelectionState {
  if (event.type === 'tool/call') {
    const data = record(event.data)
    if (data?.['name'] !== GALATEA_SELECT_PROJECT_TOOL || typeof data['callId'] !== 'string') return state
    const projectId = projectIdFromNativeArguments(data['arguments'])
    if (projectId === undefined) return state
    return {
      projectId: state.projectId,
      pendingNative: [
        ...state.pendingNative.filter(item => item.callId !== data['callId']),
        { callId: data['callId'], projectId },
      ],
    }
  }

  if (event.type === 'tool/result') {
    const callId = nativeResultCallId(event.data)
    if (callId === undefined) return state
    const pending = state.pendingNative.find(item => item.callId === callId)
    if (pending === undefined) return state
    return {
      projectId: nativeResultSucceeded(event.data) ? pending.projectId : state.projectId,
      pendingNative: state.pendingNative.filter(item => item.callId !== callId),
    }
  }

  if (event.type === 'tool/code-dispatch') {
    const data = record(event.data)
    if (data?.['name'] !== GALATEA_SELECT_PROJECT_TOOL || data['isError'] !== false) return state
    const projectId = projectIdFromArguments(data['arguments'])
    if (projectId === undefined) return state
    return { projectId, pendingNative: state.pendingNative }
  }

  return state
}

/** Host-only replay projection for one Session's selected Galatea project. */
export const galateaProjectSelectionProjection: ProjectionDefinition<
  typeof GALATEA_PROJECT_SELECTION_KEY,
  GalateaProjectSelectionState
> = {
  key: GALATEA_PROJECT_SELECTION_KEY,
  stateSchema: selectionStateSchema,
  init: () => ({ projectId: null, pendingNative: [] }),
  apply: applyProjectSelectionEvent,
  stateVersion: 1,
}

/** Return a projected id only while it still names an administrator-configured project. */
export function currentProjectId(
  state: GalateaProjectSelectionState | undefined,
  configured: (projectId: string) => boolean,
): string | undefined {
  const projectId = state?.projectId
  return projectId !== null && projectId !== undefined && configured(projectId)
    ? projectId
    : undefined
}
