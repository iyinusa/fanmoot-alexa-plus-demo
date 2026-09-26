/**
 * The landing state (W5-1): what a judge who arrives with no context sees
 * first, and has to understand in ten seconds.
 *
 * It is the device's own welcome screen, the way a smart display greets you
 * the first time it is switched on, so it explains the demo from inside the
 * demo rather than from a banner above it. It answers three questions in
 * order:
 *
 * 1. **What is this?** The headline: talk football with Alexa, hear the fans.
 * 2. **Is it real?** Yes, all of it except the device. The agent, the MCP
 *    server and the posts are real, and the connection line under the buttons
 *    is read from the live session (`facts`), not written here.
 * 3. **Where do I look?** At the trace. On a wide screen the trace column is
 *    lit up while this is showing; on a narrow one the beat says where the
 *    button is.
 *
 * "Take the tour" starts the first guided scenario (W4) as a real turn, so the
 * first thing a judge does is the thing the demo is for. Seen once per
 * browser; the ⓘ button in the page header brings it back.
 *
 * The headline is not animated in. It is the page's largest paint (W5-3), and
 * everything else arrives around it.
 */
import { m } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { Facts, Scenario } from '@/lib/protocol'
import { ArrowIcon, CloseIcon, PlayIcon, PlugIcon, TraceIcon, WaveIcon } from '@/shell/Icons'

const STORE_KEY = 'fm-intro'
const EASE = [0.22, 1, 0.36, 1] as const

function readSeen(): boolean {
  try {
    return window.localStorage.getItem(STORE_KEY) === 'seen'
  } catch {
    return false
  }
}

/** Shown until dismissed once in this browser, and on demand after that. */
export function useIntro(hasTurns: boolean) {
  const [open, setOpen] = useState(() => !hasTurns && !readSeen())
  /** Opened again from the header, rather than on arrival. */
  const [recalled, setRecalled] = useState(false)
  const close = useCallback(() => {
    setOpen(false)
    try {
      window.localStorage.setItem(STORE_KEY, 'seen')
    } catch {
      // Private mode: it shows again next visit, which is harmless.
    }
  }, [])
  const show = useCallback(() => {
    setRecalled(true)
    setOpen(true)
  }, [])
  return { open, recalled, close, show }
}

const rise = (delay: number) => ({
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.55, ease: EASE, delay },
})

