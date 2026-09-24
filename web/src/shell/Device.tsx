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
 *
 * As the page opens, the light sweeps out from the middle of the screen once
 * and fades (W5-2): the device switching on.
 *
 * Pressing volume shows the level the way the hardware does: a pill at the
 * top of the screen and the light along the bottom filled to the level, for
 * a moment, with a click at the new volume (`lib/earcon.ts`). It is Alexa's
 * voice that changes; a web page cannot change the computer's own volume.
 */
import { AnimatePresence, m } from 'framer-motion'
import { type ReactNode, useEffect, useRef, useState } from 'react'

import type { VoiceMode } from '@/shell/Waveform'
import { MicOffIcon, MinusIcon, PlusIcon, SpeakerIcon } from '@/shell/Icons'

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
  const [booting, setBooting] = useState(true)
  useEffect(() => {
    const timer = window.setTimeout(() => setBooting(false), 1900)
    return () => window.clearTimeout(timer)
  }, [])

  // The volume on show for a moment after a press, not on first render.
  const [showVolume, setShowVolume] = useState(false)
  const firstVolume = useRef(true)
  useEffect(() => {
    if (firstVolume.current) {
      firstVolume.current = false
      return
    }
    setShowVolume(true)
    const timer = window.setTimeout(() => setShowVolume(false), 1600)
    return () => window.clearTimeout(timer)
  }, [volume])
  const level = Math.round(volume * 10)

  const light = micMuted ? 'muted' : showVolume ? 'volume' : booting && mode === 'idle' ? 'boot' : mode

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
          <AnimatePresence>
            {showVolume && (
              <m.div
                key="volume"
                role="status"
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.2 }}
                className="absolute inset-x-0 top-3 z-50 mx-auto flex w-fit items-center gap-3 rounded-full border border-line bg-raised/95 px-4 py-2 shadow-[var(--fm-shadow)] backdrop-blur"
              >
                <SpeakerIcon size={18} className="text-ink" />
                <span className="flex gap-1" aria-hidden="true">
                  {Array.from({ length: 10 }, (_, i) => (
                    <span
                      key={i}
                      className={`h-3 w-1.5 rounded-full transition-colors ${i < level ? 'bg-[var(--fm-accent)]' : 'bg-[var(--fm-line-strong)]'}`}
                    />
                  ))}
                </span>
                <span className="fm-score w-5 text-right text-sm text-ink">{level}</span>
                <span className="sr-only">Alexa's volume {level} of 10</span>
              </m.div>
            )}
          </AnimatePresence>
          <div
            className={`fm-lightbar is-${light}`}
            style={light === 'volume' ? { transform: `scaleX(${volume})` } : undefined}
            aria-hidden="true"
          />
        </div>
      </div>

      <div className="fm-base" aria-hidden="true" />
    </div>
  )
}
