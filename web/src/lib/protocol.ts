/**
 * The `/ws` wire protocol, version 3 (W1-5, W2). Mirrors app/ws.py,
 * app/session.py and app/agent/runtime.py — a frame changed there is changed
 * here, and WIRE_VERSION goes up.
 *
 * The browser holds no token, no model credential and no MCP session: what it
 * knows about a turn, it learns from these frames.
 */

export const WIRE_VERSION = 3

// ── Browser → server ─────────────────────────────────────────────────────────
export type ClientFrame =
  | { type: 'ping'; t: number }
  | { type: 'resume'; session_id: string; token: string }
  | { type: 'turn'; text: string }
  | { type: 'cancel' }
  | { type: 'reset' }
  | { type: 'auth.demo' }
  | { type: 'auth.sign_out' }
  /** A card's `tools/call` through the host bridge (W2-6). */
  | { type: 'view.call'; id: number; name: string; arguments: Record<string, unknown> }

// ── Server → browser ─────────────────────────────────────────────────────────
export type AuthMode = 'none' | 'demo' | 'linked'

export type AuthState = {
  mode: AuthMode
  handle: string | null
  scopes: string[]
  /** The demo account's label, when that is who the fan is. */
  label: string | null
  /** "OAuth 2.1 + PKCE S256" — only when a grant is actually held. */
  method: string | null
  client_mode: 'static' | 'metadata_document' | 'dcr' | null
}

export type Hello = {
  type: 'hello'
  session_id: string
  resume_token: string
  service: string
  version: string
  wire: number
  mcp_url: string
}

export type SessionState = {
  type: 'session'
  session_id: string
  resume_token: string
  resumed: boolean
  sign_in_url: string
  auth: AuthState
  demo_fan: { available: boolean; label: string }
  turns_left: number
  busy: boolean
}

/** What the live MCP session reports about itself — W3-4's header strip. */
export type Facts = {
  type: 'facts'
  connected: boolean
  mcp: {
    url: string
    transport: string
    protocol_version: string
    server_name: string
    server_title: string | null
    server_version: string
    session: string | null
    connected_at: number
  } | null
  auth: AuthState
  models: { chain: string[]; primary: string | null; override: boolean }
}

export type ModelCall = {
  model: string
  provider: string
  position: number
  ms: number
  tokens_in: number
  tokens_out: number
  cost_usd: number | null
  model_version: string | null
}

export type TurnEnd = {
  type: 'turn.end'
  turn_id: string
  status: 'ok' | 'error' | 'cancelled'
  text: string
  ms: number
  tools: string[]
  /** The model the fan heard (W1-10). */
  model: string | null
  provider: string | null
  models: string[]
  calls: ModelCall[]
  failovers: { from: string; error: string; retryable: boolean; fell_through: boolean }[]
  skipped: { model: string; reason: string }[]
  usage: {
    calls: number
    tokens_in: number
    tokens_out: number
    cost_usd: number | null
    cost_complete: boolean
  }
}

export type TraceFrame = {
  seq: number
  at: number
  kind: 'mcp' | 'oauth' | 'model' | 'session'
  dir: 'in' | 'out' | 'local'
  method: string
  id: string | number | null
  status: 'ok' | 'error' | 'pending'
  ms: number | null
  summary: string
  bytes: number
  payload: unknown
  tool?: string | null
  http_status?: number | null
  www_authenticate?: string | null
  model?: string
  provider?: string
  /** Who asked, when it was not the agent: a card's button, or the ticker. */
  origin?: 'view' | 'ambient'
}

/** A match as the corner ticker keeps it (W2-7): `fanmoot-mcp`'s MatchCard, trimmed. */
export type TickerSide = {
  slug: string
  name: string
  short_name: string
  crest_url?: string | null
  colour?: string
  score?: number | null
}

export type TickerMatch = {
  id: string
  status: string
  competition?: string | null
  home?: TickerSide | null
  away?: TickerSide | null
  minute?: number | null
  added?: number | null
  period?: string | null
  score_confirmed?: boolean
  kickoff_at?: string
  kickoff_spoken?: string
  url?: string | null
}

export type Ambient = {
  type: 'ambient'
  /** True: these are in play. False: nothing is, and these are next. */
  live: boolean
  matches: TickerMatch[]
  spoken: string
  at: number
}

/** A tool's answer as JSON — MCP's CallToolResult. */
export type CallToolResult = {
  content?: { type: string; text?: string }[]
  structuredContent?: Record<string, unknown>
  isError?: boolean
}

export type ServerFrame =
  | Hello
  | SessionState
  | Facts
  | TurnEnd
  | { type: 'pong'; t: number | null }
  | { type: 'error'; code: string; message: string; turn_id?: string }
  | ({ type: 'auth'; reason: string | null } & AuthState)
  | {
      type: 'auth.required'
      tool: string
      status: number
      scope: string | null
      sign_in_url: string
      demo_available: boolean
    }
  | { type: 'session.ticket'; sign_in_url: string }
  | { type: 'session.reset' }
  | { type: 'turn.start'; turn_id: string; text: string }
  | { type: 'text.delta'; turn_id: string; text: string }
  | {
      type: 'tool.start'
      turn_id: string
      call_id: string
      name: string
      title: string | null
      args: Record<string, unknown>
      write: boolean
    }
  | {
      type: 'tool.end'
      turn_id: string
      call_id: string
      name: string
      ok: boolean
      ms: number
      summary: string
      reason?: string
      view?: string | null
    }
  | { type: 'view.resource'; uri: string; mime: string; html: string; meta: Record<string, unknown> }
  | {
      type: 'view'
      turn_id: string
      call_id: string
      uri: string
      tool: string
      input: Record<string, unknown>
      result: Record<string, unknown>
    }
  | { type: 'trace'; frame: TraceFrame }
  | Ambient
  | {
      type: 'view.call.result'
      id: number
      result?: CallToolResult
      error?: { code: number; message: string }
    }
