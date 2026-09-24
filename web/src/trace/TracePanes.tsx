/**
 * The trace inspector's two containers (W3-1), in a chunk of their own (W5-3).
 *
 * Nothing here is needed for the first paint: on a narrow screen the sheet
 * opens only when asked, and on a wide one the column fills in a moment after
 * the device has drawn. Keeping the inspector, its JSON view, the cURL
 * builder and Framer Motion's drag feature out of the entry chunk is most of
 * what brought the critical path under its budget. `TracePanel.tsx` loads
 * this lazily and prefetches it while the browser is idle.
 */
import { AnimatePresence, domMax, LazyMotion, m, useDragControls } from 'framer-motion'
import { useEffect, useRef } from 'react'

import { CloseIcon } from '@/shell/Icons'
import type { TraceData } from '@/trace/TracePanel'
import { TraceInspector } from '@/trace/TraceInspector'

const EASE = [0.22, 1, 0.36, 1] as const

export function TraceColumnBody({ data, collapse }: { data: TraceData; collapse: () => void }) {
  return (
    <TraceInspector
      {...data}
      action={
        <button
          type="button"
          onClick={collapse}
          className="rounded-full px-2 py-0.5 text-xs text-faint transition-colors hover:bg-raised hover:text-ink"
          aria-label="Collapse the MCP trace"
        >
          Hide ›
        </button>
      }
    />
  )
}

export function TraceSheetBody({ data, open, onClose }: { data: TraceData; open: boolean; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null)
  const sheet = useRef<HTMLDivElement>(null)
  // Dragged down by its handle only: the list inside scrolls, and a drag
  // that started on it would fight the scroll.
  const drag = useDragControls()
  useEffect(() => {
    if (!open) return
    // A modal sheet (W5-5): focus goes in, Tab stays in, and focus goes back
    // to whatever opened it — the header's "MCP trace" button — on close.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    close.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !sheet.current) return
      const focusable = [...sheet.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
      const first = focusable[0]
      const last = focusable.at(-1)
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      opener?.focus({ preventScroll: true })
    }
  }, [open, onClose])

  return (
    // Drag is in `domMax`, which only this chunk loads.
    <LazyMotion features={domMax}>
    <AnimatePresence>
      {open && (
        <>
          <m.div
            key="scrim"
            className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <m.div
            key="sheet"
            ref={sheet}
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
          </m.div>
        </>
      )}
    </AnimatePresence>
    </LazyMotion>
  )
}
