/**
 * The card stack (W2-6): every view this conversation has shown, newest on
 * top, the older ones one tap away.
 *
 * Each card is a live `ViewFrame` and stays mounted while it is in the stack,
 * so a reply half-typed into the thread card survives a look at the
 * scoreboard. Only the top card is operable; the edges behind it are drawn,
 * not rendered twice.
 *
 * A card may carry a `label` and a `link` of its own: a take just posted, read
 * back from Fanmoot (W4-4). It is named for what it proves — "Live on
 * fanmoot.com" — and the take's page on fanmoot.com is one tap away, because
 * fanmoot.com itself cannot be framed here.
 */
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'

import type { ViewPayload, ViewResource } from '@/lib/socket'
import type { Theme } from '@/lib/theme'
import { CloseIcon, ExpandIcon, ExternalIcon } from '@/shell/Icons'
import { type DisplayMode, type ViewHost, ViewFrame } from '@/views/ViewFrame'

const EASE = [0.22, 1, 0.36, 1] as const

function hostOf(link: string): string {
  try {
    return new URL(link).host
  } catch {
    return 'the web'
  }
}

/** "ui://fanmoot/scoreboard" → "Scoreboard". */
export function viewLabel(uri: string): string {
  const name = uri.split('/').pop() ?? uri
  return name === 'thread' ? 'Match thread' : name.charAt(0).toUpperCase() + name.slice(1)
}

export function CardStack({
  cards,
  resources,
  theme,
  version,
  call,
  message,
  expanded,
  onExpand,
}: {
  cards: ViewPayload[]
  resources: Record<string, ViewResource>
  theme: Theme
  version: string
  call: ViewHost['call']
  message: ViewHost['message']
  /** Full screen: the shell gives this pane the whole screen (and moves nothing,
   * because moving an iframe in the DOM reloads it). */
  expanded: boolean
  onExpand: (expanded: boolean) => void
}) {
  const shown = cards.filter((card) => resources[card.uri])
  const newest = shown.at(-1)?.call_id ?? null
  const [chosen, setChosen] = useState<string | null>(null)
  const [link, setLink] = useState<string | null>(null)

  // A new card comes to the top, as it would on the device.
  useEffect(() => {
    setChosen(null)
    onExpand(false)
  }, [newest, onExpand])

  if (shown.length === 0) return null
  const activeId = chosen && shown.some((card) => card.call_id === chosen) ? chosen : newest
  const behind = Math.min(2, shown.length - 1)

  return (
    <section aria-label="Cards" className={expanded ? 'flex h-full min-h-0 flex-col' : 'relative'}>
      {shown.length > 1 && (
        <div role="tablist" aria-label="Cards in this conversation" className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
          {shown.map((card) => (
            <button
              key={card.call_id}
              type="button"
              role="tab"
              aria-selected={card.call_id === activeId}
              onClick={() => setChosen(card.call_id)}
              className={`shrink-0 rounded-full border px-3 py-1 text-xs transition-colors ${
                card.call_id === activeId
                  ? 'border-transparent bg-[var(--fm-blue)] text-white'
                  : 'border-line text-muted hover:text-ink'
              }`}
            >
              {card.label ?? viewLabel(card.uri)}
            </button>
          ))}
        </div>
      )}

      <div className={expanded ? 'relative isolate min-h-0 flex-1' : 'relative isolate'}>
        {/* The edges of the cards underneath: a stack, not a list. */}
        {!expanded && Array.from({ length: behind }, (_, depth) => (
          <div
            key={depth}
            aria-hidden="true"
            className="absolute inset-x-0 top-0 h-full rounded-fm-lg border border-line bg-surface/70"
            style={{ transform: `translateY(${(depth + 1) * -7}px) scale(${1 - (depth + 1) * 0.035})`, zIndex: -1 - depth }}
          />
        ))}

        <AnimatePresence initial={false}>
          {shown.map((card) => {
            const active = card.call_id === activeId
            const resource = resources[card.uri]!
            const mode: DisplayMode = expanded && active ? 'fullscreen' : 'inline'
            const host: ViewHost = {
              call,
              message,
              link: setLink,
              displayMode: (next) => {
                setChosen(card.call_id)
                onExpand(next === 'fullscreen')
              },
            }
            return (
              <motion.div
                key={card.call_id}
                layout
                initial={{ opacity: 0, y: 24, scale: 0.97, rotateX: 8 }}
                animate={{ opacity: active ? 1 : 0, y: 0, scale: 1, rotateX: 0 }}
                exit={{ opacity: 0, y: -12, scale: 0.97 }}
                transition={{ duration: 0.42, ease: EASE }}
                aria-hidden={!active}
                className={
                  !active
                    ? 'pointer-events-none absolute inset-x-0 top-0'
                    : mode === 'fullscreen'
                      ? 'relative flex h-full flex-col'
                      : 'relative'
                }
                style={{ transformPerspective: 900 }}
              >
                <div className={mode === 'fullscreen' ? 'min-h-0 flex-1 overflow-auto' : ''}>
                  <ViewFrame
                    card={card}
                    resource={resource}
                    host={host}
                    theme={theme}
                    mode={mode}
                    version={version}
                    title={`${card.label ?? viewLabel(card.uri)} — from ${card.tool}`}
                    interactive={active}
                  />
                </div>
                {active && (
                  <div className="mt-1.5 flex items-center gap-2 px-1 text-[11px] text-faint">
                    {card.label && (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--fm-green)]/15 px-2 py-0.5 font-bold text-ink">
                        <span aria-hidden="true" className="size-1.5 rounded-full bg-[var(--fm-green-bright)]" />
                        {card.label}
                      </span>
                    )}
                    <span className="truncate font-mono">
                      {card.uri} · {card.tool}
                    </span>
                    {card.link && (
                      <a
                        href={card.link}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex shrink-0 items-center gap-1 text-accent hover:underline"
                      >
                        Open on {hostOf(card.link)} <ExternalIcon size={12} />
                      </a>
                    )}
                    <button
                      type="button"
                      onClick={() => onExpand(mode !== 'fullscreen')}
                      className="ml-auto inline-flex items-center gap-1 rounded-fm-sm px-1.5 py-1 text-muted hover:text-ink"
                      aria-label={mode === 'fullscreen' ? 'Back to the conversation' : 'Show this card full screen'}
                    >
                      {mode === 'fullscreen' ? <CloseIcon size={14} /> : <ExpandIcon size={14} />}
                    </button>
                  </div>
                )}
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>

      {/* `ui/open-link`: the host asks the person, it does not navigate for them. */}
      <AnimatePresence>
        {link && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-2 flex items-center gap-2 rounded-fm border border-line bg-raised px-3 py-2 text-sm"
          >
            <span className="min-w-0 flex-1 truncate text-muted">
              The card wants to open <span className="text-ink">{new URL(link).host}</span>
            </span>
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setLink(null)}
              className="inline-flex items-center gap-1 rounded-fm-sm bg-accent px-2.5 py-1 text-[var(--fm-accent-text)]"
            >
              Open <ExternalIcon size={14} />
            </a>
            <button type="button" onClick={() => setLink(null)} aria-label="Don't open it" className="text-faint hover:text-ink">
              <CloseIcon size={16} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}
