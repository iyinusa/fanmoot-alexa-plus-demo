/**
 * What the waveform draws (W2-2): one number per band, read every frame.
 *
 * Two sources, and the waveform does not know which it has:
 *
 * - **The microphone**, while the fan is talking: an `AnalyserNode` on the
 *   real input, so the bars are the fan's own voice — its loudness and its
 *   spectrum, bucketed into bands across the range speech lives in.
 * - **Alexa's own voice**, while she talks with Amazon Polly: the answer is
 *   played through Web Audio (lib/speech.ts), and an analyser on its output
 *   draws it exactly as the mic's draws the fan.
 * - **Pulses**, while the browser's own synthesiser talks, where Polly is not
 *   available. That plays through the browser, not through Web Audio, so
 *   there is no signal to analyse. What
 *   there is, is a `boundary` event at every word the synthesiser reaches.
 *   Each one is a pulse, the envelope decays between them, and the bars move
 *   with the words actually being spoken rather than with a loop.
 *
 * A module, not React state: it is read sixty times a second by a canvas, and
 * a re-render per frame would be the whole app redrawing to move some bars.
 */

let analyser: AnalyserNode | null = null
let bins: Uint8Array<ArrayBuffer> | null = null
let envelope = 0
let lastAt = 0

/** The mic, or nothing. Called by the listener as it opens and closes. */
export function attachAnalyser(node: AnalyserNode | null) {
  analyser = node
  bins = node ? new Uint8Array(node.frequencyBinCount) : null
}

/** Stop drawing `node`, if it is still the one being drawn. */
export function detachAnalyser(node: AnalyserNode) {
  if (analyser === node) attachAnalyser(null)
}

/** A word was said (or heard). `strength` 0–1. */
export function pulse(strength = 1) {
  envelope = Math.min(1, Math.max(envelope, strength))
}

/** A cheap, smooth, deterministic wobble per band, so pulse-driven bars differ. */
function wobble(band: number, t: number): number {
  return 0.55 + 0.45 * Math.sin(t / 170 + band * 1.7) * Math.cos(t / 290 + band * 0.9)
}

/**
 * Fill `out` with a level per band, 0–1. `floor` is the resting level the
 * current mode wants (a listening shimmer, silence when idle).
 */
export function fill(out: Float32Array, now: number, floor: number) {
  const dt = lastAt ? Math.min(100, now - lastAt) : 16
  lastAt = now
  // A word's pulse fades over about a third of a second.
  envelope *= Math.exp(-dt / 320)

  if (analyser && bins) {
    analyser.getByteFrequencyData(bins)
    // Speech sits roughly between 85 Hz and 4 kHz; the bands are spread
    // logarithmically across it, the way an ear hears it.
    const nyquist = analyser.context.sampleRate / 2
    const perBin = nyquist / bins.length
    const lo = Math.log(85)
    const hi = Math.log(4000)
    for (let band = 0; band < out.length; band++) {
      const from = Math.exp(lo + ((hi - lo) * band) / out.length)
      const to = Math.exp(lo + ((hi - lo) * (band + 1)) / out.length)
      const a = Math.max(0, Math.floor(from / perBin))
      const b = Math.min(bins.length - 1, Math.max(a, Math.ceil(to / perBin)))
      let peak = 0
      for (let i = a; i <= b; i++) peak = Math.max(peak, bins[i] ?? 0)
      // Gain and a gentle curve: a normal speaking voice should fill the bar
      // without a shout pinning it.
      const level = Math.min(1, Math.pow(peak / 255, 1.4) * 1.6)
      out[band] = Math.max(floor * wobble(band, now), level)
    }
    return
  }

  for (let band = 0; band < out.length; band++) {
    out[band] = Math.max(floor, envelope) * wobble(band, now)
  }
}

/** Touch devices do not get the meter's own capture (see `openMic`). */
export function meterAllowed(): boolean {
  return !window.matchMedia?.('(pointer: coarse)').matches
}

/**
 * What opening the microphone came to: a closer, or why not. `error` is the
 * `getUserMedia` failure's name (`NotAllowedError`, `NotFoundError`,
 * `NotReadableError`…), or `unavailable` where there is no `getUserMedia`
 * at all (an insecure origin) or the meter is not used (touch).
 */
export type MicOpened = { close: () => void } | { error: string }

/**
 * Open the microphone for the meter.
 *
 * Not opened on touch devices: on mobile browsers a second capture of the
 * mic beside speech recognition can make recognition fail with
 * `audio-capture`, and hearing the fan matters more than drawing them.
 */
export async function openMic(): Promise<MicOpened> {
  if (!navigator.mediaDevices?.getUserMedia || !meterAllowed()) return { error: 'unavailable' }
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    })
  } catch (error) {
    return { error: error instanceof DOMException ? error.name : 'unavailable' }
  }
  const context = new AudioContext()
  const source = context.createMediaStreamSource(stream)
  const node = context.createAnalyser()
  node.fftSize = 1024
  node.smoothingTimeConstant = 0.72
  source.connect(node)
  attachAnalyser(node)
  return {
    close: () => {
      if (analyser === node) attachAnalyser(null)
      source.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      void context.close()
    },
  }
}
