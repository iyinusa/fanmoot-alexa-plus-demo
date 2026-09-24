/**
 * The JavaScript budget (W5-3), enforced by `vite build` itself.
 *
 * The same two numbers as `fanmoot`'s `make bundle-budget` (its M15-2), both
 * gzipped, because gzip is what crosses the wire (app/spa.py compresses):
 *
 * - **The critical path**: every script `index.html` loads or preloads before
 *   the first screen can render. This is what a judge on a phone at the
 *   judging table waits for.
 * - **Any single chunk**, so one lazy chunk cannot quietly become a second
 *   bundle that the first number never sees.
 *
 * A plugin rather than a script run afterwards, so the budget holds wherever
 * the bundle is built: a laptop, CI and the Docker image's Node stage alike.
 * A breach fails the build and names the file. `make bundle-budget` builds
 * and prints the report.
 */
import { gzipSync } from 'node:zlib'

import type { Plugin } from 'vite'

/**
 * The critical path, gzipped. Measured at 92.6 KB (2026-09-24), down from
 * 107.7 KB, after W5-3 moved the trace inspector, the cards, and Framer
 * Motion's layout and drag features into lazy chunks: React is 44.5 KB of it,
 * and the demo with Framer Motion's core the other 48. The headroom is for
 * ordinary growth, and none for another framework.
 */
export const CRITICAL_PATH_BUDGET = 100 * 1024

/** Any one chunk, gzipped. The entry is the largest, at 48 KB. */
export const CHUNK_BUDGET = 60 * 1024

const REFERENCED = /<(?:script[^>]+src|link[^>]+rel="modulepreload"[^>]+href)="\/?(assets\/[^"]+\.js)"/g

const kb = (bytes: number) => `${(bytes / 1024).toFixed(1)} KB`

export function budget(): Plugin {
  return {
    name: 'fm-bundle-budget',
    apply: 'build',
    enforce: 'post',
    writeBundle(_options, bundle) {
      const html = bundle['index.html']
      if (!html || html.type !== 'asset') return

      const sizes = new Map<string, number>()
      for (const [name, file] of Object.entries(bundle)) {
        if (file.type === 'chunk') sizes.set(name, gzipSync(file.code, { level: 9 }).length)
      }

      const critical = [...new Set([...String(html.source).matchAll(REFERENCED)].map((m) => m[1]!))]
      const criticalBytes = critical.reduce((sum, name) => sum + (sizes.get(name) ?? 0), 0)
      const ranked = [...sizes].sort((a, b) => b[1] - a[1])

      const lines = [
        `critical path ${kb(criticalBytes)} gz (budget ${kb(CRITICAL_PATH_BUDGET)}): ${critical.join(', ')}`,
        ...ranked.slice(0, 8).map(([name, size]) => `  ${kb(size).padStart(9)} gz  ${name}${critical.includes(name) ? '' : '  (lazy)'}`),
      ]
      this.info(`\n${lines.join('\n')}`)

      const failures: string[] = []
      if (criticalBytes > CRITICAL_PATH_BUDGET) failures.push(`the critical path is ${kb(criticalBytes)} gz`)
      for (const [name, size] of ranked) {
        if (size > CHUNK_BUDGET) failures.push(`${name} is ${kb(size)} gz (chunk budget ${kb(CHUNK_BUDGET)})`)
      }
      if (failures.length) this.error(`over the JS budget (W5-3): ${failures.join('; ')}`)
    },
  }
}
