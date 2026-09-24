/**
 * The guided scenarios under the composer (W4-1): five to choose from, and once
 * one is chosen, the next thing to say.
 *
 * Every line here is sent as the fan's own turn — the same `ask()` the mic and
 * the typed field use — so a judge who taps through the tour sees the agent,
 * the MCP calls and the cards do real work. The lines are written by the
 * server from what is on today (app/guided.py), which is why each chip can say
 * why it chose its match.
 */
import { AnimatePresence, m } from 'framer-motion'

import type { Tour } from '@/lib/guide'
import type { Scenario } from '@/lib/protocol'
import { ArrowIcon, CheckIcon, CloseIcon } from '@/shell/Icons'

type Props = {
  scenarios: Scenario[]
  /** False: the fixed example phrases, because what is on could not be read. */
  live: boolean
  tour: Tour | null
  running: boolean
  start: (scenario: Scenario) => void
  next: () => void
  leave: () => void
  signIn: (options?: { signup?: boolean }) => void
}

const EASE = [0.22, 1, 0.36, 1] as const

export function Guide(props: Props) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      {props.tour ? (
        <m.div
          key="tour"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.25, ease: EASE }}
        >
          <TourBar {...props} tour={props.tour} />
        </m.div>
      ) : (
        <m.div
          key="chips"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <Chips {...props} />
        </m.div>
      )}
    </AnimatePresence>
  )
}

function Chips({ scenarios, live, running, start }: Props) {
  if (scenarios.length === 0) return null
  return (
    // One row that scrolls sideways on a phone, so the chips never cost the
    // screen its height (W2-8).
    <div className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] md:mx-0 md:flex-wrap md:overflow-visible md:px-0 md:pb-0">
      <span
        className="shrink-0 text-xs text-faint"
        title={live ? 'Written from what is on Fanmoot right now' : 'Example phrases — what is on could not be read just now'}
      >
        Try a scenario
      </span>
      {scenarios.map((scenario) => (
        <button
          key={scenario.id}
          type="button"
          disabled={running}
          onClick={() => start(scenario)}
          title={[scenario.blurb, scenario.basis].filter(Boolean).join(' — ')}
          className="group flex max-w-[22rem] shrink-0 items-baseline gap-1.5 rounded-full border border-line bg-surface/60 px-3 py-1.5 text-xs transition-colors hover:border-[var(--fm-accent)] disabled:opacity-50"
        >
          <span className="shrink-0 font-bold text-ink">{scenario.title}</span>
          <span className="truncate text-muted group-hover:text-ink">“{scenario.steps[0]}”</span>
        </button>
      ))}
    </div>
  )
}

function TourBar({ tour, running, next, leave, signIn }: Props & { tour: Tour }) {
  const { scenario, phase } = tour
  const total = scenario.steps.length
  const step = Math.min(tour.said, total)

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-fm border border-line bg-surface/70 px-3 py-2 backdrop-blur"
    >
      {/* On a phone the action takes a row of its own, under the title and
          the close button, so a line to say is never cut to "W…" (W2-8). */}
      <div className="min-w-0 flex-1 md:flex-none">
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-accent">
          {scenario.title}
          {total > 1 && phase !== 'done' && (
            <span className="ml-2 font-normal normal-case tracking-normal text-faint">
              step {step} of {total}
            </span>
          )}
        </p>
        {scenario.basis && <p className="truncate text-[11px] text-faint">{scenario.basis}</p>}
      </div>

      <div className="order-3 flex min-w-0 basis-full flex-wrap items-center gap-2 md:order-2 md:flex-1 md:basis-auto">
        {phase === 'answering' && <span className="text-xs text-muted">Alexa is answering…</span>}

        {phase === 'ready' && tour.note && <span className="text-xs text-muted">{tour.note}</span>}

        {phase === 'ready' && tour.offer && (
          <m.button
            type="button"
            onClick={next}
            disabled={running}
            initial={{ scale: 0.96, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3, ease: EASE }}
            className="inline-flex min-w-0 items-center gap-2 rounded-full bg-[var(--fm-blue)] px-3.5 py-1.5 text-left text-xs text-white shadow-sm hover:bg-[var(--fm-blue-deep)] disabled:opacity-50"
          >
            <span className="shrink-0 text-white/70">Say</span>
            <span className="min-w-0 truncate font-bold">“{tour.offer}”</span>
            <ArrowIcon size={14} className="shrink-0" />
          </m.button>
        )}

        {phase === 'signin' && (
          <>
            <span className="text-xs text-muted">
              {scenario.signup ? 'Create your Fanmoot account to carry on.' : 'Sign in to Fanmoot to carry on.'}
            </span>
            {/* The popup has to open inside this click, or the browser blocks it. */}
            <button
              type="button"
              onClick={() => signIn({ signup: scenario.signup })}
              className="rounded-full bg-[var(--fm-blue)] px-3.5 py-1.5 text-xs font-bold text-white hover:bg-[var(--fm-blue-deep)]"
            >
              {scenario.signup ? 'Create an account' : 'Sign in'}
            </button>
          </>
        )}

        {phase === 'done' && (
          <>
            <span className="inline-flex items-center gap-1.5 text-xs text-ink">
              <CheckIcon size={14} className="text-[var(--fm-green-bright)]" />
              {doneLine(scenario)}
            </span>
            <button type="button" onClick={leave} className="text-xs text-accent underline-offset-2 hover:underline">
              Try another
            </button>
          </>
        )}
      </div>

      <button
        type="button"
        onClick={leave}
        aria-label="Leave the tour"
        title="Leave the tour"
        className="order-2 text-faint hover:text-ink md:order-3"
      >
        <CloseIcon size={16} />
      </button>
    </div>
  )
}

function doneLine(scenario: Scenario): string {
  if (scenario.goal === 'post_moot') return 'Sent to Fanmoot for real.'
  if (scenario.goal === 'predict_match') return 'Called, on a real Fanmoot account.'
  return 'That’s the scenario.'
}
