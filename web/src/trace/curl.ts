/**
 * "Copy as cURL" (W3-6): any request on the trace, as a script a judge can
 * paste into a terminal and run against the same live server.
 *
 * An MCP request cannot be replayed on its own. `mcp.fanmoot.com` is stateful
 * Streamable HTTP, so a `tools/call` needs a session: `initialize`, read the
 * `Mcp-Session-Id` header, send `notifications/initialized`, and only then the
 * call. The script does all three and closes the session after. It runs in
 * `sh`, `bash` and `zsh` with nothing but curl, and it was run against
 * production before this was written.
 *
 * What cannot be replayed says why rather than producing something that looks
 * runnable and is not: a frame with a redacted value in it, a model call made
 * with this demo's own credentials, the browser half of a sign-in.
 */
import type { TraceFrame } from '@/lib/protocol'
import type { Exchange } from '@/trace/exchanges'

export type Replay = { ok: true; script: string } | { ok: false; reason: string }

export type ReplayContext = {
  /** The MCP endpoint the session is talking to, from the live `facts`. */
  mcpUrl: string | null
  /** The protocol version it negotiated. */
  protocol: string | null
  /** Whether the session held a grant, so the call went out with a bearer. */
  signedIn: boolean
}

const REDACTED = '[redacted]'

/** Single-quoted for a POSIX shell: `'` becomes `'\''`. */
export function quote(text: string): string {
  return `'${text.replace(/'/g, `'\\''`)}'`
}

function truncated(payload: unknown): boolean {
  return typeof payload === 'object' && payload !== null && (payload as { truncated?: unknown }).truncated === true
}

export function hasRedaction(payload: unknown): boolean {
  return JSON.stringify(payload ?? null).includes(REDACTED)
}

const JSON_HEADERS = [`-H 'Content-Type: application/json'`, `-H 'Accept: application/json, text/event-stream'`]

function curl(lines: string[]): string {
  return lines.join(' \\\n  ')
}

function mcpScript(request: TraceFrame, response: TraceFrame | null, ctx: ReplayContext): Replay {
  if (!ctx.mcpUrl) return { ok: false, reason: 'The MCP session is not open yet.' }
  if (truncated(request.payload)) return { ok: false, reason: 'This request was too large to keep whole on the trace.' }
  if (hasRedaction(request.payload)) {
    return { ok: false, reason: 'It carries a value that was redacted before it left the server.' }
  }
  const protocol = ctx.protocol ?? '2025-11-25'
  const body = JSON.stringify(request.payload)
  const label = request.tool ? `${request.method} ${request.tool}` : request.method

  if (request.method === 'initialize') {
    return {
      ok: true,
      script: [
        `# ${label} — replayed against the live Fanmoot MCP server.`,
        `# -i shows the Mcp-Session-Id header the server answers with.`,
        curl([`curl -sS -i ${quote(ctx.mcpUrl)}`, ...JSON_HEADERS, `--data ${quote(body)}`]),
        '',
      ].join('\n'),
    }
  }

  // A bearer, only if the demo sent one: a call that was refused with a 401
  // went without, and replaying it the same way shows the same challenge.
  const bearer = ctx.signedIn && response?.http_status !== 401
  const session = [...JSON_HEADERS, `-H 'MCP-Protocol-Version: ${protocol}'`, `-H "Mcp-Session-Id: $SID"`]
  const handshake = JSON.stringify({
    jsonrpc: '2.0',
    id: 0,
    method: 'initialize',
    params: { protocolVersion: protocol, capabilities: {}, clientInfo: { name: 'curl', version: '1.0' } },
  })

  return {
    ok: true,
    script: [
      `# ${label} — replayed against the live Fanmoot MCP server.`,
      `# Streamable HTTP, protocol ${protocol}. Runs in sh, bash or zsh.`,
      `MCP=${quote(ctx.mcpUrl)}`,
      ...(bearer
        ? [
            `# The demo sent this with the fan's grant. Use a token of your own:`,
            `# the demo never lets a token reach the browser.`,
            `FANMOOT_TOKEN='…'`,
          ]
        : []),
      '',
      `# 1. Open a session. The server names it in the Mcp-Session-Id header.`,
      `SID=$(${curl([`curl -sS -D - -o /dev/null "$MCP"`, ...JSON_HEADERS, `--data ${quote(handshake)}`])} \\`,
      `  | tr -d '\\r' | awk 'tolower($1) == "mcp-session-id:" { print $2 }')`,
      curl([`curl -sS "$MCP"`, ...session, `--data '{"jsonrpc":"2.0","method":"notifications/initialized"}'`]),
      '',
      `# 2. The frame itself, exactly as the demo sent it.`,
      curl([
        `curl -sS -N "$MCP"`,
        ...session,
        ...(bearer ? [`-H "Authorization: Bearer $FANMOOT_TOKEN"`] : []),
        `--data ${quote(body)}`,
      ]),
      '',
      `# 3. Close the session.`,
      curl([`curl -sS -X DELETE "$MCP"`, `-H 'MCP-Protocol-Version: ${protocol}'`, `-H "Mcp-Session-Id: $SID"`]),
      '',
    ].join('\n'),
  }
}

function oauthScript(request: TraceFrame): Replay {
  if (!request.url) {
    return { ok: false, reason: 'A browser step: the fan signs in on the authorization server itself.' }
  }
  if (hasRedaction(request.payload)) {
    return { ok: false, reason: 'It carries single-use values that were redacted before they left the server.' }
  }
  const [verb] = request.method.split(' ')
  const lines = [`curl -sS${verb === 'GET' ? '' : ` -X ${verb}`} ${quote(request.url)}`]
  const payload = request.payload
  if (payload && typeof payload === 'object') {
    if (request.encoding === 'form') {
      for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
        lines.push(`--data-urlencode ${quote(`${key}=${String(value)}`)}`)
      }
    } else {
      lines.push(`-H 'Content-Type: application/json'`, `--data ${quote(JSON.stringify(payload))}`)
    }
  }
  return { ok: true, script: `# ${request.method} — ${request.summary}\n${curl(lines)}\n` }
}

export function replay(exchange: Exchange, ctx: ReplayContext): Replay {
  const request = exchange.request
  switch (exchange.kind) {
    case 'mcp':
      if (!request) return { ok: false, reason: 'Sent by the server, not a request to replay.' }
      if (request.method === 'response') return { ok: false, reason: 'The demo answering the server, not a request.' }
      return mcpScript(request, exchange.response, ctx)
    case 'oauth':
      return request ? oauthScript(request) : { ok: false, reason: 'A response, without its request on the trace.' }
    case 'model':
      return { ok: false, reason: "A call to the model provider, made with this demo's own credentials." }
    case 'session':
      return { ok: false, reason: "Part of this visitor's own MCP session, not a request on its own." }
  }
}
