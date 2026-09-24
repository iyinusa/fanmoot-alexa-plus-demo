/**
 * The MCP trace inspector (W3): the one screen that shows a judge what a
 * voice interaction hides ⟵ HAD-12.
 *
 * - **The header strip** (W3-4) states the protocol, transport, OAuth, server
 *   and model, each read from the live session (`FactsStrip`).
 * - **The frame list** (W3-2): `initialize`, `tools/list`, `tools/call`,
 *   `resources/read`, and the OAuth and model calls around them, each with its
 *   direction, round trip and status, arriving as they happen. A request and
 *   its answer are one row (`exchanges.ts`).
 * - **A row opens** to the pretty-printed JSON-RPC payload (W3-3). Tokens,
 *   one-time values and email addresses were struck out on the server before
 *   the frame was sent (`app/agent/trace.py`), so there is nothing here to
 *   leak.
 * - **Every tool call has a latency badge** (W3-5): green under HAD-6's
 *   400 ms, amber to a second, red past it.
 * - **"Copy as cURL"** (W3-6) on any request that can be replayed, and a
 *   plain reason on any that cannot.
 *
 * It fills whatever holds it — the column beside the device on a wide screen,
 * a sheet on a narrow one (`TracePanel`).
 */
import { AnimatePresence, m } from 'framer-motion'
import { memo, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import type { AuthState, Facts, TraceFrame } from '@/lib/protocol'
import { scrollBehavior } from '@/lib/motion'
import { onTabKeys } from '@/lib/tabs'
import { AlertIcon, CheckIcon } from '@/shell/Icons'
import { hasRedaction, replay, type ReplayContext } from '@/trace/curl'
import { type Exchange, FAST_MS, type Filter, matches, pair, SLOW_MS, toolLatency } from '@/trace/exchanges'
import { FactsStrip, readFacts } from '@/trace/FactsStrip'
import { Json } from '@/trace/Json'

const EASE = [0.22, 1, 0.36, 1] as const

const KIND_TONE: Record<TraceFrame['kind'], string> = {
  mcp: 'text-accent',
  oauth: 'text-code-string',
  model: 'text-code-number',
  session: 'text-muted',
}

/** Who asked, when it was not the agent. */
const ORIGIN_LABEL: Record<NonNullable<TraceFrame['origin']>, string> = {
  view: 'card',
  ambient: 'ticker',
  guide: 'guide',
  verify: 'read-back',
}

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'mcp', label: 'MCP' },
  { id: 'oauth', label: 'OAuth' },
  { id: 'model', label: 'Model' },
  { id: 'session', label: 'Session' },
]

const DIR: Record<Exchange['dir'], { glyph: string; label: string }> = {
  out: { glyph: '→', label: 'sent' },
  in: { glyph: '←', label: 'received' },
  both: { glyph: '⇄', label: 'sent and answered' },
  local: { glyph: '•', label: 'local event' },
}

// ── Pieces ───────────────────────────────────────────────────────────────────
export function LatencyBadge({ ms, graded }: { ms: number; graded: boolean }) {
  const tone = !graded
    ? 'border-line text-muted'
    : ms < FAST_MS
      ? 'border-[var(--fm-green-bright)]/40 bg-[var(--fm-green-bright)]/12 text-code-string'
      : ms < SLOW_MS
        ? 'border-[var(--fm-amber)]/40 bg-[var(--fm-amber)]/12 text-code-number'
        : 'border-[var(--fm-red)]/40 bg-[var(--fm-red)]/12 text-danger'
  const text = ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
  return (
    <m.span
      initial={{ opacity: 0, scale: 0.7 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.25, ease: EASE }}
      title={graded ? `Round trip. HAD-6's budget for a tool call is ${FAST_MS} ms.` : 'Round trip'}
      className={`inline-flex shrink-0 items-center rounded-full border px-1.5 font-mono text-[10px] leading-4 tabular-nums ${tone}`}
    >
      {text}
    </m.span>
  )
}

function Pending() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 font-mono text-[10px] text-faint">
      <span className="size-2 animate-spin rounded-full border border-current border-t-transparent" aria-hidden="true" />
      waiting
    </span>
  )
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // An http:// preview or a denied permission: the old way still works.
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  }
}

function CopyButton({ label, text, disabled, why }: { label: string; text: string | null; disabled?: boolean; why?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), 1600)
    return () => window.clearTimeout(timer)
  }, [state])
  return (
    <button
      type="button"
      disabled={disabled || text === null}
      title={why}
      onClick={async () => setState(text !== null && (await copyText(text)) ? 'done' : 'failed')}
      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink transition-colors hover:border-[var(--fm-accent)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-line"
    >
      {state === 'done' ? <CheckIcon size={12} className="text-code-string" /> : null}
      {state === 'done' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}
    </button>
  )
}

