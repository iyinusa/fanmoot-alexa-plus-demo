/**
 * The Alexa+ shell (W2): the device, what is on its screen, and the voice
 * running through it — built on W1's session hook, which is the only thing
 * that talks to the server.
 *
 * What is simulated is the Alexa+ layer and nothing else: the look of the
 * device, the wake word, the voice. The agent, the MCP session, the OAuth
 * grant, the cards and the posts are real (§2.0 of the plan), and the trace
 * inspector beside the device shows every frame that proves it (W3).
 *
 * W5 is the polish around it: a landing screen for a judge who arrives with
 * no context (`Intro`), the device's own screens for being offline or out of
 * date (`Fault`), apologies in Alexa's voice rather than error banners, an
 * entrance, keyboard access to everything, a screen-reader announcement of
 * each finished answer, and the cards and trace loaded off the first paint.
 */
import { AnimatePresence, m } from 'framer-motion'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Captions } from '@/conversation/Captions'
import { Composer } from '@/conversation/Composer'
import { Guide } from '@/conversation/Guide'
import { Turns } from '@/conversation/Turns'
import { noticeFor, turnApology } from '@/lib/faults'
import { useGuide } from '@/lib/guide'
import { useDemoSession } from '@/lib/socket'
import { completeSentences, useListener, useSpeaker } from '@/lib/speech'
import { useTheme } from '@/lib/theme'
import { Device } from '@/shell/Device'
import { Fault, useFault, useOnline } from '@/shell/Fault'
import { Home } from '@/shell/Home'
import { AlertIcon, CloseIcon, InfoIcon, ResetIcon } from '@/shell/Icons'
import { Intro, useIntro } from '@/shell/Intro'
import { LinkPrompt } from '@/shell/LinkPrompt'
import { StatusBar } from '@/shell/StatusBar'
import { type VoiceMode, Waveform } from '@/shell/Waveform'
import { prefetchTrace, TraceButton, TraceColumn, TraceSheet, useTracePanel } from '@/trace/TracePanel'

const EASE = [0.22, 1, 0.36, 1] as const

/** Who made it, in the footer. */
const CREDITS = [
  { label: 'LinkedIn', href: 'https://linkedin.com/in/iyinusa' },
  { label: 'X', href: 'https://x.com/ikyinusa' },
  { label: 'YouTube', href: 'https://youtube.com/@iyinusa' },
]

/** The cards, and the view host behind them, off the critical path (W5-3). */
const loadCards = () => import('@/views/CardStack')
const CardStack = lazy(() => loadCards().then((mod) => ({ default: mod.CardStack })))

/** Nothing in the page is being typed into: "/" is free to mean "focus the question". */
function typingSomewhere(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
}

