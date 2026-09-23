/**
 * W0's screen: proof that the brand, the fonts, the motion and `/ws` are all
 * wired through. The Alexa+ shell (W2) replaces it; the landing state (W5-1)
 * is where a judge's first ten seconds get designed properly.
 */
import { motion } from 'framer-motion'

import { FieldBackdrop } from '@/components/FieldBackdrop'
import { type SocketStatus, useDemoSocket } from '@/lib/socket'

const STATUS_LABEL: Record<SocketStatus, string> = {
  connecting: 'Connecting',
  open: 'Connected',
  closed: 'Reconnecting',
}

const STATUS_DOT: Record<SocketStatus, string> = {
  connecting: 'bg-amber',
  open: 'bg-live',
  closed: 'bg-danger',
}

export function App() {
  const { status, hello, rttMs } = useDemoSocket()

  return (
    <main className="fm-motion relative flex min-h-dvh items-center justify-center px-4 py-12">
      <FieldBackdrop />

      <motion.section
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
        className="w-full max-w-md rounded-fm-lg border border-line bg-surface/80 p-6 shadow-[var(--fm-shadow)] backdrop-blur"
      >
        <div className="flex items-center gap-3">
          <img src="/icons/icon-192.png" alt="" width={40} height={40} className="rounded-fm-sm" />
          <div>
            <h1 className="text-xl">Fanmoot on Alexa+</h1>
            <p className="text-sm text-muted">Where fans debate football — now by voice.</p>
          </div>
        </div>

        <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-faint">Channel</dt>
          <dd className="flex items-center gap-2" aria-live="polite">
            <motion.span
              key={status}
              initial={{ scale: 0.4 }}
              animate={{ scale: 1 }}
              className={`inline-block size-2 rounded-full ${STATUS_DOT[status]}`}
            />
            {STATUS_LABEL[status]}
            {rttMs !== null && status === 'open' && (
              <span className="fm-score text-faint">{rttMs} ms</span>
            )}
          </dd>

          <dt className="text-faint">Build</dt>
          <dd>{hello ? `${hello.service} ${hello.version} · wire v${hello.wire}` : '—'}</dd>

          <dt className="text-faint">MCP server</dt>
          <dd className="truncate">{hello?.mcp_url ?? '—'}</dd>
        </dl>

        <p className="mt-6 border-t border-line pt-4 text-xs text-faint">
          Skeleton build. The conversation, the device shell and the live MCP trace arrive next.
        </p>
      </motion.section>
    </main>
  )
}
