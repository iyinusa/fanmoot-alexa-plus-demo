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
 * Getting the microphone to work, in order:
 *
 * 1. **A secure page.** Browsers only give the mic to `https://` or
 *    `localhost`. A phone opening a laptop's `http://192.168…` address gets
 *    nothing, and is told so, rather than an error that means nothing.
 * 2. **One permission prompt, settled first.** On a desktop the meter opens
 *    its own capture for the waveform. Started at the same moment as
 *    recognition, the two ask for the mic together, and Chrome can fail the
 *    recognition with `not-allowed` or `aborted` while its prompt is still
 *    up. So until the mic has been granted, it is asked for first, on its
 *    own, and recognition starts once the answer is in. Once granted, both
 *    start together as before.
 * 3. **Errors that name the fix.** `network` from Brave or an editor's
 *    built-in browser means it has no speech service at all, not that the
 *    connection dropped. `audio-capture` on a Mac is often the operating
 *    system refusing the browser the mic. Each says where to go.
 * 4. **English.** Recognition listens in the browser's own English, and in
 *    British English for a browser set to any other language: Fanmoot's
 *    names are English, and a French recogniser hears "Arsenal" as French.
 *
 * **Out.** `useSpeaker` speaks sentences as they complete, so Alexa starts
 * talking while the rest of the answer is still streaming, and reports which
 * words have been said for the captions track. Captions are on by default:
 * the demo is watched with the sound off as often as with it on (S6-7, X3-4).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { volumeTick } from '@/lib/earcon'
import { meterAllowed, type MicOpened, openMic, pulse } from '@/lib/meter'

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

/**
 * "Alexa, what's the score?" → "what's the score?" — and "Alexa" alone → "".
 * Recognisers often mishear the wake word ("Alexis", "a Lexa"), so those go
 * too. Not "yeah", which is how Chrome sometimes hears it: "Yeah, post it" is
 * a real answer to Alexa's question, and must reach her whole.
 */
export function withoutWakeWord(text: string): string {
  return text.replace(/^\s*((hey|ok|okay)[\s,]+)?(alexa|alexis|alexia|a\s?lexa|lexa)\b[\s,.!?]*/i, '').trim()
}

