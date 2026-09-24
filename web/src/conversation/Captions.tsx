/**
 * The captions track (W2-5, S6-7): the sentence Alexa is saying, along the
 * bottom of the screen, with the words already spoken lit and the rest
 * waiting. Driven by the synthesiser's own word boundaries, so the highlight
 * is where the voice is rather than where a timer guesses it is.
 *
 * The voice is drawn in the same pill (W2-2), so it sits beside the words it
 * belongs to rather than floating over whatever card is on screen. With
 * captions off, the pill is just the voice.
 */
import { AnimatePresence, m } from 'framer-motion'

import type { Caption } from '@/lib/speech'
import { Waveform } from '@/shell/Waveform'

export function Captions({ caption, on, speaking }: { caption: Caption | null; on: boolean; speaking: boolean }) {
  const words = on && caption
  return (
    <AnimatePresence>
      {(words || speaking) && (
        <m.div
          key="captions"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 6 }}
          transition={{ duration: 0.25 }}
          className="pointer-events-none mx-auto flex w-fit max-w-[min(92%,44rem)] items-center gap-3 rounded-fm bg-black/75 px-4 py-2 text-base leading-snug text-white shadow-lg backdrop-blur sm:text-lg"
          // Not a live region: the same words are already announced in the
          // conversation, and hearing every sentence twice is noise.
          aria-hidden="true"
        >
          {speaking && <Waveform mode="speaking" bars={12} className="h-6 w-14 shrink-0" />}
          {words && (
            <span className="text-center">
              <span>{caption.text.slice(0, caption.said)}</span>
              <span className="text-white/45">{caption.text.slice(caption.said)}</span>
            </span>
          )}
        </m.div>
      )}
    </AnimatePresence>
  )
}
