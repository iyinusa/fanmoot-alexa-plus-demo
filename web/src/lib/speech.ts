/**
 * Voice in and voice out (W2-4, W2-5): Alexa's ears and Alexa's voice.
 *
 * Both run on AWS where the session says they can (app/voice), and fall back
 * to the browser's own Web Speech API where they cannot — no AWS credentials,
 * an IAM policy that does not allow them, the day's ceiling reached.
 *
 * **In: Amazon Transcribe.** `useListener` records the mic through an
 * AudioWorklet (lib/audio.ts) — which Chrome, Edge, Firefox, Safari and Brave
 * all have — and streams it up `/ws`, where the server relays it to
 * Transcribe. Words come back as they are heard and are shown big; the
 * endpointer decides when the fan has stopped, and the finished sentence
 * becomes a turn. A leading "Alexa," is dropped the way a device drops its
 * wake word. The browser's `SpeechRecognition` is the fallback, and the typed
 * field is always there.
 *
 * Getting the microphone to work, in order:
 *
 * 1. **A secure page.** Browsers only give the mic to `https://` or
 *    `localhost`. A phone opening a laptop's `http://192.168…` address gets
 *    nothing, and is told so, rather than an error that means nothing.
 * 2. **One capture.** The mic is opened once, and the same input feeds the
 *    waveform and Transcribe. (The fallback recogniser opens its own, so the
 *    meter's capture asks for permission first there — see `startBrowser`.)
 * 3. **Errors that name the fix.** A blocked mic, a Mac refusing the browser
 *    the mic, another app holding it: each says where to go.
 *
 * **Out: Amazon Polly.** `useSpeaker` sends each sentence to Polly as it
 * completes (`POST /api/speech`), fetching the next while this one plays, so
 * Alexa starts talking while the rest of the answer is still streaming. The
 * audio plays through Web Audio, with an analyser on it for the waveform, and
 * the captions follow the audio's own clock. The browser's `speechSynthesis`
 * is the fallback. Captions are on by default: the demo is watched with the
 * sound off as often as with it on (S6-7, X3-4).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  audioSessionFor,
  canCapture,
  type Capture,
  Endpointer,
  openCapture,
  playbackContext,
} from '@/lib/audio'
import { volumeTick } from '@/lib/earcon'
import { attachAnalyser, detachAnalyser, meterAllowed, type MicOpened, openMic, pulse } from '@/lib/meter'
import type { ListenFrame } from '@/lib/protocol'

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

/** Alexa's ears on the server (lib/socket.ts `ears`), when the session has them. */
export type RemoteEars = {
  available: boolean
  start: (id: number, rate: number) => boolean
  audio: (pcm: ArrayBuffer) => void
  stop: (id: number) => void
  cancel: (id: number) => void
  subscribe: (hear: (frame: ListenFrame) => void) => () => void
}

/** Alexa's voice on the server (lib/socket.ts `speech`), when the session has it. */
export type RemoteVoice = {
  available: boolean
  fetch: (text: string, signal: AbortSignal) => Promise<ArrayBuffer>
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

/** Browsers that have the recognition API but no speech service behind it. */
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
  offline: "I'm not connected just now, so I can't listen. Try again in a moment, or type instead.",
  garbled: "I couldn't hear that properly. Try again, or type instead.",
  unavailable: "I can't listen in this browser just now. Type instead.",
}

