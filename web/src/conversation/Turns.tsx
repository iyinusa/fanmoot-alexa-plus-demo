/**
 * The conversation on the screen (W2-3): what the fan said, the tools Alexa
 * reached for, and the answer arriving word by word.
 *
 * Words, not characters or chunks. The model streams in pieces of arbitrary
 * size; splitting them into words and letting each one settle in on its own
 * reads as speech rather than as a download. A word that grows across two
 * deltas ("Arse" → "Arsenal") keeps its place and does not animate twice.
 *
 * A turn that failed is answered in Alexa's own voice (W5-4): an apology
 * that says what to do next, spoken like any reply, with the server's error
 * code in small print underneath for anyone who wants it. Never a red
 * banner, never a stack trace.
 *
 * Not a live region (W5-5). Announcing a reply word by word as it streams
 * would read a screen-reader user every fragment; the shell announces each
 * finished answer once instead.
 */
import { AnimatePresence, m } from 'framer-motion'
import { memo, useEffect, useRef } from 'react'

import { turnApology } from '@/lib/faults'
import { scrollBehavior } from '@/lib/motion'
import type { ToolCall, Turn } from '@/lib/socket'
import { AlertIcon, CheckIcon } from '@/shell/Icons'

const EASE = [0.22, 1, 0.36, 1] as const

const Words = memo(function Words({ text, live }: { text: string; live: boolean }) {
  const words = text.split(/(\s+)/).filter(Boolean)
  return (
    <>
      {words.map((word, i) =>
        /^\s+$/.test(word) ? (
          ' '
        ) : (
          <m.span
            key={i}
            initial={live ? { opacity: 0, y: 6, filter: 'blur(4px)' } : false}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            transition={{ duration: 0.32, ease: EASE }}
            className="inline-block"
          >
            {word}
          </m.span>
        ),
      )}
    </>
  )
})

function ToolChip({ call }: { call: ToolCall }) {
  const tone =
    call.status === 'error'
      ? 'border-[var(--fm-red)]/40 text-[var(--fm-red)]'
      : call.status === 'ok'
        ? 'border-line text-muted'
        : 'border-[var(--fm-accent)]/50 text-ink'
  return (
    <m.li
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.22, ease: EASE }}
      title={call.summary}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] ${tone}`}
    >
      {call.status === 'running' ? (
        <span className="size-2 animate-spin rounded-full border border-current border-t-transparent" aria-hidden="true" />
      ) : call.status === 'ok' ? (
        <CheckIcon size={11} />
      ) : (
        <AlertIcon size={11} />
      )}
      {call.name}
      {call.write && <span className="text-[var(--fm-amber)]">write</span>}
      {call.ms !== undefined && <span className="text-faint">{Math.round(call.ms)} ms</span>}
      <span className="sr-only">{call.status === 'running' ? 'running' : call.status === 'ok' ? 'done' : 'failed'}</span>
    </m.li>
  )
}

function Thinking() {
  return (
    <span className="inline-flex items-center gap-1 py-2" aria-label="Alexa is thinking">
      {[0, 1, 2].map((i) => (
        <m.span
          key={i}
          className="size-2 rounded-full bg-[var(--fm-accent)]"
          animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
          transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.16, ease: 'easeInOut' }}
        />
      ))}
    </span>
  )
}

function TurnView({ turn, latest }: { turn: Turn; latest: boolean }) {
  const live = turn.status === 'running'
  return (
    <m.li
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: latest ? 1 : 0.62, y: 0 }}
      transition={{ duration: 0.36, ease: EASE }}
      className="space-y-2.5"
    >
      <div className="flex justify-end">
        <p className="max-w-[88%] rounded-[18px_18px_4px_18px] bg-[var(--fm-blue)] px-3.5 py-2 text-[15px] leading-snug text-white shadow-sm">
          {turn.question}
        </p>
      </div>

      {turn.tools.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Tools Alexa used">
          {turn.tools.map((call) => (
            <ToolChip key={call.callId} call={call} />
          ))}
        </ul>
      )}

      <div className="max-w-[94%] text-lg leading-relaxed text-ink sm:text-xl">
        {turn.reply ? <Words text={turn.reply} live={live} /> : live ? <Thinking /> : null}
        {turn.status === 'cancelled' && <span className="ml-2 text-sm text-faint">(stopped)</span>}
      </div>

      {turn.error && (
        <m.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.32, ease: EASE }}
          className="max-w-[94%] border-l-2 border-[var(--fm-amber)]/70 pl-3"
        >
          <p className="text-lg leading-relaxed text-ink sm:text-xl">{turnApology(turn.errorCode)}</p>
          <p className="mt-0.5 text-[11px] text-faint">
            <span className="font-mono">{turn.errorCode ?? 'error'}</span> · {turn.error}
          </p>
        </m.div>
      )}

      {turn.end?.model && (
        <p className="text-[11px] text-faint">
          {turn.end.model}
          {turn.end.failovers.some((f) => f.fell_through) && ' · after a failover'} · {(turn.end.ms / 1000).toFixed(1)} s
        </p>
      )}
    </m.li>
  )
}

/** The nearest ancestor that actually scrolls — it moves with the layout. */
function scrollerOf(node: HTMLElement | null): HTMLElement | null {
  for (let el = node?.parentElement ?? null; el && el !== document.body; el = el.parentElement) {
    const { overflowY } = getComputedStyle(el)
    if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight) return el
  }
  return null
}

export function Turns({ turns }: { turns: Turn[] }) {
  const bottom = useRef<HTMLLIElement>(null)
  const last = turns.at(-1)

  // The pane scrolls, not the page: `scrollIntoView` would drag the whole
  // window along on a laptop, where the device is only part of the page.
  useEffect(() => {
    const pane = scrollerOf(bottom.current)
    pane?.scrollTo({ top: pane.scrollHeight, behavior: scrollBehavior() })
  }, [turns.length, last?.reply.length, last?.tools.length])

  return (
    <ol className="space-y-6" aria-label="Conversation">
      <AnimatePresence initial={false}>
        {turns.map((turn, i) => (
          <TurnView key={turn.id} turn={turn} latest={i === turns.length - 1} />
        ))}
      </AnimatePresence>
      <li ref={bottom} aria-hidden="true" />
    </ol>
  )
}
