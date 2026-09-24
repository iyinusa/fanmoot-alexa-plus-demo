/**
 * The strip along the top of the screen: the time, the ticker (W2-7), and who
 * the fan is — with sign-in, the demo account and sign-out one tap away.
 *
 * The account control is a disclosure, not an ARIA menu (W5-5): a `menu`
 * role promises arrow-key navigation, and two buttons are better served by
 * Tab. Opening it puts focus on the first choice; Escape closes it and
 * returns focus to the button.
 */
import { AnimatePresence, m } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'

import type { Ambient, AuthState } from '@/lib/protocol'
import type { SocketStatus } from '@/lib/socket'
import { TickerCompact } from '@/shell/Ticker'
import { useClock } from '@/shell/useClock'

const DOT: Record<SocketStatus, string> = {
  connecting: 'bg-[var(--fm-amber)]',
  open: 'bg-[var(--fm-green-bright)]',
  closed: 'bg-[var(--fm-red)]',
}

const STATUS: Record<SocketStatus, string> = {
  connecting: 'Connecting',
  open: 'Connected to Fanmoot',
  closed: 'Reconnecting',
}

export function StatusBar({
  status,
  auth,
  ambient,
  demoAvailable,
  signIn,
  useDemo,
  signOut,
  showTicker,
}: {
  status: SocketStatus
  auth: AuthState | null
  ambient: Ambient | null
  demoAvailable: boolean
  signIn: () => void
  useDemo: () => void
  signOut: () => void
  showTicker: boolean
}) {
  const now = useClock()
  const [open, setOpen] = useState(false)
  const menu = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLElement>('[data-choice]')?.focus()
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key !== 'Escape') return
        setOpen(false)
        trigger.current?.focus()
      } else if (!menu.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  const who =
    !auth || auth.mode === 'none' ? 'Guest' : auth.mode === 'demo' ? `@${auth.handle ?? 'demo'}` : `@${auth.handle ?? '…'}`

  return (
    <div className="relative z-20 flex h-11 shrink-0 items-center gap-3 px-4 text-sm @3xl:h-12 @3xl:px-5">
      <time className="fm-score shrink-0 text-base text-ink" dateTime={now.toISOString()}>
        {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
      </time>

      <div className="min-w-0 flex-1">{showTicker && <TickerCompact ambient={ambient} />}</div>

      <span className="flex shrink-0 items-center" title={STATUS[status]}>
        <span className={`size-2 rounded-full ${DOT[status]}`} aria-hidden="true" />
        <span className="sr-only" aria-live="polite">
          {STATUS[status]}
        </span>
      </span>

      <div ref={menu} className="relative shrink-0">
        <button
          ref={trigger}
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="account-panel"
          className="flex items-center gap-1.5 rounded-full border border-line bg-surface/70 py-1 pl-1 pr-2.5 text-xs text-ink hover:bg-raised"
        >
          <span
            aria-hidden="true"
            className={`grid size-5 place-items-center rounded-full text-[10px] font-bold text-white ${
              auth?.mode === 'linked' ? 'bg-[var(--fm-green)]' : auth?.mode === 'demo' ? 'bg-[var(--fm-amber)]' : 'bg-[var(--fm-blue)]'
            }`}
          >
            {who.replace('@', '').charAt(0).toUpperCase()}
          </span>
          <span className="max-w-[9rem] truncate">{who}</span>
          <span className="sr-only">, account</span>
        </button>

        <AnimatePresence>
          {open && (
            <m.div
              id="account-panel"
              initial={{ opacity: 0, y: -4, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16 }}
              className="absolute right-0 top-full mt-2 w-64 rounded-fm border border-line bg-raised p-2 text-sm shadow-[var(--fm-shadow)]"
            >
              {!auth || auth.mode === 'none' ? (
                <>
                  <p className="px-2 pb-2 pt-1 text-xs text-muted">Reading works as a guest. Posting needs a Fanmoot account.</p>
                  <MenuItem onClick={() => {
                      setOpen(false)
                      signIn()
                    }}>Sign in to Fanmoot</MenuItem>
                  {demoAvailable && <MenuItem onClick={() => {
                      setOpen(false)
                      useDemo()
                    }}>Use the demo account</MenuItem>}
                </>
              ) : (
                <>
                  <p className="px-2 pb-2 pt-1 text-xs text-muted">
                    {auth.mode === 'demo' ? (
                      <>
                        <span className="font-bold text-[var(--fm-amber)]">{auth.label}</span> — shared, and rate-limited.
                      </>
                    ) : (
                      <>Linked with {auth.method ?? 'OAuth'}.</>
                    )}
                  </p>
                  <MenuItem onClick={() => {
                      setOpen(false)
                      signOut()
                    }}>
                    {auth.mode === 'demo' ? 'Stop using the demo account' : 'Sign out and disconnect'}
                  </MenuItem>
                </>
              )}
            </m.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

function MenuItem({ children, onClick }: { children: string; onClick: () => void }) {
  return (
    <button type="button" data-choice onClick={onClick} className="block w-full rounded-fm-sm px-2 py-2 text-left hover:bg-surface">
      {children}
    </button>
  )
}
