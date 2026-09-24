/**
 * Trace frames, paired into the exchanges a judge actually reads (W3-2).
 *
 * The server sends one frame per message: a `tools/call` going out, and a
 * moment later its answer coming back. Shown raw, the request and its answer
 * are two rows with the rest of the session between them. So a request and
 * its response are one row here — the request's direction, the response's
 * status and round trip — and both payloads are a tap away.
 *
 * JSON-RPC pairs by id. Ids restart with each MCP session (a reconnect sends
 * a fresh `initialize` with id 0), so a response pairs with the most recent
 * request still waiting under its id, never an older one. OAuth is plain HTTP
 * without ids and one request at a time per endpoint, so it pairs by method.
 * Everything else — notifications, model calls, session events — stands alone.
 */
import type { TraceFrame } from '@/lib/protocol'

export type Exchange = {
  /** Stable across renders: the first frame's sequence number. */
  key: number
  kind: TraceFrame['kind']
  method: string
  /** `both` for a request and its answer, otherwise the one frame's own. */
  dir: TraceFrame['dir'] | 'both'
  request: TraceFrame | null
  response: TraceFrame | null
  /** The frame that describes the exchange best: the answer, once it is in. */
  latest: TraceFrame
  status: TraceFrame['status']
  ms: number | null
  at: number
  tool: string | null
  origin: TraceFrame['origin'] | null
}

function single(frame: TraceFrame): Exchange {
  return {
    key: frame.seq,
    kind: frame.kind,
    method: frame.method,
    dir: frame.dir,
    request: frame.dir === 'out' ? frame : null,
    response: frame.dir === 'in' ? frame : null,
    latest: frame,
    status: frame.status,
    ms: frame.ms,
    at: frame.at,
    tool: frame.tool ?? null,
    origin: frame.origin ?? null,
  }
}

function answered(exchange: Exchange, response: TraceFrame): Exchange {
  return {
    ...exchange,
    dir: 'both',
    response,
    latest: response,
    status: response.status,
    ms: response.ms,
    tool: exchange.tool ?? response.tool ?? null,
    origin: exchange.origin ?? response.origin ?? null,
  }
}

export function pair(frames: TraceFrame[]): Exchange[] {
  const out: Exchange[] = []
  const waitingRpc = new Map<string | number, number>()
  const waitingHttp = new Map<string, number>()

  for (const frame of frames) {
    const isRequest = frame.dir === 'out' && frame.status === 'pending'
    if (frame.kind === 'mcp' && frame.id !== null) {
      if (isRequest) {
        waitingRpc.set(frame.id, out.length)
        out.push(single(frame))
        continue
      }
      const at = frame.dir === 'in' ? waitingRpc.get(frame.id) : undefined
      const request = at === undefined ? undefined : out[at]
      if (at !== undefined && request) {
        waitingRpc.delete(frame.id)
        out[at] = answered(request, frame)
        continue
      }
    } else if (frame.kind === 'oauth') {
      if (isRequest) {
        waitingHttp.set(frame.method, out.length)
        out.push(single(frame))
        continue
      }
      const at = frame.dir === 'in' ? waitingHttp.get(frame.method) : undefined
      const request = at === undefined ? undefined : out[at]
      if (at !== undefined && request) {
        waitingHttp.delete(frame.method)
        out[at] = answered(request, frame)
        continue
      }
    }
    out.push(single(frame))
  }
  return out
}

export type Filter = 'all' | TraceFrame['kind']

export function matches(exchange: Exchange, filter: Filter, showAmbient: boolean): boolean {
  if (!showAmbient && exchange.origin === 'ambient') return false
  return filter === 'all' || exchange.kind === filter
}

/** Round trips of the tool calls the agent and the cards made — HAD-6's number. */
export function toolLatency(exchanges: Exchange[]): { count: number; median: number | null; fast: number } {
  const times = exchanges
    .filter((e) => e.method === 'tools/call' && e.response && e.ms !== null)
    .map((e) => e.ms as number)
    .sort((a, b) => a - b)
  if (times.length === 0) return { count: 0, median: null, fast: 0 }
  const mid = Math.floor(times.length / 2)
  const upper = times[mid] ?? 0
  const median = times.length % 2 ? upper : ((times[mid - 1] ?? upper) + upper) / 2
  return { count: times.length, median, fast: times.filter((ms) => ms < FAST_MS).length }
}

/** HAD-6's budget for a tool call: a voice surface cannot wait. */
export const FAST_MS = 400
/** Past this a pause is audible, whatever the cause. */
export const SLOW_MS = 1000
