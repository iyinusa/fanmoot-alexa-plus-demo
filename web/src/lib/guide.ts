/**
 * The guided tour (W4-1): one scenario at a time, one line at a time.
 *
 * The server writes the lines (app/guided.py); this decides which to offer
 * next, from what the last answer actually did. Every line is sent with the
 * same `ask()` as anything typed or spoken, so each step is a real turn over
 * the real MCP session. Nothing here answers anything.
 *
 * After a step's turn ends, the tour looks at it:
 *
 * - the scenario's goal tool (`post_moot`, `predict_match`) succeeded → done;
 * - the goal needed an account (`auth_required`) → wait for a sign-in, then
 *   offer the scenario's `retry` line;
 * - the goal was refused for a reason the fan can fix — Fanmoot's "confirm
 *   your email address" for a brand new account — → offer `retry_refused`;
 * - the turn failed or was stopped → offer the same line again;
 * - otherwise → the next line. With none left, a scenario without a goal is
 *   done — but one with a goal is **never** done until the goal succeeded.
 *   The agent may have answered without calling the tool at all, and the
 *   tour saying "sent" then would be a claim nothing made (HAD-7).
 *
 * So the tour follows the agent rather than a script. An agent that posts
 * without asking first finishes "Have your say" a step early, and that is
 * fine.
 */
import { useCallback, useEffect, useState } from 'react'

import type { AuthState, Scenario } from '@/lib/protocol'
import type { Turn } from '@/lib/socket'

export type TourPhase =
  /** A step has been said; its answer is on its way. */
  | 'answering'
  /** The next line is on offer. */
  | 'ready'
  /** The goal needs an account; waiting for the fan to sign in. */
  | 'signin'
  | 'done'

export type Tour = {
  scenario: Scenario
  phase: TourPhase
  /** The next line on offer, in `ready`. */
  offer: string | null
  /** Which of the scenario's steps have been said. */
  said: number
  /** The index in the conversation of the turn this step is waiting for. */
  turnIndex: number
  /** The line that turn was asked with, to offer again if it never started. */
  line: string
  /** The refusal already on screen when it was asked — not an answer to it. */
  staleError?: unknown
  /** Said beside the offer when the lines ran out short of the goal. */
  note?: string
  /** Who the fan was when the goal asked for a sign-in. A 403 for a missing
   * permission comes to a fan who *is* signed in, so what the tour waits for
   * is a change of sign-in, not merely a sign-in. */
  signedInAs?: string
}

function identity(auth: AuthState | null): string {
  return auth ? `${auth.mode}|${auth.handle ?? ''}|${auth.scopes.join(' ')}` : 'none'
}

type Options = {
  scenarios: Scenario[]
  turns: Turn[]
  auth: AuthState | null
  /** The server's last refusal, if any: a turn it would not start. */
  error: { code: string; message: string } | null
  ask: (text: string) => void
  signOut: () => void
}

/** Refusals that mean the turn never began, so no answer is coming. */
const NEVER_STARTED = new Set(['busy', 'turn_cap', 'bad_turn', 'capacity', 'session_expired'])

export function useGuide({ scenarios, turns, auth, error, ask, signOut }: Options) {
  const [tour, setTour] = useState<Tour | null>(null)
  const signedIn = auth !== null && auth.mode !== 'none'
  const who = identity(auth)

  const say = useCallback(
    (scenario: Scenario, line: string, said: number) => {
      setTour({ scenario, phase: 'answering', offer: null, said, turnIndex: turns.length, line, staleError: error })
      ask(line)
    },
    [ask, turns.length, error],
  )

  const start = useCallback(
    (scenario: Scenario) => {
      const first = scenario.steps[0]
      if (!first) return
      // "New here" is a new fan: whoever is signed in steps aside first. The
      // server handles `auth.sign_out` before the turn that follows it.
      if (scenario.signup && signedIn) signOut()
      say(scenario, first, 1)
    },
    [say, signOut, signedIn],
  )

  /** Say the line on offer. */
  const next = useCallback(() => {
    if (!tour?.offer) return
    const isStep = tour.scenario.steps[tour.said] === tour.offer
    say(tour.scenario, tour.offer, isStep ? tour.said + 1 : tour.said)
  }, [say, tour])

  const leave = useCallback(() => setTour(null), [])

  // A step's answer has finished: decide what to offer.
  const turn = tour?.phase === 'answering' ? turns[tour.turnIndex] : undefined
  useEffect(() => {
    if (!tour || tour.phase !== 'answering' || !turn || turn.status === 'running') return
    const decided = decide(tour, turn)
    setTour(decided.phase === 'signin' ? { ...decided, signedInAs: who } : decided)
  }, [tour, turn, who])

  // The server would not start the turn — the session's allowance is spent,
  // or it was still answering — so no answer is coming. Say why, and offer
  // the same line again rather than waiting on nothing.
  useEffect(() => {
    if (tour?.phase !== 'answering' || !error || error === tour.staleError || !NEVER_STARTED.has(error.code)) return
    if (turns.length > tour.turnIndex) return
    setTour({ ...tour, phase: 'ready', offer: tour.line, note: error.message })
  }, [tour, error, turns.length])

  // Signed in — or in again, with the permission — while the tour waited:
  // the retry line is on offer.
  useEffect(() => {
    if (tour?.phase === 'signin' && signedIn && who !== tour.signedInAs) {
      setTour({ ...tour, phase: 'ready', offer: tour.scenario.retry ?? tour.scenario.steps[tour.said - 1] ?? null })
    }
  }, [tour, signedIn, who])

  // The chips were rewritten (a reset, or a fresh reading): a tour on a
  // scenario that no longer exists ends rather than offering stale lines.
  useEffect(() => {
    if (tour && tour.phase !== 'answering' && !scenarios.some((s) => s.id === tour.scenario.id)) setTour(null)
  }, [scenarios, tour])

  return { tour, start, next, leave }
}

/** What to offer after a step's turn. */
export function decide(tour: Tour, turn: Turn): Tour {
  const { scenario } = tour
  const repeat = scenario.steps[tour.said - 1] ?? null

  if (turn.status !== 'ok') {
    // Stopped, or it went wrong: the same line again, one tap away.
    return { ...tour, phase: 'ready', offer: repeat }
  }

  const goal = scenario.goal ? turn.tools.filter((call) => call.name === scenario.goal) : []
  if (goal.some((call) => call.status === 'ok')) return { ...tour, phase: 'done', offer: null }

  const refused = goal.at(-1)
  if (refused?.status === 'error') {
    if (refused.reason === 'auth_required') return { ...tour, phase: 'signin', offer: null }
    if (refused.reason !== 'demo_limit') {
      // Refused for a reason the fan can fix and then ask again.
      return { ...tour, phase: 'ready', offer: scenario.retry_refused ?? repeat }
    }
    // The demo account's allowance is spent: nothing to retry into.
    return { ...tour, phase: 'done', offer: null }
  }

  const following = scenario.steps[tour.said] ?? null
  if (following) return { ...tour, phase: 'ready', offer: following }
  if (scenario.goal) {
    // The agent answered without trying: the confirming line again, not the
    // after-sign-in one, because nobody has signed in.
    const nothing = scenario.goal === 'predict_match' ? 'No prediction has gone in yet.' : 'Nothing has gone out yet.'
    return { ...tour, phase: 'ready', offer: repeat, note: nothing }
  }
  return { ...tour, phase: 'done', offer: null }
}
