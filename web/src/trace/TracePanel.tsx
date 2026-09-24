/**
 * Where the trace inspector sits (W3-1): conversation left, trace right.
 *
 * - **Wide (≥ 1280 px):** a column beside the device, as tall as the window
 *   and sticky while the page scrolls. It collapses to a slim rail that still
 *   pulses as frames arrive, for a recording that wants the device alone.
 * - **Narrow:** the device takes the screen, and a button in the page header
 *   opens the inspector as a sheet over it. A phone at the judging table
 *   shows the device first and the proof one tap away.
 *
 * **Collapsed by default** (the creator's call, 2026-09-24): most visitors
 * are fans, not developers, and the device is what they came for. The rail
 * still shows the frame count flashing as calls go out, and the landing
 * screen lights it up and says it is there. Opening or collapsing it is
 * remembered per browser — a convenience, so it survives a reload, and
 * nothing breaks without storage.
 */
import { m } from 'framer-motion'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import type { AuthState, Facts, TraceFrame } from '@/lib/protocol'

/** The inspector itself, off the critical path (W5-3). */
const loadPanes = () => import('@/trace/TracePanes')
const ColumnBody = lazy(() => loadPanes().then((mod) => ({ default: mod.TraceColumnBody })))
const SheetBody = lazy(() => loadPanes().then((mod) => ({ default: mod.TraceSheetBody })))

/** Fetched while the browser is idle, so the sheet opens at once when asked. */
export function prefetchTrace() {
  const idle = window.requestIdleCallback ?? ((run: () => void) => window.setTimeout(run, 1200))
  idle(() => void loadPanes())
}

const WIDE = '(min-width: 1280px)'
const STORE_KEY = 'fm-trace-open'
const EASE = [0.22, 1, 0.36, 1] as const

export function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches)
  useEffect(() => {
    const media = window.matchMedia(WIDE)
    const change = () => setWide(media.matches)
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])
  return wide
}

function readOpen(): boolean {
  try {
    return window.localStorage.getItem(STORE_KEY) === 'open'
  } catch {
    return false
  }
}

/** The column's open state on a wide screen, and the sheet's on a narrow one. */
export function useTracePanel() {
  const wide = useWide()
  const [column, setColumn] = useState(readOpen)
  const [sheet, setSheet] = useState(false)
  const setColumnOpen = useCallback((open: boolean) => {
    setColumn(open)
    try {
      window.localStorage.setItem(STORE_KEY, open ? 'open' : 'closed')
    } catch {
      // Private mode: it simply is not remembered.
    }
  }, [])
  // A sheet left open while the window widens would sit over the column.
  useEffect(() => {
    if (wide) setSheet(false)
  }, [wide])
  return { wide, column, setColumnOpen, sheet, setSheet }
}

export type TraceData = { trace: TraceFrame[]; facts: Facts | null; auth: AuthState | null; connected: boolean }

/** A count that flashes when it goes up, so a collapsed panel still says "something happened". */
function Count({ n }: { n: number }) {
  return (
    <m.span
      key={n}
      initial={{ scale: 1.35, color: 'var(--fm-green-bright)' }}
      animate={{ scale: 1, color: 'var(--fm-text-muted)' }}
      transition={{ duration: 0.5, ease: EASE }}
      className="inline-block font-mono tabular-nums"
    >
      {n}
    </m.span>
  )
}

export function TraceColumn({
  data,
  open,
  setOpen,
  spotlight = false,
}: {
  data: TraceData
  open: boolean
  setOpen: (open: boolean) => void
  /** Lit up while the landing screen points at it (W5-1). */
  spotlight?: boolean
}) {
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Show the MCP trace, ${data.trace.length} frames`}
        className={`flex h-full w-full flex-col items-center gap-3 rounded-fm-lg border border-line bg-surface/85 py-4 text-xs text-muted shadow-[var(--fm-shadow)] backdrop-blur transition-colors hover:text-ink ${
          spotlight ? 'fm-spotlight' : ''
        }`}
      >
        <span className={`size-2 rounded-full ${data.connected ? 'bg-live' : 'bg-[var(--fm-red)]'}`} aria-hidden="true" />
        <span className="font-bold tracking-wide [writing-mode:vertical-rl]">MCP trace</span>
        <Count n={data.trace.length} />
        {/* Collapsed is the default now, so the rail says it opens. */}
        <span aria-hidden="true" className="mt-auto text-base leading-none text-faint">
          ‹
        </span>
      </button>
    )
  }
  return (
    <div
      className={`h-full overflow-hidden rounded-fm-lg border border-line bg-surface/85 shadow-[var(--fm-shadow)] backdrop-blur transition-shadow duration-500 ${
        spotlight ? 'fm-spotlight' : ''
      }`}
    >
      <Suspense fallback={<PanePlaceholder />}>
        <ColumnBody data={data} collapse={() => setOpen(false)} />
      </Suspense>
    </div>
  )
}

/** The column's frame while its chunk arrives: the header, and nothing pretending to be data. */
function PanePlaceholder() {
  return (
    <div className="flex items-center gap-2 px-4 pt-4" aria-hidden="true">
      <span className="size-2.5 rounded-full bg-[var(--fm-line-strong)]" />
      <span className="text-sm font-bold text-muted">MCP trace</span>
    </div>
  )
}

export function TraceButton({ count, connected, onClick }: { count: number; connected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      className="fm-tap inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-line bg-surface/85 px-3 text-xs text-ink shadow-[var(--fm-shadow)] backdrop-blur"
    >
      <span className="relative flex size-2" aria-hidden="true">
        {connected && <span className="absolute inset-0 animate-ping rounded-full bg-live opacity-60" />}
        <span className={`relative size-2 rounded-full ${connected ? 'bg-live' : 'bg-[var(--fm-red)]'}`} />
      </span>
      MCP trace
      <Count n={count} />
    </button>
  )
}

export function TraceSheet({ data, open, onClose }: { data: TraceData; open: boolean; onClose: () => void }) {
  // Not mounted until first asked for, so a phone that never opens it never
  // loads it unless the idle prefetch already has.
  const [wanted, setWanted] = useState(open)
  useEffect(() => {
    if (open) setWanted(true)
  }, [open])
  if (!wanted) return null
  return (
    <Suspense fallback={null}>
      <SheetBody data={data} open={open} onClose={onClose} />
    </Suspense>
  )
}
