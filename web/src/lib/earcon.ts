/**
 * The soft click a smart display makes when its volume buttons are pressed,
 * played at the new level so the fan hears what they chose.
 *
 * A web page cannot touch the computer's own volume: browsers give no API
 * for it, on purpose. What the device's buttons control is Alexa's voice,
 * and this tone is how the change is heard even when nothing is being said.
 * Web Audio, synthesised on the spot: no file to fetch, and the context is
 * made inside the click, so no autoplay rule stops it.
 */
let context: AudioContext | null = null

export function volumeTick(volume: number) {
  try {
    context ??= new AudioContext()
    if (context.state === 'suspended') void context.resume()
    const now = context.currentTime
    const tone = context.createOscillator()
    const gain = context.createGain()
    tone.type = 'sine'
    // A little higher as it gets louder, as the hardware does.
    tone.frequency.setValueAtTime(520 + 360 * volume, now)
    tone.frequency.exponentialRampToValueAtTime(420 + 300 * volume, now + 0.09)
    gain.gain.setValueAtTime(0.0001, now)
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, 0.3 * volume), now + 0.012)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16)
    tone.connect(gain).connect(context.destination)
    tone.start(now)
    tone.stop(now + 0.18)
  } catch {
    // No Web Audio: the change still shows on the screen.
  }
}
