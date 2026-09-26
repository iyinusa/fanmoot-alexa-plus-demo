/**
 * The browser's end of `/ws` (app/ws.py): one visitor session, as React state.
 *
 * W0's transport is unchanged underneath — connect, `ping` for the round trip,
 * reconnect with backoff. W1 adds the session on top:
 *
 * - **Resume.** The session id and resume token are kept in sessionStorage, so
 *   a dropped socket (a phone at the judging table) or a reload in the same
 *   tab picks the conversation — and the fan's sign-in — back up rather than
 *   starting again. The server holds the session for FMD_SESSION_GRACE_S.
 * - **Turns.** `ask()` sends a turn; `text.delta`, `tool.*`, `view*` and
 *   `turn.end` build it up as it streams.
 * - **Sign-in.** `signIn()` opens the session's single-use `sign_in_url` in a
 *   popup — inside the click, or the browser blocks it. The server finishes
 *   the OAuth flow and says so with an `auth` frame; this page never sees a
 *   token.
 *
 * W2 adds three things the Alexa+ shell needs:
 *
 * - **Cards.** Every `view` frame is kept (the last few), so the shell can
 *   stack them rather than show only the newest.
 * - **A card's own calls.** `callFromView()` sends a `view.call` and resolves
 *   with the server's `view.call.result` — the thread view's reply composer,
 *   posting over the visitor's own MCP session (W2-6).
 * - **The ticker.** `ambient` is what the corner of the screen shows (W2-7).
 *
 * W5-4 adds what the device needs to stay in character when things go wrong:
 * when the next reconnect is due (`retryAt`) and a way to try at once
 * (`reconnect()`); the error `code` on a failed turn, so Alexa can apologise
 * for the right thing; `fault`, for the errors that stop the demo rather than
 * pass as a notice; and `ask()` saying whether it was sent, so a question
 * typed while the socket is down stays in the box instead of vanishing.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react'

import { type FaultKind, SCREEN_CODES } from '@/lib/faults'
import { WIRE_VERSION } from '@/lib/protocol'
import type {
  AuthState,
  ClientFrame,
  Facts,
  Hello,
  ServerFrame,
  SessionState,
  Ambient,
  CallToolResult,
  Scenario,
  TraceFrame,
  TurnEnd,
} from '@/lib/protocol'

export type SocketStatus = 'connecting' | 'open' | 'closed'

export type ToolCall = {
  callId: string
  name: string
  args: Record<string, unknown>
  write: boolean
  status: 'running' | 'ok' | 'error'
  ms?: number
  summary?: string
  reason?: string
  view?: string | null
}

export type Turn = {
  id: string
  question: string
  reply: string
  status: 'running' | 'ok' | 'error' | 'cancelled'
  tools: ToolCall[]
  end?: TurnEnd
  error?: string
  /** The server's code for `error` (`no_model`, `mcp_unavailable`…). */
  errorCode?: string
}

export type ViewResource = { uri: string; mime: string; html: string; meta: Record<string, unknown> }
export type ViewPayload = Extract<ServerFrame, { type: 'view' }>
export type AuthRequired = Extract<ServerFrame, { type: 'auth.required' }>

type State = {
  status: SocketStatus
  /** When the next reconnect attempt is due, while `closed`. */
  retryAt: number | null
  /** Whether this page has ever had an open socket. */
  everOpen: boolean
  /** An error that stops the demo and gets a screen of its own (W5-4). */
  fault: FaultKind | null
  hello: Hello | null
  rttMs: number | null
  session: SessionState | null
  facts: Facts | null
  auth: AuthState | null
  authReason: string | null
  turns: Turn[]
  trace: TraceFrame[]
  views: Record<string, ViewResource>
  lastView: ViewPayload | null
  /** Every card this conversation has shown, oldest first, the last few kept. */
  cards: ViewPayload[]
  ambient: Ambient | null
  /** How many people have the demo open right now; null while not connected. */
  presence: number | null
  /** The guided scenarios (W4), and whether they were written from live data. */
  scenarios: { live: boolean; list: Scenario[] } | null
  authRequired: AuthRequired | null
  error: { code: string; message: string } | null
}

