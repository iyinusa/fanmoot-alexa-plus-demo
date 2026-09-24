import { useEffect, useState } from 'react'

/** The time, re-read on the minute — a clock on a screen, not a stopwatch. */
export function useClock(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    let timer = 0
    const tick = () => {
      const current = new Date()
      setNow(current)
      timer = window.setTimeout(tick, 60_000 - (current.getSeconds() * 1000 + current.getMilliseconds()) + 50)
    }
    tick()
    return () => window.clearTimeout(timer)
  }, [])
  return now
}