function listenError(code: string): string {
  if (code === 'network') {
    return noSpeechService()
      ? "This browser has no speech service, so it can't listen here just now. Type instead."
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

/**
 * Why the server would not listen, where the browser's own recogniser (if it
 * has one) should take over: off, refused by AWS, over a ceiling.
 */
const HAND_BACK = new Set(['off', 'unavailable', 'no_credentials', 'daily_cap', 'listen_cap'])

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

type RemoteRun = {
  id: number
  capture: Capture | null
  finishing: boolean
  heard: string
  unsubscribe: () => void
  timer?: number
}

export function useListener(onHeard: (text: string) => void, remote: RemoteEars | null = null) {
  const [browserCan] = useState(() => recognitionCtor() !== null)
  const [remoteCan] = useState(canCapture)
  /** The server said no for this page: the browser's recogniser from now on. */
  const [handedBack, setHandedBack] = useState(false)
  const serverListens = Boolean(remote?.available) && remoteCan && !handedBack
  const supported = serverListens || browserCan

  const [state, setState] = useState<ListenState>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** The browser's permission prompt is up. */
  const [asking, setAsking] = useState(false)
  const heard = useRef(onHeard)
  heard.current = onHeard
  const ears = useRef(remote)
  ears.current = remote
  const viaServer = useRef(serverListens)
  viaServer.current = serverListens

  // ── The browser's own recogniser (the fallback) ──────────────────────────
  const recognition = useRef<Recognition | null>(null)
  const closeMic = useRef<(() => void) | null>(null)
  const opening = useRef(false)

  const release = useCallback(() => {
    closeMic.current?.()
    closeMic.current = null
    recognition.current = null
    setState('idle')
  }, [])

  const begin = useCallback((Ctor: RecognitionCtor, micReady: MicOpened | null) => {
    const rec = new Ctor()
    rec.lang = listeningLanguage()
    rec.interimResults = true
    rec.continuous = false
    rec.maxAlternatives = 1
    let final = ''
    let failed = false

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

  /**
   * On a desktop the meter opens its own capture for the waveform. Started at
   * the same moment as recognition, the two ask for the mic together, and
   * Chrome can fail the recognition with `not-allowed` while its prompt is
   * still up. So until the mic has been granted, it is asked for first, on
   * its own, and recognition starts once the answer is in.
   */
  const startBrowser = useCallback(async () => {
    const Ctor = recognitionCtor()
    if (!Ctor || recognition.current || opening.current) return
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

  // ── Transcribe, through the server ───────────────────────────────────────
  const run = useRef<RemoteRun | null>(null)
  const sequence = useRef(0)
  /** Streams in a row that failed outright. Two, and the browser takes over. */
  const failures = useRef(0)

  /** Close everything this utterance opened. `said` null: nothing to ask. */
  const endRemote = useCallback((said: string | null, problem?: string) => {
    const current = run.current
    if (!current) return
    run.current = null
    current.capture?.close()
    current.unsubscribe()
    window.clearTimeout(current.timer)
    setInterim('')
    setAsking(false)
    setState('idle')
    if (problem) {
      setError(problem)
      return
    }
    if (said === null) return
    const text = capitalise(withoutWakeWord(said))
    if (text) heard.current(text)
    else setError(LISTEN_ERRORS['no-speech']!)
  }, [])

  /** The fan has stopped (or tapped): the last words are on their way. */
  const finishRemote = useCallback(() => {
    const current = run.current
    if (!current || current.finishing) return
    current.finishing = true
    if (!current.capture) {
      // Stopped while the permission prompt was still up: nothing was said.
      ears.current?.cancel(current.id)
      endRemote(null)
      return
    }
    current.capture.close()
    current.capture = null
    ears.current?.stop(current.id)
    // The last words normally land within a second. If they never do, what
    // was heard so far is still the fan's question.
    current.timer = window.setTimeout(() => endRemote(current.heard), 7000)
  }, [endRemote])

  const startRemote = useCallback(async () => {
    const server = ears.current
    if (!server || run.current) return
    const id = ++sequence.current
    const endpointer = new Endpointer()
    const current: RemoteRun = { id, capture: null, finishing: false, heard: '', unsubscribe: () => {} }
    run.current = current
    setError(null)
    setInterim('')
    setState('listening')

    current.unsubscribe = server.subscribe((frame) => {
      if (frame.id !== id || run.current !== current) return
      if (frame.type === 'listen.partial') {
        current.heard = frame.text
        endpointer.heard(frame.text)
        setInterim(withoutWakeWord(frame.text))
      } else if (frame.type === 'listen.final') {
        failures.current = 0
        endRemote(frame.text || current.heard)
      } else if (current.heard) {
        // It broke after hearing something: that is still the question.
        endRemote(current.heard)
      } else if (HAND_BACK.has(frame.code) || ++failures.current >= 2) {
        // Transcribe is off, refused or spent, or has failed twice running:
        // this browser's own recogniser takes over, for this question and the
        // rest of the visit — or, with none, the typed field does.
        console.info('server listening unavailable:', frame.code)
        setHandedBack(true)
        if (browserCan) {
          endRemote(null)
          void startBrowser()
        } else {
          endRemote(null, frame.code === 'listen_cap' ? frame.message : LISTEN_ERRORS.unavailable)
        }
      } else {
        endRemote(null, LISTEN_ERRORS.garbled)
      }
    })

    if ((await micGranted()) !== true) setAsking(true)
    let connected = true
    const opened = await openCapture(
      (rate) => {
        if (run.current === current) connected = server.start(id, rate)
      },
      (pcm, level) => {
        if (run.current !== current || current.finishing) return
        server.audio(pcm)
        const verdict = endpointer.push(level)
        if (verdict === 'stop') finishRemote()
        else if (verdict === 'nothing') {
          server.cancel(id)
          endRemote('')
        }
      },
    )
    setAsking(false)
    if ('error' in opened) {
      if (run.current !== current) return
      console.info('microphone refused:', opened.error)
      if (opened.error === 'unavailable' && browserCan) {
        // No AudioWorklet after all: the browser's recogniser instead.
        setHandedBack(true)
        endRemote(null)
        void startBrowser()
        return
      }
      endRemote(null, listenError(MIC_ERRORS[opened.error] ?? (opened.error === 'unavailable' ? 'unavailable' : 'audio-capture')))
      return
    }
    if (run.current !== current || current.finishing) {
      opened.close()
      return
    }
    current.capture = opened
    if (!connected) endRemote(null, LISTEN_ERRORS.offline)
  }, [browserCan, endRemote, finishRemote, startBrowser])

  // ── The controls ─────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    if (run.current || recognition.current || opening.current) return
    if (!window.isSecureContext) {
      setError(LISTEN_ERRORS.insecure!)
      return
    }
    setError(null)
    if (viaServer.current) await startRemote()
    else if (browserCan) await startBrowser()
  }, [browserCan, startBrowser, startRemote])

  const stop = useCallback(() => {
    if (run.current) finishRemote()
    else recognition.current?.stop()
  }, [finishRemote])

  useEffect(
    () => () => {
      const current = run.current
      if (current) {
        ears.current?.cancel(current.id)
        current.capture?.close()
        current.unsubscribe()
        window.clearTimeout(current.timer)
      }
      recognition.current?.abort()
      closeMic.current?.()
    },
    [],
  )

  return {
    supported,
    /** Whose ears: Amazon Transcribe, or the browser's own recogniser. */
    engine: serverListens ? ('transcribe' as const) : browserCan ? ('browser' as const) : null,
    state,
    asking,
    interim,
    error,
    clearError: useCallback(() => setError(null), []),
    start,
    stop,
    toggle: useCallback(
      () => (run.current || recognition.current ? stop() : void start()),
      [start, stop],
    ),
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

/** The end of the word `index` falls in, so a caption never stops mid-word. */
function wordEnd(text: string, index: number): number {
  if (index >= text.length) return text.length
  const rest = text.slice(index).search(/\s|$/)
  return index + Math.max(0, rest)
}

type Queued = { text: string; audio: Promise<AudioBuffer>; abort: AbortController }
type Playing = { source: AudioBufferSourceNode; timer: number }

/** How long a suspended context gets to start before the browser's voice is used. */
const RESUME_WAIT_MS = 400

export function useSpeaker(remote: RemoteVoice | null = null) {
  const [browserCan] = useState(() => typeof window !== 'undefined' && 'speechSynthesis' in window)
  const [audioCan] = useState(() => typeof AudioContext !== 'undefined')
  /** Polly refused this page (401, 429, 503): the browser's voice from now on. */
  const [refused, setRefused] = useState(false)
  const pollySpeaks = Boolean(remote?.available) && audioCan && !refused
  const supported = pollySpeaks || browserCan

  const [speaking, setSpeaking] = useState(false)
  const [caption, setCaption] = useState<Caption | null>(null)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(0.9)
  const settings = useRef({ muted, volume })
  settings.current = { muted, volume }
  const voiceRemote = useRef(remote)
  voiceRemote.current = remote
  const viaPolly = useRef(pollySpeaks)
  viaPolly.current = pollySpeaks

  // ── The browser's own voice (the fallback) ───────────────────────────────
  const voice = useRef<SpeechSynthesisVoice | null>(null)
  const pending = useRef(0)

  useEffect(() => {
    if (!browserCan) return
    const choose = () => {
      voice.current = pickVoice(window.speechSynthesis.getVoices())
    }
    choose()
    window.speechSynthesis.addEventListener('voiceschanged', choose)
    return () => {
      window.speechSynthesis.removeEventListener('voiceschanged', choose)
      window.speechSynthesis.cancel()
    }
  }, [browserCan])

  const browserSay = useCallback(
    (sentence: string) => {
      if (!browserCan) {
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
    [browserCan],
  )

  // ── Polly, through Web Audio ─────────────────────────────────────────────
  const queue = useRef<Queued[]>([])
  const generation = useRef(0)
  const pumping = useRef(false)
  const playing = useRef<Playing | null>(null)
  const output = useRef<{ gain: GainNode; analyser: AnalyserNode } | null>(null)
  /** This answer fell back to the browser's voice: finish it in that voice. */
  const degraded = useRef(false)

  const outputFor = useCallback((context: AudioContext) => {
    if (!output.current) {
      const gain = context.createGain()
      const analyser = context.createAnalyser()
      analyser.fftSize = 1024
      analyser.smoothingTimeConstant = 0.72
      gain.connect(analyser).connect(context.destination)
      output.current = { gain, analyser }
    }
    return output.current
  }, [])

  const play = useCallback(
    (context: AudioContext, buffer: AudioBuffer, text: string, gen: number) =>
      new Promise<void>((resolve) => {
        const { gain, analyser } = outputFor(context)
        gain.gain.value = settings.current.volume
        const source = context.createBufferSource()
        source.buffer = buffer
        source.connect(gain)
        audioSessionFor('playback')
        const started = context.currentTime
        const timer = window.setInterval(() => {
          const progress = Math.min(1, (context.currentTime - started) / Math.max(0.1, buffer.duration))
          if (gen === generation.current) setCaption({ text, said: wordEnd(text, Math.round(progress * text.length)) })
        }, 90)
        source.onended = () => {
          window.clearInterval(timer)
          if (playing.current?.source === source) playing.current = null
          detachAnalyser(analyser)
          if (gen === generation.current) setCaption({ text, said: text.length })
          resolve()
        }
        playing.current = { source, timer }
        setSpeaking(true)
        setCaption({ text, said: 0 })
        attachAnalyser(analyser)
        source.start()
      }),
    [outputFor],
  )

  /** Everything still queued, in the browser's voice instead. */
  const fallBack = useCallback(
    (from: Queued[]) => {
      degraded.current = true
      for (const item of from) {
        item.abort.abort()
        browserSay(item.text)
      }
    },
    [browserSay],
  )

  const pump = useCallback(
    async (gen: number) => {
      pumping.current = true
      try {
        while (gen === generation.current && queue.current.length) {
          const item = queue.current[0]!
          const context = playbackContext()
          let buffer: AudioBuffer
          try {
            if (!context) throw new Error('No Web Audio.')
            buffer = await item.audio
            if (context.state !== 'running') {
              // Nobody has tapped the page yet, so the browser will not play.
              await Promise.race([context.resume(), new Promise((r) => window.setTimeout(r, RESUME_WAIT_MS))])
              // Read again: `resume()` changes it, which TypeScript cannot see.
              if ((context.state as AudioContextState) !== 'running') throw new Error('Audio is not allowed to start yet.')
            }
          } catch (error) {
            if (gen !== generation.current) return
            const status = (error as { status?: number }).status
            console.info('Polly voice unavailable:', status ?? (error as Error).message)
            // Refused, not just lost on the way: stop asking for this page.
            if (status === 401 || status === 429 || status === 503) setRefused(true)
            fallBack(queue.current.splice(0))
            return
          }
          if (gen !== generation.current) return
          queue.current.shift()
          await play(context, buffer, item.text, gen)
        }
      } finally {
        if (gen === generation.current) {
          pumping.current = false
          if (!queue.current.length && pending.current === 0) setSpeaking(false)
        }
      }
    },
    [fallBack, play],
  )

  const halt = useCallback(() => {
    generation.current += 1
    pumping.current = false
    degraded.current = false
    for (const item of queue.current.splice(0)) item.abort.abort()
    const current = playing.current
    if (current) {
      playing.current = null
      window.clearInterval(current.timer)
      try {
        current.source.stop()
      } catch {
        // Already stopped.
      }
    }
    if (output.current) detachAnalyser(output.current.analyser)
    pending.current = 0
    if (browserCan) window.speechSynthesis.cancel()
    setSpeaking(false)
  }, [browserCan])

  useEffect(() => () => halt(), [halt])

  const stop = useCallback(() => {
    halt()
    setCaption(null)
  }, [halt])

  const say = useCallback(
    (sentence: string) => {
      if (!supported || !sentence.trim()) return
      if (settings.current.muted) {
        // Silent, but still captioned: a reader with the sound off gets the
        // same line at the bottom of the screen.
        setCaption({ text: sentence, said: sentence.length })
        return
      }
      const server = voiceRemote.current
      const context = viaPolly.current && !degraded.current && server ? playbackContext() : null
      if (!server || !context) {
        browserSay(sentence)
        return
      }
      const abort = new AbortController()
      // Fetched now, played in turn: the next sentence is ready as this one ends.
      const audio = server.fetch(sentence, abort.signal).then((bytes) => context.decodeAudioData(bytes))
      audio.catch(() => {}) // settled in `pump`, in order
      queue.current.push({ text: sentence, audio, abort })
      if (!pumping.current) void pump(generation.current)
    },
    [supported, browserSay, pump],
  )

  return {
    supported,
    /** Whose voice: Amazon Polly, or the browser's own synthesiser. */
    engine: pollySpeaks ? ('polly' as const) : browserCan ? ('browser' as const) : null,
    speaking,
    caption,
    muted,
    volume,
    say,
    stop,
    clearCaption: useCallback(() => setCaption(null), []),
    setMuted: useCallback(
      (value: boolean) => {
        setMuted(value)
        if (value) halt()
      },
      [halt],
    ),
    /**
     * The device's volume buttons: a step of a tenth, never quite silent,
     * with a click at the new level. Pressing one unmutes the voice, as the
     * hardware's do. Polly's voice changes at once; the browser's own fixes
     * an utterance's volume when it starts, so it is heard from the next one.
     */
    nudgeVolume: useCallback((step: number) => {
      const next = Math.min(1, Math.max(0.1, Math.round((settings.current.volume + step) * 10) / 10))
      setVolume(next)
      setMuted(false)
      const gain = output.current?.gain
      if (gain) gain.gain.setTargetAtTime(next, gain.context.currentTime, 0.03)
      volumeTick(next)
    }, []),
  }
}