const INITIAL: State = {
  status: 'connecting',
  retryAt: null,
  everOpen: false,
  fault: null,
  hello: null,
  rttMs: null,
  session: null,
  facts: null,
  auth: null,
  authReason: null,
  turns: [],
  trace: [],
  views: {},
  lastView: null,
  cards: [],
  ambient: null,
  presence: null,
  scenarios: null,
  authRequired: null,
  error: null,
}

/** Cards kept for the stack. A shelf, not an archive. */
const CARD_LIMIT = 6

/** How long a card's call may take before it is given up on. */
const VIEW_CALL_TIMEOUT_MS = 30_000

/** The trace panel shows the recent past, not the whole session. */
const TRACE_LIMIT = 300

type Action =
  | { kind: 'status'; status: SocketStatus; retryAt?: number }
  | { kind: 'rtt'; ms: number }
  | { kind: 'frame'; frame: ServerFrame }
  | { kind: 'dismiss' }

function updateTurn(turns: Turn[], id: string, change: (turn: Turn) => Turn): Turn[] {
  return turns.map((turn) => (turn.id === id ? change(turn) : turn))
}

function reduce(state: State, action: Action): State {
  if (action.kind === 'status') {
    return {
      ...state,
      status: action.status,
      retryAt: action.retryAt ?? null,
      everOpen: state.everOpen || action.status === 'open',
      // A count from a socket that has gone is a guess; the next one sends it afresh.
      presence: action.status === 'open' ? state.presence : null,
    }
  }
  if (action.kind === 'rtt') return { ...state, rttMs: action.ms }
  if (action.kind === 'dismiss') return { ...state, error: null, authRequired: null }

  const frame = action.frame
  switch (frame.type) {
    case 'hello':
      // A tab left open across a deploy speaks an older protocol than the
      // server it reconnects to. Say so, rather than half-work.
      // A session was granted, so "the demo is full" is over.
      return frame.wire === WIRE_VERSION
        ? { ...state, hello: frame, fault: state.fault === 'capacity' ? null : state.fault }
        : { ...state, hello: frame, fault: 'stale' }
    case 'session':
      // The conversation on screen is kept: a reconnecting socket sees the
      // fresh session's frame first and the resumed one a moment later.
      // It is cleared by a reset, or when a resume fails.
      return { ...state, session: frame, auth: frame.auth }
    case 'session.ticket':
      return state.session ? { ...state, session: { ...state.session, sign_in_url: frame.sign_in_url } } : state
    case 'session.reset':
      return { ...state, turns: [], lastView: null, cards: [], authRequired: null, error: null }
    case 'facts':
      return { ...state, facts: frame, auth: frame.auth }
    case 'auth': {
      const { type: _type, reason, ...auth } = frame
      return { ...state, auth, authReason: reason, authRequired: auth.mode === 'none' ? state.authRequired : null }
    }
    case 'auth.required':
      return { ...state, authRequired: frame }
    case 'turn.start':
      return {
        ...state,
        error: null,
        turns: [...state.turns, { id: frame.turn_id, question: frame.text, reply: '', status: 'running', tools: [] }],
      }
    case 'text.delta':
      return { ...state, turns: updateTurn(state.turns, frame.turn_id, (t) => ({ ...t, reply: t.reply + frame.text })) }
    case 'tool.start':
      return {
        ...state,
        turns: updateTurn(state.turns, frame.turn_id, (t) => ({
          ...t,
          tools: [...t.tools, { callId: frame.call_id, name: frame.name, args: frame.args, write: frame.write, status: 'running' }],
        })),
      }
    case 'tool.end':
      return {
        ...state,
        turns: updateTurn(state.turns, frame.turn_id, (t) => ({
          ...t,
          tools: t.tools.map((call) =>
            call.callId === frame.call_id
              ? { ...call, status: frame.ok ? 'ok' : 'error', ms: frame.ms, summary: frame.summary, reason: frame.reason, view: frame.view ?? null }
              : call,
          ),
        })),
      }
    case 'view.resource':
      return { ...state, views: { ...state.views, [frame.uri]: frame } }
    case 'view':
      return { ...state, lastView: frame, cards: [...state.cards.slice(-(CARD_LIMIT - 1)), frame] }
    case 'ambient':
      return { ...state, ambient: frame }
    case 'presence':
      return { ...state, presence: frame.online }
    case 'scenarios':
      return { ...state, scenarios: { live: frame.live_data, list: frame.scenarios } }
    case 'view.call.result':
      // Settled in the socket handler, where the promise that waits for it lives.
      return state
    case 'turn.end':
      return {
        ...state,
        turns: updateTurn(state.turns, frame.turn_id, (t) => ({
          ...t,
          status: frame.status,
          // The whole answer, in case a delta was missed across a reconnect.
          reply: frame.text || t.reply,
          end: frame,
        })),
        session: state.session ? { ...state.session, turns_left: Math.max(0, state.session.turns_left - 1) } : state.session,
      }
    case 'trace':
      return { ...state, trace: [...state.trace.slice(-(TRACE_LIMIT - 1)), frame.frame] }
    case 'error':
      if (SCREEN_CODES[frame.code]) return { ...state, fault: SCREEN_CODES[frame.code]! }
      if (frame.code === 'resume_failed') {
        // The old session has ended on the server, and its conversation with it.
        return { ...state, error: null, turns: [], lastView: null, cards: [] }
      }
      if (frame.turn_id) {
        return {
          ...state,
          error: frame,
          turns: updateTurn(state.turns, frame.turn_id, (t) => ({ ...t, error: frame.message, errorCode: frame.code })),
        }
      }
      return { ...state, error: frame }
    case 'pong':
      return state
    default:
      // A frame this build does not know — a newer server. Ignored, never
      // fatal: a reducer that returned nothing here blanked the whole page.
      return state
  }
}

