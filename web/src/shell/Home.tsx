/**
 * The screen before anyone has spoken: a big clock, a line to say, and the
 * ticker's board — so the device looks switched on and busy with football
 * rather than waiting for instructions (W2-7).
 */
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'

import type { Ambient } from '@/lib/protocol'
import { SUGGESTIONS } from '@/conversation/Composer'
import { TickerBoard } from '@/shell/Ticker'
import { useClock } from '@/shell/useClock'

const EASE = [0.22, 1, 0.36, 1] as const

function greeting(hour: number): string {
  if (hour < 5) return 'Late one'
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function Home({ ambient, canListen }: { ambient: Ambient | null; canListen: boolean }) {
  const now = useClock()
  const [hint, setHint] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => setHint((i) => (i + 1) % SUGGESTIONS.length), 5200)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="flex h-full flex-col items-start justify-center gap-6 overflow-y-auto px-6 pb-10 pt-2 @3xl:flex-row @3xl:items-center @3xl:justify-between @3xl:gap-10 @3xl:px-12">
      <div className="min-w-0">
        <p className="text-sm text-muted">
          {greeting(now.getHours())} · {now.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
        </p>
        <p className="fm-score mt-1 text-6xl leading-none text-ink @3xl:text-8xl">
          {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
        </p>
        <div className="mt-6 max-w-lg">
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-muted">{canListen ? 'Tap the mic and say' : 'Try typing'}</p>
          <div className="relative mt-1 min-h-[4.5rem] @3xl:min-h-[5.5rem]">
            <AnimatePresence mode="wait">
              <motion.p
                key={hint}
                initial={{ opacity: 0, y: 12, filter: 'blur(6px)' }}
                animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                exit={{ opacity: 0, y: -10, filter: 'blur(6px)' }}
                transition={{ duration: 0.5, ease: EASE }}
                className="text-2xl font-light leading-snug text-ink @3xl:text-3xl"
              >
                “Alexa, {SUGGESTIONS[hint]!.charAt(0).toLowerCase() + SUGGESTIONS[hint]!.slice(1)}”
              </motion.p>
            </AnimatePresence>
          </div>
        </div>
      </div>
      <TickerBoard ambient={ambient} />
    </div>
  )
}
