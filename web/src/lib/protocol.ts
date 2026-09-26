/**
 * The `/ws` wire protocol, version 6 (W1-5, W2, W3, W4, and Alexa's voice).
 * Besides these JSON frames, the fan's voice goes up as binary frames of
 * 16-bit PCM between `listen.start` and `listen.stop` (app/voice/listen.py). Mirrors app/ws.py,
 * app/session.py and app/agent/runtime.py — a frame changed there is changed
 * here, and WIRE_VERSION goes up.
 *
 * The browser holds no token, no model credential and no MCP session: what it
 * knows about a turn, it learns from these frames.
 */

export const WIRE_VERSION = 6

// ── Browser → server ─────────────────────────────────────────────────────────
export type ClientFrame =
  | { type: 'ping'; t: number }
  | { type: 'resume'; session_id: string; token: string }
  | { type: 'turn'; text: string }
  | { type: 'cancel' }
  | { type: 'reset' }
  | { type: 'auth.sign_out' }
  /** The mic is open: the binary frames that follow are the fan's voice. */
  | { type: 'listen.start'; id: number; rate: number }
  /** The fan has stopped: the last words come back as `listen.final`. */
  | { type: 'listen.stop'; id: number }
  | { type: 'listen.cancel'; id: number }
  /** A card's `tools/call` through the host bridge (W2-6). */
  | { type: 'view.call'; id: number; name: string; arguments: Record<string, unknown> }

// ── Server → browser ─────────────────────────────────────────────────────────
export type AuthMode = 'none' | 'linked'

export type AuthState = {
  mode: AuthMode
  handle: string | null
  scopes: string[]
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

/**
 * Whether Alexa speaks with Amazon Polly and listens with Amazon Transcribe
 * (app/voice). Either off, and the browser uses its own Web Speech API.
 */
export type VoiceState = {
  speak: boolean
  listen: boolean
  /** The Polly voice, and its engine (`generative`, or `neural` behind it). */
  voice: string
  engine: string
  language: string
}

/** What Transcribe heard, answering the `listen.start` with the same `id`. */
export type ListenFrame =
  | { type: 'listen.partial'; id: number; text: string }
  | { type: 'listen.final'; id: number; text: string }
  | { type: 'listen.error'; id: number; code: string; message: string }

export type SessionState = {
  type: 'session'
  session_id: string
  resume_token: string
  resumed: boolean
  sign_in_url: string
  auth: AuthState
  voice: VoiceState
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
  /** What the authorization server advertises in its own metadata (W3-4). */
  oauth: { issuer: string; resource: string; pkce: string | null; scopes: string[] } | null
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
  /** An OAuth request's address and body encoding, for "copy as cURL" (W3-6). */
  url?: string
  encoding?: 'form' | 'json' | null
  pkce?: string
  client_mode?: string
  model?: string
  provider?: string
  position?: number
  tokens_in?: number
  tokens_out?: number
  cost_usd?: number | null
  model_version?: string | null
  /** Who asked, when it was not the agent: a card's button, the ticker, the
   * guided scenarios reading what is on, or a post being read back (W4). */
  origin?: 'view' | 'ambient' | 'guide' | 'verify'
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

/** One guided scenario (W4, app/guided.py): lines for the fan to say, in order. */
export type Scenario = {
  id: string
  title: string
  blurb: string
  steps: string[]
  /** Why these words today — "Brentford v Chelsea · Last Friday · 3 takes". */
  basis: string
  /** The tool whose success finishes it: `post_moot`, `predict_match`. */
  goal: string | null
  /** Said again once the fan has signed in. */
  retry: string | null
  /** Said again after a refusal the fan can fix — an unconfirmed email. */
  retry_refused: string | null
  /** Its sign-in opens on the sign-up form. */
  signup: boolean
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
  /** How many pages have the demo open right now; sent when it changes. */
  | { type: 'presence'; online: number }
  | { type: 'error'; code: string; message: string; turn_id?: string }
  | ({ type: 'auth'; reason: string | null } & AuthState)
  | {
      type: 'auth.required'
      tool: string
      status: number
      scope: string | null
      sign_in_url: string
    }
  | { type: 'session.ticket'; sign_in_url: string }
  | ListenFrame
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
      /** Set when the card is not simply the tool's answer — a post read back
       * from Fanmoot (W4-4) — with the take's own address. */
      label?: string
      link?: string
    }
  | { type: 'trace'; frame: TraceFrame }
  | Ambient
  /** The guided scenarios' chips; `live_data` false means the fixed phrases. */
  | { type: 'scenarios'; live_data: boolean; scenarios: Scenario[] }
  | {
      type: 'view.call.result'
      id: number
      result?: CallToolResult
      error?: { code: number; message: string }
    }