function capitalise(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

const MAC = /Mac/.test(navigator.platform) || /Mac OS X/.test(navigator.userAgent)
const MAC_SETTING = MAC ? ' On a Mac, also allow your browser in System Settings → Privacy & Security → Microphone.' : ''

/** Browsers that have the API but no speech service behind it. */
function noSpeechService(): boolean {
  return 'brave' in navigator || /Electron|Code\/|Cursor/i.test(navigator.userAgent)
}

const LISTEN_ERRORS: Record<string, string> = {
  'not-allowed': `The microphone is blocked for this page. Allow it from the icon in the address bar, or type instead.${MAC_SETTING}`,
  'service-not-allowed': MAC
    ? 'Speech recognition is switched off here. In Safari, turn on Dictation in System Settings → Keyboard, or type instead.'
    : 'This browser will not do speech recognition here. Type instead.',
  'audio-capture': `I can't hear a microphone. Check one is connected, or type instead.${MAC_SETTING}`,
  'no-speech': "I didn't hear anything. Tap the mic and try again.",
  'language-not-supported': "This browser can't recognise English speech here. Type instead.",
  insecure:
    'The microphone only works on a secure page. Open the demo at https://alexaplus.fanmoot.com (or on localhost), or type instead.',
  busy: 'Another app is using the microphone. Close it and try again, or type instead.',
}

function listenError(code: string): string {
  if (code === 'network') {
    return noSpeechService()
      ? "This browser has no speech service, so it can't listen. Chrome, Edge and Safari can, or type instead."
      : "Speech recognition couldn't reach its service. Check the connection, or type instead."
  }
  return LISTEN_ERRORS[code] ?? "That didn't work. Type instead."
}

/** From a `getUserMedia` failure to a recognition-style code. */
const MIC_ERRORS: Record<string, string> = {
  NotAllowedError: 'not-allowed',
  SecurityError: 'not-allowed',
  NotFoundError: 'audio-capture',
  OverconstrainedError: 'audio-capture',
  NotReadableError: 'busy',
  AbortError: 'busy',
}

/** English, as the browser speaks it, or British English. */
function listeningLanguage(): string {
  const lang = navigator.language || ''
  return /^en(-|$)/i.test(lang) ? lang : 'en-GB'
}

/** Whether the mic has already been granted to this page, where the browser says. */
async function micGranted(): Promise<boolean | null> {
  try {
    const status = await navigator.permissions.query({ name: 'microphone' as PermissionName })
    return status.state === 'granted'
  } catch {
    return null
  }
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

  const opening = useRef(false)
  /** The browser's permission prompt is up. */
  const [asking, setAsking] = useState(false)

  const begin = useCallback((Ctor: RecognitionCtor, micReady: MicOpened | null) => {
    const rec = new Ctor()
    rec.lang = listeningLanguage()
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
      // In the console as well, for whoever is working out why.
      console.info('speech recognition error:', event.error)
      failed = true
      if (event.error !== 'aborted') setError(listenError(event.error))
    }
    let failed = false
    rec.onend = () => {
      release()
      setInterim('')
      const text = capitalise(withoutWakeWord(final))
      if (text) heard.current(text)
      // Ended with nothing heard and nothing said about why: say so, rather
      // than the mic closing as if it had never opened.
      else if (!failed) setError(LISTEN_ERRORS['no-speech']!)
    }

    setError(null)
    setInterim('')
    recognition.current = rec
    setState('listening')
    try {
      rec.start()
    } catch {
      if (micReady && 'close' in micReady) micReady.close()
      release()
      setError("The microphone didn't start. Type instead.")
      return
    }
    const attach = (opened: MicOpened) => {
      if (!('close' in opened)) return
      // Recognition may have ended before the mic opened.
      if (recognition.current === rec) closeMic.current = opened.close
      else opened.close()
    }
    if (micReady) attach(micReady)
    else void openMic().then(attach)
  }, [release])

  const start = useCallback(async () => {
    const Ctor = recognitionCtor()
    if (!Ctor || recognition.current || opening.current) return
    if (!window.isSecureContext) {
      setError(LISTEN_ERRORS.insecure!)
      return
    }
    setError(null)
    // Granted already, or a touch device (no meter capture to race with):
    // start at once. Otherwise ask for the mic on its own first (see above).
    if (!meterAllowed() || (await micGranted()) === true) {
      begin(Ctor, null)
      return
    }
    opening.current = true
    setAsking(true)
    setState('listening')
    setInterim('')
    const opened = await openMic()
    opening.current = false
    setAsking(false)
    if ('error' in opened && opened.error !== 'unavailable') {
      console.info('microphone refused:', opened.error)
      setState('idle')
      setError(listenError(MIC_ERRORS[opened.error] ?? 'audio-capture'))
      return
    }
    begin(Ctor, opened)
  }, [begin])

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
    asking,
    interim,
    error,
    clearError: useCallback(() => setError(null), []),
    start,
    stop,
    toggle: useCallback(() => (recognition.current ? stop() : void start()), [start, stop]),
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
    /**
     * The device's volume buttons: a step of a tenth, never quite silent,
     * with a click at the new level. Pressing one unmutes the voice, as the
     * hardware's do. Speech synthesis fixes an utterance's volume when it
     * starts, so the change is heard from the next sentence.
     */
    nudgeVolume: useCallback((step: number) => {
      const next = Math.min(1, Math.max(0.1, Math.round((settings.current.volume + step) * 10) / 10))
      setVolume(next)
      setMuted(false)
      volumeTick(next)
    }, []),
  }
}