export function AlexaShell() {
  const demo = useDemoSession()
  const panel = useTracePanel()
  const theme = useTheme()
  // Alexa's voice and ears are Amazon Polly and Amazon Transcribe when the
  // session has them (app/voice), and the browser's own otherwise.
  const speakWithPolly = demo.session?.voice?.speak ?? false
  const listenWithTranscribe = demo.session?.voice?.listen ?? false
  const voice = useMemo(() => ({ available: speakWithPolly, fetch: demo.speech }), [speakWithPolly, demo.speech])
  const ears = useMemo(() => ({ available: listenWithTranscribe, ...demo.ears }), [listenWithTranscribe, demo.ears])
  const speaker = useSpeaker(voice)
  const [captions, setCaptions] = useState(true)
  const [micMuted, setMicMuted] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'error' } | null>(null)

  const last = demo.turns.at(-1)
  const running = last?.status === 'running'
  const online = useOnline()
  const fault = useFault({ status: demo.status, fault: demo.fault, online })
  const intro = useIntro(demo.turns.length > 0)

  // ── Asking: typed, tapped, spoken, or from a card's `ui/message` ─────────
  // False when there was no connection to send it on (W5-4): the caller keeps
  // the words, and Alexa says she will be back.
  const ask = useCallback(
    (text: string): boolean => {
      speaker.stop() // barge-in: a fan who talks over Alexa is heard
      const sent = demo.ask(text)
      if (!sent) setNotice({ text: noticeFor('offline_ask', ''), tone: 'error' })
      return sent
    },
    [demo.ask, speaker.stop],
  )
  const listener = useListener(ask, ears)
  const scenarios = demo.scenarios?.list ?? []
  const guide = useGuide({
    scenarios,
    turns: demo.turns,
    auth: demo.auth,
    error: demo.error,
    ask,
    signOut: demo.signOut,
  })

  const toggleListening = useCallback(() => {
    if (micMuted) return
    speaker.stop()
    intro.close()
    listener.toggle()
  }, [micMuted, listener.toggle, speaker.stop, intro.close])

  // Asking anything, by any route, is past the introduction.
  const turnCount = demo.turns.length
  useEffect(() => {
    if (turnCount > 0) intro.close()
  }, [turnCount, intro.close])

  useEffect(() => {
    if (micMuted) listener.stop()
  }, [micMuted, listener.stop])

  // ── Speaking the answer as it streams (W2-5) ──────────────────────────────
  const spoken = useRef<{ id: string; next: number; done: boolean } | null>(null)
  useEffect(() => {
    if (!last) {
      spoken.current = null
      return
    }
    if (spoken.current?.id !== last.id) {
      speaker.stop()
      spoken.current = { id: last.id, next: 0, done: false }
    }
    const track = spoken.current
    if (track.done) return
    const { sentences, next } = completeSentences(last.reply, track.next)
    sentences.forEach(speaker.say)
    track.next = next
    if (last.status !== 'running') {
      if (last.status === 'ok') speaker.say(last.reply.slice(track.next).trim())
      // A failed turn is apologised for out loud, as the device would (W5-4).
      if (last.status === 'error' && last.error) speaker.say(turnApology(last.errorCode))
      track.done = true
    }
  }, [last?.id, last?.reply, last?.status, last?.error, last?.errorCode, speaker.say, speaker.stop])

  // ── What a screen reader hears (W5-5) ─────────────────────────────────────
  // Each answer once, when it is finished — not every word as it streams.
  const [announcement, setAnnouncement] = useState('')
  useEffect(() => {
    if (!last || last.status === 'running') return
    const said = last.status === 'error' && last.error ? turnApology(last.errorCode) : last.reply
    if (said) setAnnouncement(`Alexa: ${said}`)
  }, [last?.id, last?.status, last?.reply, last?.error, last?.errorCode])

  // A caption lingers a moment after the voice stops, then clears.
  useEffect(() => {
    if (speaker.speaking || !speaker.caption) return
    const timer = window.setTimeout(speaker.clearCaption, 2400)
    return () => window.clearTimeout(timer)
  }, [speaker.speaking, speaker.caption, speaker.clearCaption])

  // ── What the device is doing, for the light and the waveform ──────────────
  const mode: VoiceMode =
    fault === 'offline' || fault === 'disconnected' || fault === 'capacity'
      ? 'connecting'
      : listener.state === 'listening'
        ? 'listening'
        : speaker.speaking
          ? 'speaking'
          : running
            ? 'thinking'
            : 'idle'

  // ── Things worth a line on the screen ─────────────────────────────────────
  // A failed turn is not a notice: Alexa answers it with an apology in the
  // conversation, and says it (W5-4).
  useEffect(() => {
    const error = demo.error as { code: string; message: string; turn_id?: string } | null
    if (error && error.code !== 'busy' && !error.turn_id) setNotice({ text: noticeFor(error.code, error.message), tone: 'error' })
  }, [demo.error])
  useEffect(() => {
    if (listener.error) setNotice({ text: listener.error, tone: 'error' })
  }, [listener.error])
  useEffect(() => {
    if (!demo.authReason) return
    const who = demo.auth?.handle ? ` as @${demo.auth.handle}` : ''
    setNotice({ text: demo.auth?.mode === 'none' ? demo.authReason : `${capitalise(demo.authReason)}${who}.`, tone: 'info' })
  }, [demo.authReason, demo.auth?.handle, demo.auth?.mode])
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 6000)
    return () => window.clearTimeout(timer)
  }, [notice])

  // ── A card's calls, through the host bridge (W2-6) ────────────────────────
  const message = useCallback(
    (text: string) => {
      if (running) return false
      ask(text)
      return true
    },
    [running, ask],
  )

  // ── The keyboard (W5-5) ───────────────────────────────────────────────────
  // "/" goes to the question, from anywhere that is not already a text field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey || typingSomewhere(event.target)) return
      event.preventDefault()
      document.getElementById('ask')?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── Off the critical path, but ready before they are wanted (W5-3) ─────────
  useEffect(() => {
    prefetchTrace()
    const idle = window.requestIdleCallback ?? ((run: () => void) => window.setTimeout(run, 1500))
    idle(() => void loadCards())
  }, [])

  // ── The landing screen's way in (W5-1) ────────────────────────────────────
  const startTour = useCallback(
    (scenario: (typeof scenarios)[number]) => {
      intro.close()
      guide.start(scenario)
    },
    [intro.close, guide.start],
  )

  // ── Start over (W4-7) ─────────────────────────────────────────────────────
  // One tap from anywhere, and judges will press it at the worst moment. The
  // server stops the turn and anything still due to land on the screen before
  // it says `session.reset`; this side silences Alexa, closes the mic, and
  // drops the tour, the notices and any full-screen card. The sign-in is kept,
  // and said so, so nobody wonders whether starting over signed them out.
  const reset = () => {
    speaker.stop()
    listener.stop()
    speaker.clearCaption()
    setExpanded(false)
    guide.leave()
    demo.reset()
    const who = demo.auth?.mode === 'none' ? null : demo.auth?.handle
    setNotice({ text: who ? `Started over. Still signed in as @${who}.` : 'Started over.', tone: 'info' })
  }

  const hasTurns = demo.turns.length > 0
  const hasCards = demo.cards.some((card) => demo.views[card.uri])
  const version = demo.hello?.version ?? 'dev'
  const showPrompt = demo.authRequired !== null && (demo.auth?.mode === 'none' || demo.authRequired.status === 403)
  const closeSheet = useCallback(() => panel.setSheet(false), [panel.setSheet])
  const trace = {
    trace: demo.trace,
    facts: demo.facts,
    auth: demo.auth,
    connected: demo.status === 'open' && (demo.facts?.connected ?? false),
  }

  return (
    <>
      <a
        href="#ask"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-full focus:bg-[var(--fm-blue)] focus:px-4 focus:py-2 focus:text-sm focus:font-bold focus:text-white"
      >
        Skip to the question box
      </a>
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
      <m.header
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: EASE }}
        className="mx-auto mb-4 flex w-full max-w-[1480px] items-center gap-3 sm:mb-6"
      >
        <img src="/icons/icon-192.png" alt="" width={36} height={36} className="rounded-fm-sm" />
        {/* On a phone the lockup stacks, and the strapline gives way to the
            header's buttons rather than being cut to "Where fans d…". */}
        <div className="min-w-0">
          <h1 className="text-lg leading-tight">
            Fanmoot <span className="font-light text-muted max-sm:block max-sm:text-sm">on Alexa+</span>
          </h1>
          <p className="truncate text-xs text-muted max-sm:hidden">Where fans debate football — now by voice.</p>
        </div>
        <span className="ml-auto flex items-center gap-2">
          {demo.presence !== null && <Presence online={demo.presence} />}
          <button
            type="button"
            onClick={intro.show}
            aria-label="What is this demo?"
            title="What is this demo?"
            className="grid size-9 place-items-center rounded-full border border-line text-muted transition-colors hover:border-[var(--fm-accent)] hover:text-ink"
          >
            <InfoIcon size={17} />
          </button>
          <button
            type="button"
            onClick={reset}
            title="Start over: a fresh conversation, the sign-in kept"
            className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-xs text-muted transition-colors hover:border-[var(--fm-accent)] hover:text-ink"
          >
            <ResetIcon size={15} />
            <span className="max-sm:sr-only">Start over</span>
          </button>
          {!panel.wide && (
            <TraceButton count={demo.trace.length} connected={trace.connected} onClick={() => panel.setSheet(true)} />
          )}
        </span>
      </m.header>
      <div
        className={`mx-auto grid w-full max-w-[1480px] gap-5 xl:items-start ${
          panel.column ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : 'xl:grid-cols-[minmax(0,1fr)_44px]'
        }`}
      >
        {/* Transform only, no fade: the landing headline inside is the page's
            largest paint, and an element fading in from nothing is painted
            late (W5-3). */}
        <m.div
          initial={{ y: 18, scale: 0.99 }}
          animate={{ y: 0, scale: 1 }}
          transition={{ duration: 0.7, ease: EASE }}
          className="min-w-0 space-y-4"
        >
          <Device
            mode={mode}
            volume={speaker.volume}
            nudgeVolume={speaker.nudgeVolume}
            micMuted={micMuted}
            setMicMuted={setMicMuted}
          >
            <div className="relative flex h-full flex-col">
              <StatusBar
                status={demo.status}
                auth={demo.auth}
                ambient={demo.ambient}
                signIn={demo.signIn}
                signOut={demo.signOut}
                showTicker={hasTurns}
              />

              <Fault kind={fault} retryAt={demo.retryAt} reconnect={demo.reconnect} />

              <AnimatePresence>
                {intro.open && !fault && (
                  <Intro
                    facts={demo.facts}
                    connected={trace.connected}
                    wide={panel.wide}
                    scenarios={scenarios}
                    canListen={listener.supported}
                    recalled={intro.recalled}
                    onTour={startTour}
                    onClose={intro.close}
                  />
                )}
              </AnimatePresence>

              <AnimatePresence>
                {notice && (
                  <m.div
                    role={notice.tone === 'error' ? 'alert' : 'status'}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    className="absolute inset-x-0 top-12 z-30 mx-auto flex w-fit max-w-[90%] items-center gap-2 rounded-full border border-line bg-raised/95 px-4 py-2 text-sm shadow-[var(--fm-shadow)] backdrop-blur"
                  >
                    {notice.tone === 'error' && <AlertIcon size={16} className="shrink-0 text-[var(--fm-amber)]" />}
                    <span>{notice.text}</span>
                    <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="text-faint hover:text-ink">
                      <CloseIcon size={14} />
                    </button>
                  </m.div>
                )}
              </AnimatePresence>

              <div className="relative min-h-0 flex-1">
                <AnimatePresence mode="wait" initial={false}>
                  {!hasTurns ? (
                    <m.div
                      key="home"
                      className="absolute inset-0"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0, scale: 0.98 }}
                      transition={{ duration: 0.35, ease: EASE }}
                    >
                      <Home
                        ambient={demo.ambient}
                        canListen={listener.supported}
                        lines={scenarios.map((scenario) => scenario.steps[0] ?? '').filter(Boolean)}
                      />
                    </m.div>
                  ) : (
                    <m.div
                      key="conversation"
                      className={`fm-screen-scroll absolute inset-0 overflow-y-auto @3xl:grid @3xl:gap-6 @3xl:overflow-hidden @3xl:px-5 ${
                        hasCards && !expanded ? '@3xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]' : '@3xl:grid-cols-1'
                      }`}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      {!expanded && (
                        <div
                          className={`fm-screen-scroll px-4 pb-24 pt-2 @3xl:h-full @3xl:overflow-y-auto @3xl:px-1 ${hasCards ? '' : 'mx-auto w-full max-w-3xl'}`}
                        >
                          <Turns turns={demo.turns} />
                          <button
                            type="button"
                            onClick={reset}
                            className="mt-6 text-xs text-faint underline-offset-2 hover:text-ink hover:underline"
                          >
                            Start over
                          </button>
                        </div>
                      )}
                      {hasCards && (
                        <div className={`fm-screen-scroll px-4 pb-24 pt-2 @3xl:px-1 ${expanded ? 'h-full' : '@3xl:h-full @3xl:overflow-y-auto'}`}>
                          <Suspense fallback={<div className="h-64 animate-pulse rounded-fm-lg border border-line bg-surface/60" />}>
                          <CardStack
                            cards={demo.cards}
                            resources={demo.views}
                            theme={theme}
                            version={version}
                            call={demo.callFromView}
                            message={message}
                            expanded={expanded}
                            onExpand={setExpanded}
                          />
                          </Suspense>
                        </div>
                      )}
                    </m.div>
                  )}
                </AnimatePresence>

                {/* Listening: what is being heard, big, over everything (W2-4). */}
                <AnimatePresence>
                  {listener.state === 'listening' && (
                    <m.div
                      key="listening"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="absolute inset-0 z-20 flex flex-col items-center justify-end gap-5 bg-gradient-to-t from-[var(--fm-bg)] via-[var(--fm-bg)]/85 to-transparent px-6 pb-12"
                    >
                      <p className="max-w-2xl text-center text-2xl font-light leading-snug text-ink @3xl:text-4xl" aria-live="polite">
                        {listener.asking ? (
                          <span className="text-muted">Allow the microphone when your browser asks…</span>
                        ) : (
                          listener.interim || <span className="text-muted">Listening…</span>
                        )}
                      </p>
                      <Waveform mode="listening" className="h-14 w-64 @3xl:w-80" />
                    </m.div>
                  )}
                </AnimatePresence>

                {/* Speaking: the captions track, with the voice drawn in it (W2-2, W2-5). */}
                <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 px-4">
                  {listener.state !== 'listening' && !showPrompt && (
                    <Captions caption={speaker.caption} on={captions} speaking={mode === 'speaking'} />
                  )}
                </div>

                <AnimatePresence>
                  {showPrompt && demo.authRequired && (
                    <LinkPrompt
                      required={demo.authRequired}
                      auth={demo.auth}
                      signIn={demo.signIn}
                      dismiss={demo.dismiss}
                      signup={guide.tour?.scenario.signup ?? false}
                    />
                  )}
                </AnimatePresence>
              </div>
            </div>
          </Device>

          <m.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: EASE, delay: 0.15 }}
            className="max-md:sticky max-md:bottom-0 max-md:z-40 max-md:-mx-4 max-md:border-t max-md:border-line max-md:bg-bg/90 max-md:px-4 max-md:pb-[max(0.75rem,env(safe-area-inset-bottom))] max-md:pt-3 max-md:backdrop-blur md:mx-auto md:max-w-4xl">
            <Composer
              running={running}
              listening={listener.state === 'listening'}
              canListen={listener.supported}
              micMuted={micMuted}
              interim={listener.interim}
              ask={ask}
              cancel={demo.cancel}
              toggleListening={toggleListening}
              captions={captions}
              setCaptions={setCaptions}
              voiceOn={!speaker.muted}
              canSpeak={speaker.supported}
              setVoiceOn={(on) => speaker.setMuted(!on)}
            >
              <Guide
                scenarios={scenarios}
                live={demo.scenarios?.live ?? false}
                tour={guide.tour}
                running={running}
                start={guide.start}
                next={guide.next}
                leave={guide.leave}
                signIn={demo.signIn}
              />
            </Composer>
          </m.div>

          {/* text-muted, not faint: at this size faint misses WCAG AA on the dark ground (W5-5). */}
          <footer className="mx-auto max-w-3xl space-y-1.5 px-2 text-center text-xs leading-relaxed text-muted">
            <p>
              Alexa+ experience simulated — the Alexa+ Add-on toolkit is in preview. The agent, the Fanmoot MCP server,
              the sign-in and every post are real. Not made or endorsed by Amazon.
            </p>
            <p>
              Designed by <span className="text-ink">Kennedy Yinusa</span>
              {CREDITS.map((credit) => (
                <span key={credit.href}>
                  <span aria-hidden="true"> · </span>
                  <a
                    href={credit.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Kennedy Yinusa on ${credit.label}`}
                    className="text-accent underline-offset-2 hover:underline"
                  >
                    {credit.label}
                  </a>
                </span>
              ))}
            </p>
          </footer>
        </m.div>

        {panel.wide && (
          <m.aside
            aria-label="MCP trace inspector"
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.2 }}
            className="sticky top-6 h-[calc(100dvh-7rem)] min-h-[32rem] min-w-0"
          >
            <TraceColumn data={trace} open={panel.column} setOpen={panel.setColumnOpen} spotlight={intro.open && !fault} />
          </m.aside>
        )}
      </div>
      {!panel.wide && <TraceSheet data={trace} open={panel.sheet} onClose={closeSheet} />}
    </>
  )
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** How many people have the demo open right now, beside the header's buttons. */
function Presence({ online }: { online: number }) {
  const label = `${online} ${online === 1 ? 'person' : 'people'} using the demo right now`
  return (
    <span
      title={label}
      className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-xs text-muted"
    >
      <span className="sr-only">{label}</span>
      <span className="relative flex size-2" aria-hidden="true">
        <span className="absolute inset-0 animate-ping rounded-full bg-live opacity-60" />
        <span className="relative size-2 rounded-full bg-live" />
      </span>
      <span className="font-bold tabular-nums text-ink" aria-hidden="true">
        {online}
      </span>
    </span>
  )
}
