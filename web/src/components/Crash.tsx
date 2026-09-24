/**
 * The last line of defence (W5-4): if anything throws while rendering, the
 * page shows a device that needs a restart, in the same words Alexa would
 * use, rather than a white page or a stack trace.
 *
 * Deliberately plain: no Framer Motion, no hooks into the session. Whatever
 * broke may be one of those. The error goes to the console for whoever is
 * debugging, and never onto the screen.
 *
 * A chunk that fails to load after a deploy (the old hashes are gone) lands
 * here too, and "Restart" is exactly the fix for it.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'

type State = { failed: boolean }

export class Crash extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('The demo stopped rendering:', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main className="flex min-h-dvh items-center justify-center px-4">
        <section
          role="alert"
          className="w-full max-w-md rounded-fm-lg border border-line bg-surface p-7 text-center shadow-[var(--fm-shadow)]"
        >
          <img src="/icons/icon-192.png" alt="" width={48} height={48} className="mx-auto rounded-fm-sm" />
          <h1 className="mt-5 text-2xl">Sorry, I need a quick restart.</h1>
          <p className="mt-3 text-muted">
            Something on this screen stopped working. Your Fanmoot sign-in is safe, and restarting picks up where you
            were if the conversation is still open.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="fm-tap mt-6 rounded-full bg-[var(--fm-blue)] px-6 font-bold text-white hover:bg-[var(--fm-blue-deep)]"
          >
            Restart the demo
          </button>
        </section>
      </main>
    )
  }
}
