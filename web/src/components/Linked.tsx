/**
 * `/linked` — where the sign-in popup lands (W1-7).
 *
 * The OAuth flow has already finished on the server by the time this renders:
 * `/oauth/callback` redeemed the code and told the visitor's socket. So this
 * page only has to say how it went, tell the window that opened it, and close.
 * If it was opened some other way — a bookmarked URL, a blocked opener — it
 * stays open with a link back.
 */
import { useEffect } from 'react'

const COPY: Record<string, { title: string; body: string }> = {
  linked: { title: 'Signed in', body: 'You can close this window and carry on talking.' },
  denied: { title: 'Not signed in', body: 'Nothing was shared. You can close this window.' },
  error: { title: "That didn't work", body: 'Close this window and try signing in again.' },
}

export function Linked() {
  const params = new URLSearchParams(window.location.search)
  const status = params.get('status') ?? 'error'
  const reason = params.get('reason')
  const copy = COPY[status] ?? COPY.error!

  useEffect(() => {
    if (!window.opener) return
    window.opener.postMessage({ type: 'fanmoot:linked', status }, window.location.origin)
    const timer = window.setTimeout(() => window.close(), status === 'linked' ? 900 : 2500)
    return () => window.clearTimeout(timer)
  }, [status])

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <section className="w-full max-w-sm rounded-fm-lg border border-line bg-surface p-6 text-center shadow-[var(--fm-shadow)]">
        <img src="/icons/icon-192.png" alt="" width={48} height={48} className="mx-auto rounded-fm-sm" />
        <h1 className="mt-4 text-xl">{copy.title}</h1>
        {reason && <p className="mt-2 text-sm">{status === 'linked' ? `as ${reason}` : reason}</p>}
        <p className="mt-2 text-sm text-muted">{copy.body}</p>
        {!window.opener && (
          <a href="/" className="mt-4 inline-block text-sm text-accent hover:underline">
            Back to the demo
          </a>
        )}
      </section>
    </main>
  )
}
