/**
 * The ambient matchday state (W2-7): live scores in the corner of the screen
 * before anyone has said a word, from real `live_scores` calls the visitor's
 * session makes on its own schedule (app/session.py — on the trace as
 * `ambient`). With nothing in play it says what is next instead, from
 * `upcoming_matches`, because most visitors will not arrive mid-match.
 *
 * Two sizes: one match at a time in the status bar, rotating; and the board
 * on the home screen.
 */
import { AnimatePresence, m } from 'framer-motion'
import { useEffect, useState } from 'react'

import type { Ambient, TickerMatch, TickerSide } from '@/lib/protocol'

const EASE = [0.22, 1, 0.36, 1] as const

/** Crests are absolute already; a relative one is on fanmoot.com (HAD-11). */
function crestOf(side: TickerSide): string | null {
  const url = side.crest_url
  if (!url) return null
  return url.startsWith('/') ? `https://fanmoot.com${url}` : url
}

function Crest({ side, size }: { side: TickerSide; size: number }) {
  const src = crestOf(side)
  if (!src) {
    return (
      <span
        aria-hidden="true"
        className="inline-grid shrink-0 place-items-center rounded-full bg-raised text-[10px] font-bold text-muted"
        style={{ width: size, height: size }}
      >
        {side.short_name.slice(0, 1)}
      </span>
    )
  }
  return <img src={src} alt={`${side.name} badge`} width={size} height={size} className="shrink-0 object-contain" loading="lazy" />
}

/** "67'", "45+2'", "HT" — the clock as a scoreboard writes it. */
function clockOf(match: TickerMatch): string {
  const period = (match.period ?? '').toLowerCase()
  if (period === 'ht' || period === 'half_time' || period === 'halftime') return 'HT'
  if (match.minute == null) return 'Live'
  return match.added ? `${match.minute}+${match.added}'` : `${match.minute}'`
}

/** "Sat 17:30", in the visitor's own time zone. */
function kickoffOf(match: TickerMatch): string {
  if (!match.kickoff_at) return match.kickoff_spoken ?? ''
  const at = new Date(match.kickoff_at)
  if (Number.isNaN(at.getTime())) return match.kickoff_spoken ?? ''
  const today = new Date()
  const sameDay = at.toDateString() === today.toDateString()
  const time = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return sameDay ? `Today ${time}` : `${at.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`
}

function Score({ match, live }: { match: TickerMatch; live: boolean }) {
  if (!live) return <span className="text-faint">v</span>
  if (match.score_confirmed === false) {
    // The server's rule, kept on screen too: a score nobody confirmed is not shown.
    return (
      <span className="text-faint" title="Score not confirmed yet">
        – <span className="sr-only">score not confirmed</span>
      </span>
    )
  }
  const text = `${match.home?.score ?? 0}–${match.away?.score ?? 0}`
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <m.span
        key={text}
        initial={{ scale: 1.5, color: 'var(--fm-green-bright)' }}
        animate={{ scale: 1, color: 'var(--fm-text)' }}
        transition={{ duration: 0.6, ease: EASE }}
        className="fm-score inline-block"
      >
        {text}
      </m.span>
    </AnimatePresence>
  )
}

function Line({
  match,
  live,
  size = 18,
  compact = false,
}: {
  match: TickerMatch
  live: boolean
  size?: number
  /** In the status bar: names only where the screen has room for them. */
  compact?: boolean
}) {
  if (!match.home || !match.away) return null
  const name = compact ? 'hidden truncate @2xl:inline' : 'truncate'
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <Crest side={match.home} size={size} />
      <span className={name}>{match.home.short_name}</span>
      <Score match={match} live={live} />
      <span className={name}>{match.away.short_name}</span>
      <Crest side={match.away} size={size} />
    </span>
  )
}

/** One match at a time, for the status bar. */
export function TickerCompact({ ambient }: { ambient: Ambient | null }) {
  const matches = ambient?.matches.filter((m) => m.home && m.away) ?? []
  const [index, setIndex] = useState(0)

  useEffect(() => {
    if (matches.length < 2) return
    const timer = window.setInterval(() => setIndex((i) => i + 1), 6000)
    return () => window.clearInterval(timer)
  }, [matches.length])

  if (!ambient || matches.length === 0) return null
  const match = matches[index % matches.length]!
  const live = ambient.live

  return (
    <div className="flex min-w-0 items-center gap-2 text-xs" aria-live="off">
      <span
        className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
          live ? 'bg-[var(--fm-red)]/90 text-white' : 'bg-raised text-muted'
        }`}
      >
        {live && <span className="size-1.5 animate-pulse rounded-full bg-white" aria-hidden="true" />}
        {live ? 'Live' : 'Next'}
      </span>
      <AnimatePresence mode="wait" initial={false}>
        <m.span
          key={match.id}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.3, ease: EASE }}
          className="flex min-w-0 items-center gap-2"
        >
          <Line match={match} live={live} size={16} compact />
          <span className="shrink-0 text-muted">{live ? clockOf(match) : kickoffOf(match)}</span>
        </m.span>
      </AnimatePresence>
    </div>
  )
}

/** The home screen's board: all of them, a little larger. */
export function TickerBoard({ ambient }: { ambient: Ambient | null }) {
  const matches = ambient?.matches.filter((m) => m.home && m.away) ?? []
  if (!ambient || matches.length === 0) return null
  const live = ambient.live
  return (
    <section aria-label={live ? 'Live now' : 'Coming up'} className="w-full max-w-md">
      <h2 className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-muted">
        {live ? 'Live now on Fanmoot' : 'Coming up'}
      </h2>
      <ul className="divide-y divide-line overflow-hidden rounded-fm-lg border border-line bg-surface/70 backdrop-blur">
        {matches.map((match, i) => (
          <m.li
            key={match.id}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.35, delay: i * 0.06, ease: EASE }}
            className="flex items-center gap-3 px-3 py-2.5 text-sm"
          >
            <span className="min-w-0 flex-1">
              <Line match={match} live={live} size={22} />
            </span>
            <span className={`shrink-0 text-xs ${live ? 'font-bold text-[var(--fm-green-bright)]' : 'text-muted'}`}>
              {live ? clockOf(match) : kickoffOf(match)}
            </span>
          </m.li>
        ))}
      </ul>
    </section>
  )
}
