/**
 * The header strip (W3-4): the five facts a judge is checking, each read from
 * the live session rather than written into the page.
 *
 * | Fact      | Where it comes from                                              |
 * |-----------|------------------------------------------------------------------|
 * | Protocol  | `initialize` → `result.protocolVersion`                         |
 * | Transport | the transport the session was opened on, and the session id the |
 * |           | server gave it in `Mcp-Session-Id`                              |
 * | OAuth     | the grant the session holds, or — before anyone signs in — the |
 * |           | PKCE method the authorization server advertises in its metadata |
 * | Server    | the endpoint, and `serverInfo` from `initialize`                |
 * | Model     | the model that answered the last model call on the trace; until |
 * |           | there is one, the head of the configured chain, marked as such  |
 *
 * A fact the session has not reported yet shows as waiting, never as a
 * plausible default. A demo that asserted a protocol the server no longer
 * speaks would be the demo lying.
 */
import type { AuthState, Facts, TraceFrame } from '@/lib/protocol'

type Fact = {
  label: string
  value: string | null
  detail: string
  /** Read from the live session (true), configured but unproven (false). */
  live: boolean
}

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function readFacts(facts: Facts | null, auth: AuthState | null, lastModel: TraceFrame | null): Fact[] {
  const mcp = facts?.connected ? facts.mcp : null
  const advertised = facts?.oauth?.pkce ?? null
  const held = auth?.method ?? null
  const who = auth?.mode === 'demo' ? (auth.label ?? 'demo fan') : auth?.handle ? `@${auth.handle}` : 'a fan'

  return [
    {
      label: 'Protocol',
      value: mcp?.protocol_version ?? null,
      detail: 'Negotiated in initialize: result.protocolVersion.',
      live: Boolean(mcp),
    },
    {
      label: 'Transport',
      value: mcp?.transport ?? null,
      detail: mcp?.session
        ? `Session ${mcp.session}…, named by the server in its Mcp-Session-Id header.`
        : 'The session has not opened yet.',
      live: Boolean(mcp),
    },
    {
      label: 'OAuth',
      value: held ?? (advertised ? `OAuth 2.1 + PKCE ${advertised}` : null),
      detail: held
        ? `Signed in as ${who}. The grant was obtained with PKCE and is held on the demo's server; the browser never sees a token.`
        : advertised && facts?.oauth
          ? `Advertised by ${host(facts.oauth.issuer)} in its RFC 8414 metadata. No one is signed in on this session yet.`
          : 'Waiting for the authorization server’s metadata.',
      live: Boolean(held || advertised),
    },
    {
      label: 'Server',
      value: mcp ? host(mcp.url) : null,
      detail: mcp
        ? `${mcp.server_title ?? mcp.server_name} ${mcp.server_version} at ${mcp.url}, from initialize: result.serverInfo.`
        : 'The session has not opened yet.',
      live: Boolean(mcp),
    },
    {
      label: 'Model',
      value: lastModel?.model ?? facts?.models.primary ?? null,
      detail: lastModel?.model
        ? [
            `Answered the last model call`,
            lastModel.model_version ? ` (${lastModel.model_version})` : '',
            lastModel.ms !== null ? ` in ${(lastModel.ms / 1000).toFixed(1)} s` : '',
            '.',
          ].join('')
        : 'First in the configured chain. Nothing has been asked yet, so nothing has answered.',
      live: Boolean(lastModel?.model),
    },
  ]
}

export function FactsStrip({ items }: { items: Fact[] }) {
  return (
    <dl className="grid grid-cols-2 gap-1.5">
      {items.map((fact) => (
        <div
          key={fact.label}
          title={fact.detail}
          className="group relative min-w-0 rounded-fm-sm border border-line bg-bg/60 px-2.5 py-1.5 last:col-span-full"
        >
          <dt className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.08em] text-faint">
            <span
              className={`size-1.5 shrink-0 rounded-full ${
                fact.value === null ? 'animate-pulse bg-[var(--fm-line-strong)]' : fact.live ? 'bg-live' : 'bg-[var(--fm-amber)]'
              }`}
              aria-hidden="true"
            />
            {fact.label}
            <span className="sr-only">
              {fact.value === null ? '(waiting)' : fact.live ? '(read from the live session)' : '(configured, not yet used)'}
            </span>
          </dt>
          <dd className="mt-0.5 truncate font-mono text-xs leading-5 text-ink">
            {fact.value ?? <span className="text-faint">waiting…</span>}
          </dd>
          <dd className="sr-only">{fact.detail}</dd>
        </div>
      ))}
    </dl>
  )
}
