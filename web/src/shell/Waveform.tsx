/**
 * The voice waveform (W2-2): mirrored bars, drawn on a canvas from
 * `lib/meter.ts` every animation frame while there is a voice to draw.
 *
 * Listening, the bars are the microphone's own spectrum. Speaking, they pulse
 * on each word the synthesiser reaches. Idle, the loop stops altogether — a
 * canvas redrawing silence is a laptop fan spinning up for nothing.
 *
 * Reduced motion is respected here as well as in Framer Motion: the bars are
 * drawn once at a calm resting shape and are not animated.
 */
import { useEffect, useRef } from 'react'

import { fill } from '@/lib/meter'

/** `connecting`: the device has lost its connection (W5-4). The light pulses amber; the bars rest. */
export type VoiceMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'connecting'

const FLOOR: Record<VoiceMode, number> = {
  idle: 0,
  muted: 0,
  connecting: 0,
  listening: 0.1,
  thinking: 0.16,
  speaking: 0.14,
}

export function Waveform({
  mode,
  bars = 32,
  className = '',
}: {
  mode: VoiceMode
  bars?: number
  className?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context) return

    const levels = new Float32Array(bars)
    const shown = new Float32Array(bars)
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0

    const draw = (now: number) => {
      const ratio = window.devicePixelRatio || 1
      const width = element.clientWidth
      const height = element.clientHeight
      if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
        element.width = Math.round(width * ratio)
        element.height = Math.round(height * ratio)
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.clearRect(0, 0, width, height)

      if (still) {
        for (let i = 0; i < bars; i++) levels[i] = mode === 'idle' ? 0 : 0.18 + 0.12 * Math.sin(i * 0.8)
      } else {
        fill(levels, now, FLOOR[mode])
        if (mode === 'thinking') {
          // Thinking has no voice: a highlight travels along the bars instead.
          const head = ((now / 900) % 1) * bars
          for (let i = 0; i < bars; i++) {
            const distance = Math.min(Math.abs(i - head), bars - Math.abs(i - head))
            levels[i] = Math.max(levels[i] ?? 0, 0.55 * Math.exp(-(distance * distance) / 10))
          }
        }
      }

      const gradient = context.createLinearGradient(0, 0, width, 0)
      gradient.addColorStop(0, '#4d80ff')
      gradient.addColorStop(0.55, '#2f8fd8')
      gradient.addColorStop(1, '#4fbf57')
      context.fillStyle = gradient

      const gap = Math.max(2, width / bars / 3)
      const barWidth = (width - gap * (bars - 1)) / bars
      const middle = height / 2
      // A canvas laid out narrower than its gaps (collapsed, or mid-animation)
      // has no room for a bar, and a negative radius throws.
      for (let i = 0; barWidth > 0 && i < bars; i++) {
        // Rise quickly, fall gently: bars that snap down read as jitter.
        const target = levels[i] ?? 0
        const previous = shown[i] ?? 0
        const eased = target > previous ? previous + (target - previous) * 0.55 : previous + (target - previous) * 0.18
        shown[i] = eased
        const barHeight = Math.max(barWidth, eased * (height - 2))
        const x = i * (barWidth + gap)
        context.beginPath()
        context.roundRect(x, middle - barHeight / 2, barWidth, barHeight, barWidth / 2)
        context.fill()
      }

      if (!still && mode !== 'idle' && mode !== 'muted' && mode !== 'connecting') frame = requestAnimationFrame(draw)
    }

    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [mode, bars])

  return <canvas ref={canvas} className={className} aria-hidden="true" />
}
