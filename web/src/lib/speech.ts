/**
 * Voice in and voice out, from the browser's own Web Speech API (W2-4, W2-5).
 *
 * **In.** `useListener` wraps `SpeechRecognition` (still `webkit`-prefixed in
 * Chrome and Safari; absent in Firefox). Interim words are shown as they are
 * heard, the final transcript becomes a turn, and a leading "Alexa," is
 * dropped the way a device drops its wake word. Where recognition does not
 * exist, or the mic is refused, `supported` / `error` say so and the typed
 * field is the way in — the demo works in every browser and in a quiet room.
 *
 * **Out.** `useSpeaker` speaks sentences as they complete, so Alexa starts
 * talking while the rest of the answer is still streaming, and reports which
 * words have been said for the captions track. Captions are on by default:
 * the demo is watched with the sound off as often as with it on (S6-7, X3-4).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { openMic, pulse } from '@/lib/meter'

// ── Types TypeScript's DOM lib does not carry ────────────────────────────────
type Alternative = { transcript: string }
type RecognitionResult = { isFinal: boolean; length: number; [index: number]: Alternative }
type RecognitionEvent = { resultIndex: number; results: { length: number; [index: number]: RecognitionResult } }
type Recognition = {
  lang: string
  interimResults: boolean
  continuous: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  onspeechstart: (() => void) | null
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** "Alexa, what's the score?" → "what's the score?" — and "Alexa" alone → "". */
export function withoutWakeWord(text: string): string {
  return text.replace(/^\s*(hey\s+)?alexa[\s,.!?]*/i, '').trim()
}

function capitalise(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

const LISTEN_ERRORS: Record<string, string> = {
  'not-allowed': 'The microphone is blocked for this page. Type instead, or allow it in the address bar.',
  'service-not-allowed': 'This browser will not do speech recognition here. Type instead.',
  'audio-capture': 'No microphone was found. Type instead.',
  'no-speech': "I didn't hear anything. Tap the mic and try again.",
  network: 'Speech recognition needs a connection just now. Type instead.',
}

export type ListenState = 'idle' | 'listening'

export function useListener(onHeard: (text: string) => void) {
  const [supported] = useState(() => recognitionCtor() !== null)
  const [state, setState] = useState<ListenState>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recognition = useRef<Recognition | null>(null)
  const closeMic = useRef<(() => void) | null>(null)
  const heard = useRef(onHeard)
  heard.current = onHeard

  const release = useCallback(() => {
    closeMic.current?.()
    closeMic.current = null
    recognition.current = null
    setState('idle')
  }, [])

  const stop = useCallback(() => recognition.current?.stop(), [])

  const start = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor || recognition.current) return
    const rec = new Ctor()
    rec.lang = navigator.language || 'en-GB'
    rec.interimResults = true
    rec.continuous = false
    rec.maxAlternatives = 1
    let final = ''

    rec.onspeechstart = () => pulse(0.6)
    rec.onresult = (event) => {
      let live = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        const words = result?.[0]?.transcript ?? ''
        if (result?.isFinal) final += words
        else live += words
      }
      // A word arriving is a pulse too, for the touch devices where the
      // meter has no microphone of its own to read (lib/meter.ts).
      pulse(0.7)
      setInterim(withoutWakeWord(final + live))
    }
    rec.onerror = (event) => {
      if (event.error !== 'aborted') setError(LISTEN_ERRORS[event.error] ?? "That didn't work. Type instead.")
    }
    rec.onend = () => {
      release()
      setInterim('')
      const text = capitalise(withoutWakeWord(final))
      if (text) heard.current(text)
    }

    setError(null)
    setInterim('')
    recognition.current = rec
    setState('listening')
    try {
      rec.start()
    } catch {
      release()
      setError("The microphone didn't start. Type instead.")
      return
    }
    void openMic().then((close) => {
      // Recognition may have ended before the mic opened.
      if (recognition.current === rec) closeMic.current = close
      else close?.()
    })
  }, [release])

  useEffect(
    () => () => {
      recognition.current?.abort()
      closeMic.current?.()
    },
    [],
  )

  return {
    supported,
    state,
    interim,
    error,
    clearError: useCallback(() => setError(null), []),
    start,
    stop,
    toggle: useCallback(() => (recognition.current ? stop() : start()), [start, stop]),
  }
}

