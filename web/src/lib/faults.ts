/**
 * What goes wrong, said the way the device would say it (W5-4).
 *
 * The server's error frames carry a `code` and a plain `message` written for
 * a developer (app/ws.py, app/agent/runtime.py). A person talking to a smart
 * display should hear Alexa apologise and say what happens next, never a
 * status code or a stack trace. So every code the server sends has a line
 * here, in Alexa's voice, and the code itself stays on the screen only in
 * small print, for the judge who wants to know.
 *
 * Two kinds:
 *
 * - **A turn that failed.** The line becomes Alexa's answer: shown in the
 *   conversation and spoken aloud, like any other reply.
 * - **Something about the session.** Either a notice that comes and goes, or,
 *   for the ones that stop the demo (offline, disconnected, updated, timed
 *   out, full), a screen of its own on the device (`shell/Fault.tsx`).
 */

/** What Alexa says when a turn could not be answered. */
const TURN: Record<string, string> = {
  no_model: "Sorry, I couldn't come up with an answer just now. Give me a moment and ask again.",
  mcp_unavailable: "Sorry, I can't reach Fanmoot right now. Try me again in a moment.",
  turn_failed: 'Sorry, something went wrong there. Could you ask me that again?',
}

export function turnApology(code: string | undefined): string {
  return (code && TURN[code]) ?? TURN.turn_failed!
}

/** What a passing notice says. Anything not listed falls back to the server's own words. */
const NOTICE: Record<string, string> = {
  busy: "Hang on, I'm still answering the last one.",
  bad_turn: "Sorry, I didn't catch that. Try saying it another way.",
  turn_cap: "That's all the questions I can take in one visit. Thanks for trying me out!",
  resume_failed: 'That conversation had timed out, so we’re starting a fresh one.',
  demo_unavailable: "Sorry, the demo account isn't available just now. You can sign in with your own.",
  bad_frame: 'Sorry, something got garbled on the way. Try that again.',
  unknown_type: 'Sorry, something got garbled on the way. Try that again.',
  offline_ask: "I'm not connected just now. I'll be back in a moment, and your words are still in the box.",
}

export function noticeFor(code: string, message: string): string {
  return NOTICE[code] ?? message
}

/** The errors that take over the screen, rather than passing as a notice. */
export type FaultKind = 'offline' | 'disconnected' | 'capacity' | 'stale' | 'expired'

export const SCREEN_CODES: Record<string, FaultKind> = {
  capacity: 'capacity',
  stale_page: 'stale',
  session_expired: 'expired',
}

export const FAULT_COPY: Record<FaultKind, { title: string; body: string; action: 'retry' | 'reload' | null }> = {
  offline: {
    title: "You're offline.",
    body: "I'll reconnect as soon as you're back. Your conversation and your sign-in are kept for a few minutes.",
    action: null,
  },
  disconnected: {
    title: "I'm having trouble connecting.",
    body: "I'll keep trying on my own. Your conversation and your sign-in are kept for a few minutes.",
    action: 'retry',
  },
  capacity: {
    title: "I'm a bit busy right now.",
    body: "Lots of people are trying the demo at once. I'll make room for you in a minute.",
    action: 'retry',
  },
  stale: {
    title: "I've just been updated.",
    body: 'Reload to get the new version. Your conversation comes with you.',
    action: 'reload',
  },
  expired: {
    title: 'That conversation has timed out.',
    body: 'Reload to start a fresh one. Anything you posted is still on Fanmoot.',
    action: 'reload',
  },
}
