/**
 * The device's own screen for the errors that stop the demo (W5-4): offline,
 * disconnected, full, updated, timed out. It covers the device's screen the
 * way a smart display shows "I'm having trouble connecting", with the light
 * along the bottom pulsing amber, and it says what happens next rather than
 * what broke.
 *
 * A dropped socket is not shown at once. A phone that blinks between cells
 * reconnects in half a second, and a screen that flashes up for that is
 * worse than none. So "disconnected" waits `GRACE_MS` first, and the status
 * dot in the corner is the only sign of a blip.
 */
import { AnimatePresence, m } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'

import { FAULT_COPY, type FaultKind } from '@/lib/faults'
import type { SocketStatus } from '@/lib/socket'
import { AlertIcon, OfflineIcon, ResetIcon } from '@/shell/Icons'

const GRACE_MS = 2500
const EASE = [0.22, 1, 0.36, 1] as const

export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

/** Which fault, if any, the screen should show right now. */
export function useFault({
  status,
  fault,
  online,
}: {
  status: SocketStatus
  fault: FaultKind | null
  online: boolean
}): FaultKind | null {
  // Timed from the moment the socket stopped being open. Keyed on "open or
  // not" rather than on the status itself, because every reconnect attempt
  // flips it between `connecting` and `closed`, and restarting the clock on
  // each flip would hold the screen back for as long as the attempts are
  // quick.
  const open = status === 'open'
  const [late, setLate] = useState(false)
  useEffect(() => {
    if (open) {
      setLate(false)
      return
    }
    const timer = window.setTimeout(() => setLate(true), GRACE_MS)
    return () => window.clearTimeout(timer)
  }, [open])

  if (fault === 'stale' || fault === 'expired') return fault
  if (!online) return 'offline'
  if (fault === 'capacity') return 'capacity'
  return late && !open ? 'disconnected' : null
}

function Countdown({ at }: { at: number | null }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (at === null) return
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [at])
  if (at === null) return <>Trying again now…</>
  const seconds = Math.ceil((at - now) / 1000)
  return <>{seconds > 0 ? `Trying again in ${seconds} s.` : 'Trying again now…'}</>
}

export function Fault({
  kind,
  retryAt,
  reconnect,
}: {
  kind: FaultKind | null
  retryAt: number | null
  reconnect: () => void
}) {
  const action = useRef<HTMLButtonElement>(null)
  // The action is where a keyboard wants to be, once the screen is up.
  useEffect(() => {
    if (kind) action.current?.focus({ preventScroll: true })
  }, [kind])

  return (
    <AnimatePresence>
      {kind && (
        <m.div
          key={kind}
          role="alert"
          aria-live="assertive"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          // Stops short of the bottom edge, so the amber light shows under it.
          className="absolute inset-x-0 bottom-[5px] top-0 z-40 grid place-items-center bg-[var(--fm-bg)]/94 px-6 text-center backdrop-blur-md"
        >
          <m.div
            initial={{ y: 12, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.45, ease: EASE, delay: 0.05 }}
            className="max-w-md"
          >
            <span
              aria-hidden="true"
              className="mx-auto grid size-16 place-items-center rounded-full bg-[var(--fm-amber)]/12 text-[var(--fm-amber)] ring-1 ring-[var(--fm-amber)]/30"
            >
              {kind === 'offline' || kind === 'disconnected' ? <OfflineIcon size={28} /> : <AlertIcon size={28} />}
            </span>
            <h2 className="mt-5 text-2xl font-light leading-snug @3xl:text-3xl">{FAULT_COPY[kind].title}</h2>
            <p className="mt-2 text-muted">{FAULT_COPY[kind].body}</p>
            {(kind === 'disconnected' || kind === 'capacity') && (
              <p className="mt-2 text-sm text-muted" aria-live="off">
                <Countdown at={retryAt} />
              </p>
            )}
            {FAULT_COPY[kind].action && (
              <button
                ref={action}
                type="button"
                onClick={() => (FAULT_COPY[kind].action === 'reload' ? window.location.reload() : reconnect())}
                className="fm-tap mt-6 inline-flex items-center gap-2 rounded-full bg-[var(--fm-blue)] px-5 font-bold text-white hover:bg-[var(--fm-blue-deep)]"
              >
                <ResetIcon size={16} />
                {FAULT_COPY[kind].action === 'reload' ? 'Reload' : 'Try now'}
              </button>
            )}
          </m.div>
        </m.div>
      )}
    </AnimatePresence>
  )
}