export function Intro({
  facts,
  connected,
  wide,
  scenarios,
  canListen,
  recalled,
  onTour,
  onClose,
}: {
  facts: Facts | null
  connected: boolean
  /** The trace is a column to the right, rather than a button in the header. */
  wide: boolean
  scenarios: Scenario[]
  canListen: boolean
  recalled: boolean
  onTour: (scenario: Scenario) => void
  onClose: () => void
}) {
  const first = scenarios[0]
  const tour = useRef<HTMLButtonElement>(null)

  // Recalled from the header: the keyboard goes with it. On arrival the page
  // is left alone, so nothing is focused before the visitor has done anything.
  useEffect(() => {
    if (recalled) tour.current?.focus({ preventScroll: true })
  }, [recalled])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const mcp = facts?.mcp
  const beats = [
    {
      icon: <WaveIcon size={20} />,
      title: canListen ? 'Speak or type' : 'Type, and hear it answer',
      body: canListen
        ? 'Tap the mic and ask, or type if you’re somewhere quiet. Alexa answers out loud, with captions.'
        : 'This browser can’t listen, so type your question. Alexa answers out loud, with captions.',
    },
    {
      icon: <PlugIcon size={20} />,
      title: 'Nothing is scripted',
      body: 'A live AI agent calls Fanmoot’s MCP server for real fixtures, fans’ takes, and posts under your own handle.',
    },
    {
      icon: <TraceIcon size={20} />,
      title: 'See every call',
      body: wide
        ? 'Open the MCP trace on the right to watch each MCP request and reply as it happens.'
        : 'Tap “MCP trace” at the top to see each MCP request and reply as it happens.',
    },
  ]

  return (
    <m.section
      aria-labelledby="intro-title"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.985, transition: { duration: 0.3, ease: EASE } }}
      transition={{ duration: 0.2 }}
      className="fm-screen-scroll absolute inset-0 z-20 overflow-y-auto bg-[var(--fm-bg)]/90 backdrop-blur-md"
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close the introduction"
        className="fm-tap absolute right-2 top-2 z-10 grid place-items-center rounded-full text-faint hover:text-ink @3xl:right-4 @3xl:top-3"
      >
        <CloseIcon size={18} />
      </button>

      <div className="mx-auto grid min-h-full max-w-6xl content-center gap-8 px-6 py-10 @3xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] @3xl:items-center @3xl:gap-12 @3xl:px-12">
        <div className="min-w-0">
          <m.p {...rise(0.05)} className="inline-flex items-center gap-2.5 text-xs font-bold uppercase tracking-[0.16em] text-accent">
            <span className="fm-bars" aria-hidden="true">
              <span />
              <span />
              <span />
              <span />
            </span>
            Fanmoot on Alexa+ · a working demo
          </m.p>

          <h2 id="intro-title" className="mt-4 text-[2rem] font-light leading-[1.12] tracking-[-0.02em] text-ink @3xl:text-5xl">
            Talk football with Alexa.{' '}
            <span className="font-bold">Hear what the fans are saying.</span>
          </h2>

          <m.p {...rise(0.12)} className="mt-4 max-w-xl text-base leading-relaxed text-muted @3xl:text-lg">
            Ask about a match, get the score and the mood of the crowd, then post your own take to Fanmoot. All by voice.
          </m.p>

          <m.div {...rise(0.2)} className="mt-7 flex flex-wrap items-center gap-3">
            <button
              ref={tour}
              type="button"
              disabled={!first}
              onClick={() => first && onTour(first)}
              className="fm-tap group inline-flex items-center gap-2.5 rounded-full bg-[var(--fm-blue)] py-2 pl-2.5 pr-5 font-bold text-white shadow-lg shadow-[var(--fm-blue)]/25 transition-colors hover:bg-[var(--fm-blue-deep)] disabled:opacity-60"
            >
              <span className="grid size-8 place-items-center rounded-full bg-white/15 transition-transform group-hover:scale-110">
                <PlayIcon size={14} />
              </span>
              {first ? 'Take the tour' : 'Reading today’s matches…'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="fm-tap inline-flex items-center gap-1.5 rounded-full px-4 text-muted transition-colors hover:text-ink"
            >
              I’ll look around <ArrowIcon size={16} />
            </button>
          </m.div>
          {first?.steps[0] && (
            <m.p {...rise(0.26)} className="mt-3 text-sm text-muted">
              It starts with “{first.steps[0]}” and takes about a minute.
            </m.p>
          )}

          <m.p {...rise(0.32)} className="mt-6 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[11px] text-muted">
            <span className={`size-1.5 rounded-full ${connected && mcp ? 'bg-live' : 'bg-[var(--fm-amber)]'}`} aria-hidden="true" />
            {connected && mcp ? (
              <>
                <span>Live: {new URL(mcp.url).host}</span>
                <span className="text-faint">·</span>
                <span>MCP {mcp.protocol_version}</span>
                <span className="text-faint">·</span>
                <span>{mcp.transport}</span>
              </>
            ) : (
              <span>Connecting to mcp.fanmoot.com…</span>
            )}
          </m.p>
        </div>

        <ol className="grid gap-3" aria-label="How it works">
          {beats.map((beat, i) => (
            <m.li
              key={beat.title}
              {...rise(0.18 + i * 0.09)}
              className="flex gap-3.5 rounded-fm-lg border border-line bg-surface/75 p-4 shadow-[var(--fm-shadow)]"
            >
              <span
                aria-hidden="true"
                className="grid size-10 shrink-0 place-items-center rounded-full bg-[var(--fm-blue)]/14 text-accent ring-1 ring-[var(--fm-blue)]/25"
              >
                {beat.icon}
              </span>
              <span className="min-w-0">
                <span className="block font-bold text-ink">
                  <span className="sr-only">{i + 1}. </span>
                  {beat.title}
                </span>
                <span className="mt-0.5 block text-sm leading-relaxed text-muted">{beat.body}</span>
              </span>
            </m.li>
          ))}
          <m.li {...rise(0.48)} className="px-1 text-xs leading-relaxed text-muted">
            Only the Alexa+ device is simulated: Amazon’s add-on toolkit isn’t open yet. Not made or
            endorsed by Amazon.
          </m.li>
        </ol>
      </div>
    </m.section>
  )
}
