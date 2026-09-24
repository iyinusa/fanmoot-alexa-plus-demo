/**
 * The live session's facts and its frames, beside the device.
 *
 * Carried over from W1's console so nothing a judge could already see goes
 * missing while the shell is built. W3 replaces this with the proper trace
 * inspector — the header strip, expandable payloads, latency badges and
 * "copy as cURL". Frames the agent did not ask for are marked with who did:
 * a card's own button, or the corner ticker.
 */
import type { Facts as FactsFrame, TraceFrame } from '@/lib/protocol'
import type { useDemoSession } from '@/lib/socket'

type Demo = ReturnType<typeof useDemoSession>

const KIND_TONE: Record<TraceFrame['kind'], string> = {
  mcp: 'text-accent',
  oauth: 'text-pitch',
  model: 'text-amber',
  session: 'text-muted',
}

function Facts({ facts, demo }: { facts: FactsFrame | null; demo: Demo }) {
  const rows: [string, string][] = [
    ['Server', facts?.mcp ? `${facts.mcp.server_name} ${facts.mcp.server_version}` : '—'],
    ['Protocol', facts?.mcp?.protocol_version ?? '—'],
    ['Transport', facts?.mcp?.transport ?? '—'],
    ['Auth', demo.auth?.method ?? 'none — read tools only'],
    ['Model', facts?.models.primary ?? '—'],
  ]
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-faint">{label}</dt>
          <dd className="truncate font-mono text-xs leading-5">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function SessionPanel({ demo }: { demo: Demo }) {
  const recent = demo.trace.slice(-80).reverse()
  return (
    <details open className="group rounded-fm-lg border border-line bg-surface/85 shadow-[var(--fm-shadow)] backdrop-blur">
      <summary className="flex cursor-pointer list-none items-center gap-2 p-4 text-sm text-muted">
        <span className="size-2 rounded-full bg-[var(--fm-green-bright)]" aria-hidden="true" />
        Live MCP session · {demo.trace.length} frames
        <span className="ml-auto text-xs text-faint group-open:hidden">Show</span>
      </summary>
      <div className="space-y-4 px-4 pb-4">
        <Facts facts={demo.facts} demo={demo} />
        <ol className="max-h-[52dvh] space-y-1 overflow-y-auto font-mono text-[11px] leading-4">
          {recent.map((frame) => (
            <li key={frame.seq} className="flex gap-2">
              <span className="w-3 shrink-0 text-faint">{frame.dir === 'out' ? '→' : frame.dir === 'in' ? '←' : '·'}</span>
              <span className={`shrink-0 ${KIND_TONE[frame.kind]}`}>{frame.method}</span>
              {frame.origin && (
                <span className="shrink-0 rounded bg-raised px-1 text-[10px] uppercase tracking-wide text-muted">{frame.origin}</span>
              )}
              <span className={`min-w-0 flex-1 truncate ${frame.status === 'error' ? 'text-danger' : 'text-faint'}`}>{frame.summary}</span>
              {frame.ms !== null && <span className="shrink-0 text-faint">{Math.round(frame.ms)}ms</span>}
            </li>
          ))}
        </ol>
      </div>
    </details>
  )
}
