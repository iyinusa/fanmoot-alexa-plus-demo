/**
 * Two routes. `/linked` is where the sign-in popup lands (W1-7); everything
 * else is the demo: the Alexa+ shell (W2), with the live session beside it
 * until the trace inspector (W3) takes that place, and the landing state
 * (W5-1) still to be designed properly for a judge's first ten seconds.
 */
import { FieldBackdrop } from '@/components/FieldBackdrop'
import { Linked } from '@/components/Linked'
import { AlexaShell } from '@/shell/AlexaShell'

export function App() {
  if (window.location.pathname === '/linked') return <Linked />

  return (
    <main className="fm-motion relative min-h-dvh px-4 pb-8 pt-4 sm:pt-6">
      <FieldBackdrop />
      <header className="mx-auto mb-4 flex w-full max-w-[1480px] items-center gap-3 sm:mb-6">
        <img src="/icons/icon-192.png" alt="" width={36} height={36} className="rounded-fm-sm" />
        <div className="min-w-0">
          <h1 className="text-lg leading-tight">
            Fanmoot <span className="font-light text-muted">on Alexa+</span>
          </h1>
          <p className="truncate text-xs text-muted">Where fans debate football — now by voice.</p>
        </div>
      </header>
      <AlexaShell />
    </main>
  )
}
