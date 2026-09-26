/**
 * `/linked` — where the sign-in popup lands (W1-7).
 *
 * The OAuth flow has already finished on the server by the time this renders:
 * `/oauth/callback` redeemed the code and told the visitor's socket. So this
 * page only has to say how it went, tell the conversation, and close.
 *
 * It cannot rely on `window.opener` for that. Fanmoot's sign-in and consent
 * pages send `Cross-Origin-Opener-Policy: same-origin`, and passing through
 * them severs the popup from the tab that opened it — `opener` is null by the
 * time the fan is sent back here. So the popup calls out on a same-origin
 * `BroadcastChannel` instead, and the conversation tab (`useSignInAnswer`)
 * answers. It closes itself either way; if the browser refuses, the page says
 * to close it — and only offers a link when no conversation answered, so a
 * fan who is signed in is never sent into a second copy of the demo.
 */
import { useEffect, useState } from 'react'

const CHANNEL = 'fanmoot-sign-in'

type Signal = { type: 'fanmoot:linked'; status: string } | { type: 'fanmoot:here' }

const COPY: Record<string, { title: string; body: string }> = {
  linked: { title: 'Signed in', body: 'Carry on talking in your conversation.' },
  denied: { title: 'Not signed in', body: 'Nothing was shared.' },
  error: { title: "That didn't work", body: 'Go back to your conversation and try signing in again.' },
}

function openChannel(): BroadcastChannel | null {
  return typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL)
}

/** The conversation tab's side: answer a sign-in popup and come to the front. */
export function useSignInAnswer() {
  useEffect(() => {
    const channel = openChannel()
    if (!channel) return
    channel.onmessage = (event: MessageEvent<Signal>) => {
      if (event.data?.type !== 'fanmoot:linked') return
      channel.postMessage({ type: 'fanmoot:here' } satisfies Signal)
      window.focus()
    }
    return () => channel.close()
  }, [])
}

export function Linked() {
  const params = new URLSearchParams(window.location.search)
  const status = params.get('status') ?? 'error'
  const reason = params.get('reason')
  const copy = COPY[status] ?? COPY.error!
  /** A conversation tab answered on the channel. */
  const [answered, setAnswered] = useState(false)
  /** We asked the browser to close this window and it is still here. */
  const [stayed, setStayed] = useState(false)

  useEffect(() => {
    const signal: Signal = { type: 'fanmoot:linked', status }
    window.opener?.postMessage(signal, window.location.origin)
    const channel = openChannel()
    if (channel) {
      channel.onmessage = (event: MessageEvent<Signal>) => {
        if (event.data?.type === 'fanmoot:here') setAnswered(true)
      }
      channel.postMessage(signal)
    }
    let check = 0
    const timer = window.setTimeout(
      () => {
        window.close()
        check = window.setTimeout(() => setStayed(true), 300)
      },
      status === 'linked' ? 900 : 2500,
    )
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(check)
      channel?.close()
    }
  }, [status])

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <section className="w-full max-w-sm rounded-fm-lg border border-line bg-surface p-6 text-center shadow-[var(--fm-shadow)]">
        <img src="/icons/icon-192.png" alt="" width={48} height={48} className="mx-auto rounded-fm-sm" />
        <h1 className="mt-4 text-xl">{copy.title}</h1>
        {reason && <p className="mt-2 text-sm">{status === 'linked' ? `as ${reason}` : reason}</p>}
        <p className="mt-2 text-sm text-muted">{copy.body}</p>
        {stayed &&
          (answered || window.opener ? (
            <p className="mt-4 text-sm">You can close this tab now.</p>
          ) : (
            <a href="/" className="mt-4 inline-block text-sm text-accent hover:underline">
              Start a conversation
            </a>
          ))}
      </section>
    </main>
  )
}
