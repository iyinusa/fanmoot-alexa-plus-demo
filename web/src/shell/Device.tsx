/**
 * The device (W2-1): an Echo Show-*inspired* smart display, drawn from
 * nothing but CSS — a charcoal bezel with a camera, three buttons along the
 * top edge, and a fabric speaker base. Original artwork: no Amazon marks, no
 * copied assets, and the light along the bottom of the screen is Fanmoot's
 * blue-to-green rather than anyone else's colour (HAD-11).
 *
 * The buttons work. Volume steps Alexa's voice, and the mic button switches
 * the microphone off the way the hardware one does — the light turns red and
 * nothing is heard until it is switched back on.
 *
 * On a phone the chrome falls away (W2-8): the phone *is* the device, and a
 * drawing of a screen inside a screen wastes the one thing a phone is short of.
 */
import type { ReactNode } from 'react'

import type { VoiceMode } from '@/shell/Waveform'
import { MicOffIcon, MinusIcon, PlusIcon } from '@/shell/Icons'

export function Device({
  children,
  mode,
  volume,
  nudgeVolume,
  micMuted,
  setMicMuted,
}: {
  children: ReactNode
  mode: VoiceMode
  volume: number
  nudgeVolume: (step: number) => void
  micMuted: boolean
  setMicMuted: (muted: boolean) => void
}) {
  return (
    <div className="fm-device">
      <div className="fm-device-buttons" role="group" aria-label="Device buttons">
        <button type="button" onClick={() => nudgeVolume(-0.1)} aria-label={`Volume down (now ${Math.round(volume * 10)} of 10)`}>
          <MinusIcon size={12} />
        </button>
        <button type="button" onClick={() => nudgeVolume(0.1)} aria-label={`Volume up (now ${Math.round(volume * 10)} of 10)`}>
          <PlusIcon size={12} />
        </button>
        <button
          type="button"
          onClick={() => setMicMuted(!micMuted)}
          aria-pressed={micMuted}
          aria-label={micMuted ? 'Switch the microphone on' : 'Switch the microphone off'}
          className={micMuted ? 'is-on' : ''}
        >
          <MicOffIcon size={12} />
        </button>
      </div>

      <div className="fm-bezel">
        <span className="fm-camera" aria-hidden="true" />
        <div className="fm-screen @container">
          {children}
          <div className={`fm-lightbar is-${micMuted ? 'muted' : mode}`} aria-hidden="true" />
        </div>
      </div>

      <div className="fm-base" aria-hidden="true" />
    </div>
  )
}
