/**
 * The microphone as Amazon Transcribe wants it: mono, 16-bit PCM, 16 kHz,
 * a tenth of a second at a time (app/voice/listen.py).
 *
 * An AudioWorklet, because every current browser has one — Chrome, Edge,
 * Firefox, Safari, Brave — where speech *recognition* exists in only some of
 * them. The browser's own rate (44.1 or 48 kHz, usually) is brought down by
 * averaging each run of input samples into one output sample: a box filter,
 * which is plenty for speech on its way to a recogniser. Each chunk goes to
 * the page with its loudness, which is how the page hears the fan stop.
 *
 * Plain JavaScript in `public/`, served from this origin: a worklet module is
 * loaded as a script, and the page's CSP allows scripts from 'self' only.
 */
class FanmootCapture extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.rate = Math.min(options.processorOptions?.rate ?? 16000, sampleRate)
    this.step = sampleRate / this.rate
    this.size = Math.round(this.rate / 10)
    this.chunk = new Int16Array(this.size)
    this.filled = 0
    this.position = 0
    this.sum = 0
    this.count = 0
    this.energy = 0
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    for (let i = 0; i < channel.length; i++) {
      this.sum += channel[i]
      this.count += 1
      this.position += 1
      if (this.position < this.step) continue
      this.position -= this.step
      const sample = Math.max(-1, Math.min(1, this.sum / this.count))
      this.sum = 0
      this.count = 0
      this.energy += sample * sample
      this.chunk[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      if (this.filled === this.size) {
        const level = Math.sqrt(this.energy / this.size)
        this.port.postMessage({ pcm: this.chunk.buffer, level }, [this.chunk.buffer])
        this.chunk = new Int16Array(this.size)
        this.filled = 0
        this.energy = 0
      }
    }
    return true
  }
}

registerProcessor('fanmoot-capture', FanmootCapture)
