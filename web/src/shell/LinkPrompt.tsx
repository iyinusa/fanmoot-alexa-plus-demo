/**
 * The card Alexa shows when a tool needs the fan's own account — the server
 * answered a `tools/call` with a 401 (no grant) or a 403 (a grant without
 * the scope). "Sign in to Fanmoot" opens the real `mcp.fanmoot.com/authorize`
 * in a popup (W1-7); nothing about the flow is simulated.
 *
 * Both ways in are always offered, because there is no shared account to
 * fall back on: a fan posts as themselves. The "New here" scenario (W4-5)
 * leads with creating one — the popup opens on Fanmoot's own sign-up form
 * (`prompt=create`) — and everywhere else signing in leads.
 *
 * Keyboard (W5-5): the card takes focus when it appears, on its main button,
 * because a keyboard or screen-reader user would otherwise not know it had.
 * Escape is "Not now", and focus goes back to the typed field when it closes.
 */
import { m } from 'framer-motion'
import { useEffect, useRef } from 'react'

import type { AuthRequired } from '@/lib/socket'
import type { AuthState } from '@/lib/protocol'
import { CloseIcon } from '@/shell/Icons'

export function LinkPrompt({
  required,
  auth,
  signIn,
  dismiss,
  signup = false,
}: {
  required: AuthRequired
  auth: AuthState | null
  signIn: (options?: { signup?: boolean }) => void
  dismiss: () => void
  signup?: boolean
}) {
  const scoped = required.status === 403 && auth?.mode === 'linked'
  const joining = signup && !scoped
  const card = useRef<HTMLDivElement>(null)
  const primary = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    primary.current?.focus({ preventScroll: true })
    const node = card.current
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && dismiss()
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (node?.contains(document.activeElement)) document.getElementById('ask')?.focus({ preventScroll: true })
    }
  }, [dismiss])

  return (
    <m.div
      ref={card}
      role="dialog"
      aria-modal="false"
      aria-labelledby="link-title"
      aria-describedby="link-body"
      initial={{ opacity: 0, y: 24, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 12, scale: 0.98 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="absolute inset-x-3 bottom-6 z-30 mx-auto max-w-md rounded-fm-lg border border-line bg-raised/95 p-5 shadow-[var(--fm-shadow)] backdrop-blur @3xl:bottom-10"
    >
      <button type="button" onClick={dismiss} aria-label="Not now" className="absolute right-3 top-3 text-faint hover:text-ink">
        <CloseIcon size={18} />
      </button>
      <div className="flex items-center gap-3">
        <img src="/icons/icon-192.png" alt="" width={40} height={40} className="rounded-fm-sm" />
        <div>
          <h2 id="link-title" className="text-base leading-tight">
            {scoped ? 'Fanmoot needs one more permission' : joining ? 'Join Fanmoot to post' : 'Link your Fanmoot account'}
          </h2>
          <p className="mt-0.5 font-mono text-[11px] text-faint">
            {required.tool} · HTTP {required.status}
            {required.scope ? ` · ${required.scope}` : ''}
          </p>
        </div>
      </div>
      <p id="link-body" className="mt-3 text-sm text-muted">
        {scoped
          ? `This sign-in did not grant ${required.scope ?? 'that permission'}. Sign in again and allow it.`
          : joining
            ? 'Create your account on Fanmoot’s own page, confirm your email, and your first take goes out under your new handle. Alexa never sees your password.'
            : 'Posts and predictions go out under your own handle. Sign in, or join free in a minute, on Fanmoot’s own page — Alexa never sees your password.'}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          ref={primary}
          type="button"
          onClick={() => signIn({ signup: joining })}
          className="fm-tap rounded-fm-sm bg-[var(--fm-blue)] px-4 font-bold text-white hover:bg-[var(--fm-blue-deep)]"
        >
          {joining ? 'Create a Fanmoot account' : 'Sign in to Fanmoot'}
        </button>
        {joining && (
          <button type="button" onClick={() => signIn()} className="fm-tap rounded-fm-sm border border-line px-4 text-ink hover:bg-surface">
            I already have one
          </button>
        )}
        {!scoped && !joining && (
          <button
            type="button"
            onClick={() => signIn({ signup: true })}
            className="fm-tap rounded-fm-sm border border-line px-4 text-ink hover:bg-surface"
          >
            Create an account
          </button>
        )}
      </div>
    </m.div>
  )
}
