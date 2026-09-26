/**
 * Web Audio for Alexa's voice and ears (app/voice).
 *
 * - **One context for playback**, created and resumed inside the first tap
 *   or key press anywhere on the page. Browsers start audio suspended until
 *   the page has been interacted with, and Safari on iOS only lets it start
 *   inside the gesture itself — by the time an answer arrives, the gesture
 *   is long gone.
 * - **Capture**: the microphone through an AudioWorklet
 *   (`public/audio/capture-worklet.js`) as 16 kHz PCM, with an analyser on
 *   the same input for the waveform, so the mic is opened once, not twice.
 * - **The endpointer**: when the fan has finished speaking, judged from the
 *   loudness of each chunk and from the words Transcribe sends back.
 */
import { attachAnalyser } from '@/lib/meter'

// ── Playback ─────────────────────────────────────────────────────────────────
let shared: AudioContext | null = null

type AudioSessionNavigator = Navigator & { audioSession?: { type: string } }

/** Safari's audio session: "playback" is heard with the ringer switch off. */
export function audioSessionFor(kind: 'playback' | 'play-and-record') {
  const session = (navigator as AudioSessionNavigator).audioSession
  if (session) {
    try {
      session.type = kind
    } catch {
      // An older Safari without this type: its own default stands.
    }
  }
}

export function playbackContext(): AudioContext | null {
  if (shared) return shared
  if (typeof AudioContext === 'undefined') return null
  shared = new AudioContext()
  return shared
}

function unlock() {
  const context = playbackContext()
  if (context?.state === 'suspended') void context.resume().catch(() => {})
}

if (typeof window !== 'undefined') {
  for (const type of ['pointerdown', 'touchend', 'click', 'keydown']) {
    window.addEventListener(type, unlock, { capture: true, passive: true })
  }
}

// ── Capture ──────────────────────────────────────────────────────────────────
export type Capture = { rate: number; close: () => void }

/** Whether this browser can record for Transcribe at all. */
export function canCapture(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof AudioWorkletNode !== 'undefined'
  )
}

/**
 * Open the mic. `ready(rate)` is called before the first chunk, so the
 * server is told the rate before any audio reaches it. Fails with the
 * `getUserMedia` error's name (`NotAllowedError`…) or `unavailable`.
 */
export async function openCapture(
  ready: (rate: number) => void,
  chunk: (pcm: ArrayBuffer, level: number) => void,
): Promise<Capture | { error: string }> {
  if (!canCapture()) return { error: 'unavailable' }
  audioSessionFor('play-and-record')
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
  } catch (error) {
    return { error: error instanceof DOMException ? error.name : 'unavailable' }
  }
  const stop = () => stream.getTracks().forEach((track) => track.stop())

  const context = new AudioContext()
  try {
    await context.audioWorklet.addModule('/audio/capture-worklet.js')
    if (context.state === 'suspended') await context.resume()
  } catch {
    stop()
    void context.close()
    return { error: 'unavailable' }
  }

  const rate = Math.min(16_000, context.sampleRate)
  const source = context.createMediaStreamSource(stream)
  const analyser = context.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.72
  const worklet = new AudioWorkletNode(context, 'fanmoot-capture', {
    processorOptions: { rate },
    numberOfInputs: 1,
    numberOfOutputs: 1,
    channelCount: 1,
  })
  // Some browsers only run a node whose output goes somewhere: to the
  // speakers, silenced, so the fan never hears themselves.
  const silent = context.createGain()
  silent.gain.value = 0

  ready(rate)
  worklet.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number }>) =>
    chunk(event.data.pcm, event.data.level)
  source.connect(analyser)
  source.connect(worklet)
  worklet.connect(silent).connect(context.destination)
  attachAnalyser(analyser)

  let closed = false
  return {
    rate,
    close: () => {
      if (closed) return
      closed = true
      attachAnalyser(null)
      worklet.port.onmessage = null
      source.disconnect()
      worklet.disconnect()
      stop()
      void context.close()
      audioSessionFor('playback')
    },
  }
}

// ── When the fan has finished ────────────────────────────────────────────────
export type Verdict = 'go' | 'stop' | 'nothing'

/**
 * Decides, a chunk at a time, whether the fan is still talking.
 *
 * The room's own level is read from the first few chunks, and speech is
 * anything well above it for a fifth of a second. A second of quiet after
 * speech ends the question. In a noisy room the quiet may never come, so
 * the words stopping changing for a couple of seconds ends it too. Nothing
 * said for seven seconds is "I didn't hear anything"; twenty seconds is
 * longer than a question, and is answered as it stands.
 */
export class Endpointer {
  private floor = Infinity
  private frames = 0
  private loud = 0
  private quiet = 0
  private elapsed = 0
  private words = ''
  private wordsAt = 0
  private speaking = false

  push(level: number, ms = 100): Verdict {
    this.elapsed += ms
    if (this.frames < 3) {
      this.floor = Math.min(this.floor, level)
      this.frames += 1
    }
    const threshold = Math.max(0.012, Math.min(this.floor, 0.03) * 2.5)
    if (level > threshold) {
      this.loud += ms
      this.quiet = 0
      if (this.loud >= 200) this.speaking = true
    } else {
      this.loud = 0
      this.quiet += ms
    }
    if (this.elapsed >= 20_000) return 'stop'
    if (this.speaking && this.quiet >= 1_000) return 'stop'
    if (this.words && this.elapsed - this.wordsAt >= 2_200) return 'stop'
    if (!this.speaking && !this.words && this.elapsed >= 7_000) return 'nothing'
    return 'go'
  }

  /** Transcribe heard words: the fan is talking, whatever the level says. */
  heard(text: string) {
    if (!text || text === this.words) return
    this.words = text
    this.wordsAt = this.elapsed
    this.speaking = true
    this.quiet = 0
  }
}
