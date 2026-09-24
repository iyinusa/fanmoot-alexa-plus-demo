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
 * Whether the wide column is open is remembered per browser — a convenience,
 * so it survives a reload, and nothing breaks without storage.
 */
import { AnimatePresence, motion, useDragControls } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { AuthState, Facts, TraceFrame } from '@/lib/protocol'
import { CloseIcon } from '@/shell/Icons'
import { TraceInspector } from '@/trace/TraceInspector'

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
    return window.localStorage.getItem(STORE_KEY) !== 'closed'
  } catch {
    return true
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

type Data = { trace: TraceFrame[]; facts: Facts | null; auth: AuthState | null; connected: boolean }

/** A count that flashes when it goes up, so a collapsed panel still says "something happened". */
function Count({ n }: { n: number }) {
  return (
    <motion.span
      key={n}
      initial={{ scale: 1.35, color: 'var(--fm-green-bright)' }}
      animate={{ scale: 1, color: 'var(--fm-text-muted)' }}
      transition={{ duration: 0.5, ease: EASE }}
      className="inline-block font-mono tabular-nums"
    >
      {n}
    </motion.span>
  )
}

export function TraceColumn({ data, open, setOpen }: { data: Data; open: boolean; setOpen: (open: boolean) => void }) {
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Show the MCP trace, ${data.trace.length} frames`}
        className="flex h-full w-full flex-col items-center gap-3 rounded-fm-lg border border-line bg-surface/85 py-4 text-xs text-muted shadow-[var(--fm-shadow)] backdrop-blur transition-colors hover:text-ink"
      >
        <span className={`size-2 rounded-full ${data.connected ? 'bg-live' : 'bg-[var(--fm-red)]'}`} aria-hidden="true" />
        <span className="font-bold tracking-wide [writing-mode:vertical-rl]">MCP trace</span>
        <Count n={data.trace.length} />
      </button>
    )
  }
  return (
    <div className="h-full overflow-hidden rounded-fm-lg border border-line bg-surface/85 shadow-[var(--fm-shadow)] backdrop-blur">
      <TraceInspector
        {...data}
        action={
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded-full px-2 py-0.5 text-xs text-faint transition-colors hover:bg-raised hover:text-ink"
            aria-label="Collapse the MCP trace"
          >
            Hide ›
          </button>
        }
      />
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

export function TraceSheet({ data, open, onClose }: { data: Data; open: boolean; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null)
  // Dragged down by its handle only: the list inside scrolls, and a drag
  // that started on it would fight the scroll.
  const drag = useDragControls()
  useEffect(() => {
    if (!open) return
    close.current?.focus()
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
    }
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="scrim"
            className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            key="sheet"
            role="dialog"
            aria-modal="true"
            aria-label="MCP trace"
            className="fixed inset-x-0 bottom-0 z-50 mx-auto flex h-[88dvh] max-w-3xl flex-col overflow-hidden rounded-t-fm-lg border border-b-0 border-line bg-surface pb-[env(safe-area-inset-bottom)] shadow-[var(--fm-shadow)]"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.38, ease: EASE }}
            drag="y"
            dragControls={drag}
            dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.6 }}
            onDragEnd={(_event, info) => {
              if (info.offset.y > 120 || info.velocity.y > 600) onClose()
            }}
          >
            <div
              className="flex h-5 shrink-0 cursor-grab touch-none items-end justify-center active:cursor-grabbing"
              onPointerDown={(event) => drag.start(event)}
              aria-hidden="true"
            >
              <span className="h-1 w-10 rounded-full bg-[var(--fm-line-strong)]" />
            </div>
            <div className="min-h-0 flex-1">
              <TraceInspector
                {...data}
                action={
                  <button
                    ref={close}
                    type="button"
                    onClick={onClose}
                    aria-label="Close the MCP trace"
                    className="fm-tap -mr-2 grid place-items-center rounded-full text-faint hover:text-ink"
                  >
                    <CloseIcon size={18} />
                  </button>
                }
              />
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