// ── Speaking ─────────────────────────────────────────────────────────────────
/** What the captions track shows: the sentence, and how much of it is said. */
export type Caption = { text: string; said: number }

/** A voice for the locale, preferring the natural-sounding ones browsers ship. */
function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter((voice) => voice.lang.toLowerCase().startsWith('en'))
  const local = navigator.language.toLowerCase()
  const good = (voice: SpeechSynthesisVoice) => /natural|neural|premium|enhanced|google/i.test(voice.name)
  const ranked = [
    english.find((v) => v.lang.toLowerCase() === local && good(v)),
    english.find((v) => v.lang.toLowerCase() === 'en-gb' && good(v)),
    english.find((v) => good(v)),
    english.find((v) => v.lang.toLowerCase() === local),
    english.find((v) => v.default),
    english[0],
  ]
  return ranked.find(Boolean) ?? null
}

/**
 * Sentences that are complete in `text` from `from` onwards, and where the
 * unfinished remainder starts. A sentence ends at . ! ? or … followed by a
 * space — so "2.5" and "fanmoot.com" are not split in two.
 */
export function completeSentences(text: string, from: number): { sentences: string[]; next: number } {
  const sentences: string[] = []
  const pattern = /[^.!?…]*[.!?…]+["'’”)\]]*\s+/gy
  pattern.lastIndex = from
  let next = from
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text))) {
    const sentence = match[0].trim()
    if (sentence) sentences.push(sentence)
    next = pattern.lastIndex
  }
  return { sentences, next }
}

export function useSpeaker() {
  const [supported] = useState(() => typeof window !== 'undefined' && 'speechSynthesis' in window)
  const [speaking, setSpeaking] = useState(false)
  const [caption, setCaption] = useState<Caption | null>(null)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(0.9)
  const voice = useRef<SpeechSynthesisVoice | null>(null)
  const pending = useRef(0)
  const settings = useRef({ muted, volume })
  settings.current = { muted, volume }

  useEffect(() => {
    if (!supported) return
    const choose = () => {
      voice.current = pickVoice(window.speechSynthesis.getVoices())
    }
    choose()
    window.speechSynthesis.addEventListener('voiceschanged', choose)
    return () => {
      window.speechSynthesis.removeEventListener('voiceschanged', choose)
      window.speechSynthesis.cancel()
    }
  }, [supported])

  const stop = useCallback(() => {
    pending.current = 0
    if (supported) window.speechSynthesis.cancel()
    setSpeaking(false)
    setCaption(null)
  }, [supported])

  const say = useCallback(
    (sentence: string) => {
      if (!supported || !sentence.trim()) return
      if (settings.current.muted) {
        // Silent, but still captioned: a reader with the sound off gets the
        // same line at the bottom of the screen.
        setCaption({ text: sentence, said: sentence.length })
        return
      }
      const utterance = new SpeechSynthesisUtterance(sentence)
      if (voice.current) utterance.voice = voice.current
      utterance.lang = voice.current?.lang ?? navigator.language
      utterance.volume = settings.current.volume
      utterance.rate = 1.04
      utterance.onstart = () => {
        setSpeaking(true)
        setCaption({ text: sentence, said: 0 })
        pulse(0.8)
      }
      utterance.onboundary = (event) => {
        if (event.name && event.name !== 'word') return
        pulse(0.85)
        const said = event.charIndex + (event.charLength || sentence.slice(event.charIndex).search(/\s|$/))
        setCaption({ text: sentence, said })
      }
      const done = () => {
        pending.current = Math.max(0, pending.current - 1)
        setCaption({ text: sentence, said: sentence.length })
        if (pending.current === 0) setSpeaking(false)
      }
      utterance.onend = done
      utterance.onerror = done
      pending.current += 1
      window.speechSynthesis.speak(utterance)
    },
    [supported],
  )

  return {
    supported,
    speaking,
    caption,
    muted,
    volume,
    say,
    stop,
    clearCaption: useCallback(() => setCaption(null), []),
    setMuted: useCallback((value: boolean) => {
      setMuted(value)
      if (value && supported) {
        pending.current = 0
        window.speechSynthesis.cancel()
        setSpeaking(false)
      }
    }, [supported]),
    /** The device's volume buttons: a step of a tenth, and never quite silent. */
    nudgeVolume: useCallback((step: number) => setVolume((v) => Math.min(1, Math.max(0.1, Math.round((v + step) * 10) / 10))), []),
  }
}
