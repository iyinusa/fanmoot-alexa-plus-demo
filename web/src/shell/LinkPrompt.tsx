/**
 * The card Alexa shows when a tool needs the fan's own account — the server
 * answered a `tools/call` with a 401 (no grant) or a 403 (a grant without
 * the scope). "Sign in to Fanmoot" opens the real `mcp.fanmoot.com/authorize`
 * in a popup (W1-7); nothing about the flow is simulated.
 */
import { motion } from 'framer-motion'

import type { AuthRequired } from '@/lib/socket'
import type { AuthState } from '@/lib/protocol'
import { CloseIcon } from '@/shell/Icons'

export function LinkPrompt({
  required,
  auth,
  signIn,
  useDemo,
  dismiss,
}: {
  required: AuthRequired
  auth: AuthState | null
  signIn: () => void
  useDemo: () => void
  dismiss: () => void
}) {
  const scoped = required.status === 403 && auth?.mode === 'linked'
  return (
    <motion.div
      role="dialog"
      aria-modal="false"
      aria-labelledby="link-title"
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
            {scoped ? 'Fanmoot needs one more permission' : 'Link your Fanmoot account'}
          </h2>
          <p className="mt-0.5 font-mono text-[11px] text-faint">
            {required.tool} · HTTP {required.status}
            {required.scope ? ` · ${required.scope}` : ''}
          </p>
        </div>
      </div>
      <p className="mt-3 text-sm text-muted">
        {scoped
          ? `This sign-in did not grant ${required.scope ?? 'that permission'}. Sign in again and allow it.`
          : 'Posting goes out under your own handle. You sign in on Fanmoot’s own page — Alexa never sees your password.'}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" onClick={signIn} className="fm-tap rounded-fm-sm bg-[var(--fm-blue)] px-4 font-bold text-white hover:bg-[var(--fm-blue-deep)]">
          Sign in to Fanmoot
        </button>
        {required.demo_available && !scoped && (
          <button type="button" onClick={useDemo} className="fm-tap rounded-fm-sm border border-line px-4 text-ink hover:bg-surface">
            Use the demo account
          </button>
        )}
      </div>
    </motion.div>
  )
}