function clock(at: number): string {
  const d = new Date(at)
  const pad = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

function Meta({ frame }: { frame: TraceFrame }) {
  const rows: [string, string][] = [
    ['seq', `#${frame.seq}`],
    ['at', clock(frame.at)],
    ['bytes', frame.bytes.toLocaleString()],
  ]
  if (frame.id !== null) rows.push(['id', String(frame.id)])
  if (frame.http_status) rows.push(['http', String(frame.http_status)])
  if (frame.url) rows.push(['url', frame.url])
  if (frame.pkce) rows.push(['pkce', frame.pkce])
  if (frame.client_mode) rows.push(['client', frame.client_mode])
  if (frame.provider) rows.push(['provider', frame.provider])
  if (frame.tokens_in !== undefined) rows.push(['tokens', `${frame.tokens_in} in · ${frame.tokens_out ?? 0} out`])
  if (frame.cost_usd !== undefined) rows.push(['cost', frame.cost_usd === null ? 'price unknown' : `$${frame.cost_usd.toFixed(5)}`])
  if (frame.model_version) rows.push(['version', frame.model_version])
  return (
    <dl className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-faint">
      {rows.map(([k, v]) => (
        <div key={k} className="flex min-w-0 gap-1">
          <dt>{k}</dt>
          <dd className="truncate text-muted">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Payload({ frame }: { frame: TraceFrame }) {
  const payload = frame.payload as { truncated?: boolean; bytes?: number; preview?: string } | null
  if (payload && typeof payload === 'object' && payload.truncated) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted">
          {(payload.bytes ?? frame.bytes).toLocaleString()} bytes — too large to send whole to the browser, so this is
          the first part. A <code className="font-mono">ui://</code> view is most of that: the card renders it.
        </p>
        <pre className="fm-scroll max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-[1.55] text-muted">
          {payload.preview}
        </pre>
      </div>
    )
  }
  if (frame.payload === null || frame.payload === undefined) {
    return <p className="text-xs text-faint">{frame.summary || 'No payload.'}</p>
  }
  return <Json value={frame.payload} />
}

function Detail({ exchange, ctx }: { exchange: Exchange; ctx: ReplayContext }) {
  const sides = [
    exchange.request && { id: 'request', label: 'Request', frame: exchange.request },
    exchange.response && { id: 'response', label: 'Response', frame: exchange.response },
  ].filter(Boolean) as { id: string; label: string; frame: TraceFrame }[]
  const fallback = { id: 'frame', label: 'Frame', frame: exchange.latest }
  if (sides.length === 0) sides.push(fallback)
  const [side, setSide] = useState(sides.at(-1)?.id ?? 'frame')
  const shown = sides.find((s) => s.id === side) ?? sides[0] ?? fallback
  const script = useMemo(() => replay(exchange, ctx), [exchange, ctx])
  const redacted = sides.some((s) => hasRedaction(s.frame.payload))

  return (
    <m.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.24, ease: EASE }}
      className="overflow-hidden"
    >
      <div className="mt-2 space-y-2.5 rounded-fm-sm border border-line bg-bg/70 p-2.5">
        <div className="flex flex-wrap items-center gap-2">
          {sides.length > 1 && (
            <div role="tablist" aria-label="Payload" onKeyDown={onTabKeys} className="inline-flex rounded-full border border-line p-0.5">
              {sides.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  role="tab"
                  aria-selected={s.id === shown.id}
                  tabIndex={s.id === shown.id ? 0 : -1}
                  onClick={() => setSide(s.id)}
                  className={`rounded-full px-2.5 py-0.5 text-[11px] transition-colors ${
                    s.id === shown.id ? 'bg-[var(--fm-accent)] text-[var(--fm-accent-text)]' : 'text-muted hover:text-ink'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="ml-auto flex gap-1.5">
            <CopyButton
              label="Copy as cURL"
              text={script.ok ? script.script : null}
              why={script.ok ? 'A script that replays this request against the live server' : script.reason}
            />
            <CopyButton label="Copy JSON" text={JSON.stringify(shown.frame.payload ?? null, null, 2)} />
          </div>
        </div>
        {!script.ok && <p className="text-[11px] leading-4 text-faint">Not replayable: {script.reason}</p>}
        <Meta frame={shown.frame} />
        {shown.frame.www_authenticate && (
          <p className="break-all rounded bg-[var(--fm-amber)]/10 px-2 py-1 font-mono text-[10px] leading-4 text-code-number">
            WWW-Authenticate: {shown.frame.www_authenticate}
          </p>
        )}
        <div className="fm-scroll max-h-[42dvh] overflow-auto">
          <Payload frame={shown.frame} />
        </div>
        {redacted && (
          <p className="text-[11px] leading-4 text-faint">
            Tokens, one-time sign-in values and email addresses are struck out on the demo's server, before a frame is
            sent. They never reach this page.
          </p>
        )}
      </div>
    </m.div>
  )
}

const Row = memo(function Row({
  exchange,
  open,
  toggle,
  ctx,
  fresh,
}: {
  exchange: Exchange
  open: boolean
  toggle: (key: number) => void
  ctx: ReplayContext
  /** Arrived after the list first drew, so it glows as it lands (W5-2). */
  fresh: boolean
}) {
  // Decided once, when the row mounts: a glow that stopped because a later
  // render changed its mind would cut off mid-fade.
  const [glow] = useState(fresh)
  const dir = DIR[exchange.dir]
  const error = exchange.status === 'error'
  const httpStatus = exchange.latest.http_status
  const isTool = exchange.method === 'tools/call'
  return (
    <m.li
      initial={{ opacity: 0, x: 14 }}
      // Dimmed, not faded: at 0.72 the ticker tag fell below WCAG AA (W5-5).
      animate={{ opacity: exchange.origin === 'ambient' && !open ? 0.86 : 1, x: 0 }}
      transition={{ duration: 0.3, ease: EASE }}
      className={`relative rounded-fm-sm border px-2.5 py-1.5 transition-colors ${
        open ? 'border-[var(--fm-accent)]/45 bg-surface' : 'border-transparent hover:border-line hover:bg-surface/70'
      }`}
    >
      {glow && (
        <m.span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-fm-sm bg-[var(--fm-accent)]"
          initial={{ opacity: 0.16 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.4, ease: 'easeOut' }}
        />
      )}
      <button
        type="button"
        onClick={() => toggle(exchange.key)}
        aria-expanded={open}
        className="relative block w-full text-left"
      >
        <span className="flex items-center gap-1.5 font-mono text-[11px] leading-5">
          <span className="w-3 shrink-0 text-center text-faint" title={dir.label}>
            {dir.glyph}
            <span className="sr-only">{dir.label}</span>
          </span>
          <span className={`shrink-0 font-bold ${KIND_TONE[exchange.kind]}`}>{exchange.method}</span>
          {exchange.tool && <span className="min-w-0 truncate text-ink">{exchange.tool}</span>}
          {exchange.origin && (
            <span className="shrink-0 rounded bg-raised px-1 text-[9px] uppercase tracking-wide text-muted ring-1 ring-line">
              {ORIGIN_LABEL[exchange.origin]}
            </span>
          )}
          {httpStatus && httpStatus >= 400 && (
            <span className="shrink-0 rounded bg-[var(--fm-red)]/12 px-1 text-[10px] text-danger">{httpStatus}</span>
          )}
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {error && <AlertIcon size={12} className="text-danger" />}
            {exchange.status === 'pending' ? (
              <Pending />
            ) : exchange.ms !== null ? (
              <LatencyBadge ms={exchange.ms} graded={isTool} />
            ) : null}
          </span>
        </span>
        {exchange.latest.summary && (
          <span className={`block truncate pl-[1.125rem] text-[11px] leading-4 ${error ? 'text-danger' : 'text-faint'}`}>
            {exchange.latest.summary}
          </span>
        )}
      </button>
      <AnimatePresence initial={false}>{open && <Detail exchange={exchange} ctx={ctx} />}</AnimatePresence>
    </m.li>
  )
})

// ── The inspector ────────────────────────────────────────────────────────────
type Props = {
  trace: TraceFrame[]
  facts: Facts | null
  auth: AuthState | null
  connected: boolean
  /** The header's own close or collapse control, when whatever holds it has one. */
  action?: ReactNode
}

export function TraceInspector({ trace, facts, auth, connected, action }: Props) {
  const [filter, setFilter] = useState<Filter>('all')
  const [showAmbient, setShowAmbient] = useState(true)
  const [open, setOpen] = useState<number | null>(null)
  const exchanges = useMemo(() => pair(trace), [trace])
  const shown = useMemo(() => exchanges.filter((e) => matches(e, filter, showAmbient)), [exchanges, filter, showAmbient])
  const ambientCount = useMemo(() => exchanges.filter((e) => e.origin === 'ambient').length, [exchanges])
  const latency = useMemo(() => toolLatency(exchanges), [exchanges])
  const lastModel = useMemo(() => {
    for (let i = trace.length - 1; i >= 0; i--) {
      const frame = trace[i]
      if (frame?.kind === 'model' && frame.method === 'model call' && frame.model) return frame
    }
    return null
  }, [trace])

  const ctx = useMemo<ReplayContext>(
    () => ({
      mcpUrl: facts?.mcp?.url ?? null,
      protocol: facts?.mcp?.protocol_version ?? null,
      signedIn: Boolean(auth?.method),
    }),
    [facts?.mcp?.url, facts?.mcp?.protocol_version, auth?.method],
  )
  const toggle = useCallback((key: number) => setOpen((current) => (current === key ? null : key)), [])

  // Which rows were already there when the list first drew, or have been
  // drawn since: only a row that is new to this inspector glows (W5-2), so
  // changing the filter does not light up the whole list again.
  const drawn = useRef<Set<number> | null>(null)
  if (drawn.current === null) drawn.current = new Set(exchanges.map((e) => e.key))
  const seen = drawn.current
  useEffect(() => {
    for (const e of shown) seen.add(e.key)
  }, [shown, seen])

  // ── Always on the newest frame ──────────────────────────────────────────
  // Every new frame brings the list to its end, and so does changing the
  // filter. Opening a row does not: that is the reader looking at something.
  const list = useRef<HTMLDivElement>(null)
  const first = useRef(true)
  useLayoutEffect(() => {
    const el = list.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: first.current ? 'auto' : scrollBehavior() })
    first.current = false
  }, [trace.length, shown.length, filter, showAmbient])

  return (
    <section aria-label="MCP trace" className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-2 px-4 pb-3 pt-4">
        <span className="relative flex size-2.5" aria-hidden="true">
          {connected && <span className="absolute inset-0 animate-ping rounded-full bg-live opacity-50" />}
          <span className={`relative size-2.5 rounded-full ${connected ? 'bg-live' : 'bg-[var(--fm-red)]'}`} />
        </span>
        <h2 className="text-sm">MCP trace</h2>
        <span className="text-xs text-faint">
          {connected ? 'live' : 'reconnecting'} · {trace.length} frames
        </span>
        <span className="ml-auto flex items-center">{action}</span>
      </header>

      <div className="space-y-3 px-4 pb-3">
        <FactsStrip items={readFacts(facts, auth, lastModel)} />

        <div className="flex flex-wrap items-center gap-1.5">
          <div role="group" aria-label="Show frames of kind" className="inline-flex rounded-full border border-line bg-bg/60 p-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}
                className={`rounded-full px-2.5 py-0.5 text-[11px] transition-colors ${
                  filter === f.id ? 'bg-raised text-ink shadow-sm ring-1 ring-line' : 'text-muted hover:text-ink'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          {ambientCount > 0 && (
            <button
              type="button"
              aria-pressed={showAmbient}
              onClick={() => setShowAmbient((v) => !v)}
              title="The corner ticker's own live_scores calls, on the same MCP session"
              className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                showAmbient ? 'border-line text-muted hover:text-ink' : 'border-dashed border-line text-faint line-through'
              }`}
            >
              Ticker · {ambientCount}
            </button>
          )}
        </div>
      </div>

      <div className="relative min-h-0 flex-1 border-t border-line">
        <div ref={list} className="fm-scroll h-full overflow-y-auto overscroll-contain px-2 py-2">
          {shown.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-faint">
              {trace.length === 0 ? 'Opening the MCP session…' : 'Nothing of this kind yet.'}
            </p>
          ) : (
            <ol className="space-y-0.5" aria-live="off">
              {shown.map((exchange) => (
                <Row
                  key={exchange.key}
                  exchange={exchange}
                  open={open === exchange.key}
                  toggle={toggle}
                  ctx={ctx}
                  fresh={!seen.has(exchange.key)}
                />
              ))}
            </ol>
          )}
        </div>
      </div>

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-4 py-2.5 text-[11px] text-faint">
        {latency.count === 0 ? (
          <span>No tool calls yet — ask something.</span>
        ) : (
          <>
            <span>
              <span className="font-mono text-muted">tools/call</span> × {latency.count}
            </span>
            <span className="flex items-center gap-1">
              median {latency.median !== null && <LatencyBadge ms={latency.median} graded />}
            </span>
            <span>
              {latency.fast}/{latency.count} under {FAST_MS} ms
            </span>
          </>
        )}
      </footer>
    </section>
  )
}
