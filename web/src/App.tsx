/**
 * Two routes. `/linked` is where the sign-in popup lands (W1-7); everything
 * else is the demo: the Alexa+ shell (W2) with the MCP trace inspector (W3)
 * beside it. A first visit opens on the device's welcome screen (W5-1,
 * `shell/Intro.tsx`).
 */
import { FieldBackdrop } from '@/components/FieldBackdrop'
import { Linked } from '@/components/Linked'
import { AlexaShell } from '@/shell/AlexaShell'

export function App() {
  if (window.location.pathname === '/linked') return <Linked />

  return (
    <main className="fm-motion relative min-h-dvh px-4 pb-8 pt-4 sm:pt-6">
      <FieldBackdrop />
      <AlexaShell />
    </main>
  )
}