// ── Resume ───────────────────────────────────────────────────────────────────
const STORE_KEY = 'fm-demo-session'

type Stored = { session_id: string; resume_token: string }

function readStored(): Stored | null {
  try {
    const raw = window.sessionStorage.getItem(STORE_KEY)
    return raw ? (JSON.parse(raw) as Stored) : null
  } catch {
    return null
  }
}

function writeStored(value: Stored | null) {
  try {
    if (value) window.sessionStorage.setItem(STORE_KEY, JSON.stringify(value))
    else window.sessionStorage.removeItem(STORE_KEY)
  } catch {
    // A private window without storage: the session simply is not resumed.
  }
}

/** Same origin, so the CSP's `connect-src 'self'` covers it and no URL is configured. */
function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/ws`
}

/** 0.5 s, 1 s, 2 s … capped at 10 s: quick on a blip, quiet on an outage. */
const backoff = (attempt: number) => Math.min(10_000, 500 * 2 ** attempt)

export function useDemoSession() {
  const [state, dispatch] = useReducer(reduce, INITIAL)
  const socketRef = useRef<WebSocket | null>(null)
  const signInUrl = useRef<string | null>(null)
  signInUrl.current = state.session?.sign_in_url ?? null
  const viewCalls = useRef(new Map<number, { resolve: (r: CallToolResult) => void; reject: (e: Error) => void; timer: number }>())
  const nextViewCall = useRef(1)
  const connectNow = useRef<() => void>(() => {})

  useEffect(() => {
    let attempt = 0
    let retry: number | undefined
    let pinger: number | undefined
    let disposed = false

    const connect = () => {
      dispatch({ kind: 'status', status: 'connecting' })
      const socket = new WebSocket(socketUrl())
      socketRef.current = socket
      const send = (frame: ClientFrame) => socket.send(JSON.stringify(frame))

      socket.onopen = () => {
        attempt = 0
        dispatch({ kind: 'status', status: 'open' })
        const ping = () => send({ type: 'ping', t: performance.now() })
        ping()
        pinger = window.setInterval(ping, 15_000)
      }

      socket.onmessage = (event) => {
        let frame: ServerFrame
        try {
          frame = JSON.parse(String(event.data)) as ServerFrame
        } catch {
          return
        }
        if (frame.type === 'hello') {
          const stored = readStored()
          if (stored && stored.session_id !== frame.session_id) {
            send({ type: 'resume', session_id: stored.session_id, token: stored.resume_token })
          }
        } else if (frame.type === 'session') {
          writeStored({ session_id: frame.session_id, resume_token: frame.resume_token })
        } else if (frame.type === 'error' && frame.code === 'session_expired') {
          // Not on `resume_failed`: by then the fresh session's own `session`
          // frame has already replaced what was stored, and that is the one
          // to resume next time.
          writeStored(null)
        } else if (frame.type === 'view.call.result') {
          const waiting = viewCalls.current.get(frame.id)
          if (waiting) {
            viewCalls.current.delete(frame.id)
            window.clearTimeout(waiting.timer)
            if (frame.error) waiting.reject(Object.assign(new Error(frame.error.message), { code: frame.error.code }))
            else waiting.resolve(frame.result ?? {})
          }
          return
        } else if (frame.type === 'pong') {
          if (typeof frame.t === 'number') dispatch({ kind: 'rtt', ms: Math.round(performance.now() - frame.t) })
          return
        }
        dispatch({ kind: 'frame', frame })
      }

      socket.onclose = () => {
        window.clearInterval(pinger)
        if (disposed || socketRef.current !== socket) return
        const wait = backoff(attempt++)
        dispatch({ kind: 'status', status: 'closed', retryAt: Date.now() + wait })
        retry = window.setTimeout(connect, wait)
      }
    }

    // "Try now" on the device's fault screen, and the browser coming back
    // online: skip the rest of the backoff.
    connectNow.current = () => {
      const socket = socketRef.current
      if (disposed || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return
      window.clearTimeout(retry)
      attempt = 0
      connect()
    }
    const online = () => connectNow.current()
    window.addEventListener('online', online)

    connect()
    return () => {
      // StrictMode mounts twice in development; the first socket is closed
      // here and must not schedule a reconnect behind the second.
      disposed = true
      window.removeEventListener('online', online)
      window.clearTimeout(retry)
      window.clearInterval(pinger)
      socketRef.current?.close()
    }
  }, [])

  /** False when there is no open socket to send it on. */
  const send = useCallback((frame: ClientFrame): boolean => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(frame))
    return true
  }, [])

  return {
    ...state,
    /** False when it could not be sent: the socket is down. */
    ask: useCallback((text: string) => send({ type: 'turn', text }), [send]),
    reconnect: useCallback(() => connectNow.current(), []),
    cancel: useCallback(() => send({ type: 'cancel' }), [send]),
    reset: useCallback(() => send({ type: 'reset' }), [send]),
    useDemoAccount: useCallback(() => send({ type: 'auth.demo' }), [send]),
    signOut: useCallback(() => send({ type: 'auth.sign_out' }), [send]),
    dismiss: useCallback(() => dispatch({ kind: 'dismiss' }), []),
    /** A card's `tools/call`, made by the server on the visitor's MCP session (W2-6). */
    callFromView: useCallback(
      (name: string, args: Record<string, unknown>): Promise<CallToolResult> =>
        new Promise((resolve, reject) => {
          const socket = socketRef.current
          if (socket?.readyState !== WebSocket.OPEN) {
            reject(new Error('Not connected just now — try again in a moment.'))
            return
          }
          const id = nextViewCall.current++
          const timer = window.setTimeout(() => {
            viewCalls.current.delete(id)
            reject(new Error('That took too long. It may still have gone through.'))
          }, VIEW_CALL_TIMEOUT_MS)
          viewCalls.current.set(id, { resolve, reject, timer })
          socket.send(JSON.stringify({ type: 'view.call', id, name, arguments: args } satisfies ClientFrame))
        }),
      [],
    ),
    /**
     * Must be called inside the click that asked for it, or the popup is
     * blocked. `signup` opens Fanmoot's sign-up form first (W4-5).
     */
    signIn: useCallback((options?: { signup?: boolean }) => {
      if (signInUrl.current) {
        const url = options?.signup ? `${signInUrl.current}&signup=1` : signInUrl.current
        window.open(url, 'fanmoot-sign-in', 'popup,width=480,height=760')
      }
    }, []),
  }
}
