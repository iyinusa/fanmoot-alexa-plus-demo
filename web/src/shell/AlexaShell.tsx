/**
 * The Alexa+ shell (W2): the device, what is on its screen, and the voice
 * running through it — built on W1's session hook, which is the only thing
 * that talks to the server.
 *
 * What is simulated is the Alexa+ layer and nothing else: the look of the
 * device, the wake word, the voice. The agent, the MCP session, the OAuth
 * grant, the cards and the posts are real (§2.0 of the plan), and the trace
 * inspector beside the device shows every frame that proves it (W3).
 */
import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Captions } from '@/conversation/Captions'
import { Composer } from '@/conversation/Composer'
import { Turns } from '@/conversation/Turns'
import { useDemoSession } from '@/lib/socket'
import { completeSentences, useListener, useSpeaker } from '@/lib/speech'
import { useTheme } from '@/lib/theme'
import { Device } from '@/shell/Device'
import { Home } from '@/shell/Home'
import { AlertIcon, CloseIcon } from '@/shell/Icons'
import { LinkPrompt } from '@/shell/LinkPrompt'
import { StatusBar } from '@/shell/StatusBar'
import { type VoiceMode, Waveform } from '@/shell/Waveform'
import { TraceButton, TraceColumn, TraceSheet, useTracePanel } from '@/trace/TracePanel'
import { CardStack } from '@/views/CardStack'

const EASE = [0.22, 1, 0.36, 1] as const

export function AlexaShell() {
  const demo = useDemoSession()
  const panel = useTracePanel()
  const theme = useTheme()
  const speaker = useSpeaker()
  const [captions, setCaptions] = useState(true)
  const [micMuted, setMicMuted] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'error' } | null>(null)

  const last = demo.turns.at(-1)
  const running = last?.status === 'running'

  // ── Asking: typed, tapped, spoken, or from a card's `ui/message` ─────────
  const ask = useCallback(
    (text: string) => {
      speaker.stop() // barge-in: a fan who talks over Alexa is heard
      demo.ask(text)
    },
    [demo.ask, speaker.stop],
  )
  const listener = useListener(ask)

  const toggleListening = useCallback(() => {
    if (micMuted) return
    speaker.stop()
    listener.toggle()
  }, [micMuted, listener.toggle, speaker.stop])

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
      track.done = true
    }
  }, [last?.id, last?.reply, last?.status, speaker.say, speaker.stop])

  // A caption lingers a moment after the voice stops, then clears.
  useEffect(() => {
    if (speaker.speaking || !speaker.caption) return
    const timer = window.setTimeout(speaker.clearCaption, 2400)
    return () => window.clearTimeout(timer)
  }, [speaker.speaking, speaker.caption, speaker.clearCaption])

  // ── What the device is doing, for the light and the waveform ──────────────
  const mode: VoiceMode =
    listener.state === 'listening' ? 'listening' : speaker.speaking ? 'speaking' : running ? 'thinking' : 'idle'

  // ── Things worth a line on the screen ─────────────────────────────────────
  useEffect(() => {
    if (demo.error && demo.error.code !== 'busy') setNotice({ text: demo.error.message, tone: 'error' })
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

  const reset = () => {
    speaker.stop()
    setExpanded(false)
    demo.reset()
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
      <header className="mx-auto mb-4 flex w-full max-w-[1480px] items-center gap-3 sm:mb-6">
        <img src="/icons/icon-192.png" alt="" width={36} height={36} className="rounded-fm-sm" />
        <div className="min-w-0">
          <h1 className="text-lg leading-tight">
            Fanmoot <span className="font-light text-muted">on Alexa+</span>
          </h1>
          <p className="truncate text-xs text-muted">Where fans debate football — now by voice.</p>
        </div>
        {!panel.wide && (
          <span className="ml-auto">
            <TraceButton count={demo.trace.length} connected={trace.connected} onClick={() => panel.setSheet(true)} />
          </span>
        )}
      </header>
      <div
        className={`mx-auto grid w-full max-w-[1480px] gap-5 xl:items-start ${
          panel.column ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : 'xl:grid-cols-[minmax(0,1fr)_44px]'
        }`}
      >
        <div className="min-w-0 space-y-4">
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
                demoAvailable={demo.session?.demo_fan.available ?? false}
                signIn={demo.signIn}
                useDemo={demo.useDemoAccount}
                signOut={demo.signOut}
                showTicker={hasTurns}
              />

              <AnimatePresence>
                {notice && (
                  <motion.div
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
                  </motion.div>
                )}
              </AnimatePresence>

              <div className="relative min-h-0 flex-1">
                <AnimatePresence mode="wait" initial={false}>
                  {!hasTurns ? (
                    <motion.div
                      key="home"
                      className="absolute inset-0"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0, scale: 0.98 }}
                      transition={{ duration: 0.35, ease: EASE }}
                    >
                      <Home ambient={demo.ambient} canListen={listener.supported} />
                    </motion.div>
                  ) : (
                    <motion.div
                      key="conversation"
                      className={`absolute inset-0 overflow-y-auto @3xl:grid @3xl:gap-6 @3xl:overflow-hidden @3xl:px-5 ${
                        hasCards && !expanded ? '@3xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]' : '@3xl:grid-cols-1'
                      }`}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      {!expanded && (
                        <div
                          className={`px-4 pb-24 pt-2 @3xl:h-full @3xl:overflow-y-auto @3xl:px-1 ${hasCards ? '' : 'mx-auto w-full max-w-3xl'}`}
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
                        <div className={`px-4 pb-24 pt-2 @3xl:px-1 ${expanded ? 'h-full' : '@3xl:h-full @3xl:overflow-y-auto'}`}>
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
                        </div>
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Listening: what is being heard, big, over everything (W2-4). */}
                <AnimatePresence>
                  {listener.state === 'listening' && (
                    <motion.div
                      key="listening"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="absolute inset-0 z-20 flex flex-col items-center justify-end gap-5 bg-gradient-to-t from-[var(--fm-bg)] via-[var(--fm-bg)]/85 to-transparent px-6 pb-12"
                    >
                      <p className="max-w-2xl text-center text-2xl font-light leading-snug text-ink @3xl:text-4xl" aria-live="polite">
                        {listener.interim || <span className="text-muted">Listening…</span>}
                      </p>
                      <Waveform mode="listening" className="h-14 w-64 @3xl:w-80" />
                    </motion.div>
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
                      useDemo={demo.useDemoAccount}
                      dismiss={demo.dismiss}
                    />
                  )}
                </AnimatePresence>
              </div>
            </div>
          </Device>

          <div className="max-md:sticky max-md:bottom-0 max-md:z-40 max-md:-mx-4 max-md:border-t max-md:border-line max-md:bg-bg/90 max-md:px-4 max-md:pb-[max(0.75rem,env(safe-area-inset-bottom))] max-md:pt-3 max-md:backdrop-blur md:mx-auto md:max-w-4xl">
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
              showSuggestions={!running}
            />
          </div>

          <p className="mx-auto max-w-3xl px-2 text-center text-xs leading-relaxed text-faint">
            Alexa+ experience simulated — the Alexa+ Add-on toolkit is in preview and not available to hackathon
            participants. The agent, the Fanmoot MCP server, the sign-in and every post are real. Not made or endorsed by
            Amazon.
          </p>
        </div>

        {panel.wide && (
          <aside className="sticky top-6 h-[calc(100dvh-7rem)] min-h-[32rem] min-w-0">
            <TraceColumn data={trace} open={panel.column} setOpen={panel.setColumnOpen} />
          </aside>
        )}
      </div>
      {!panel.wide && <TraceSheet data={trace} open={panel.sheet} onClose={closeSheet} />}
    </>
  )
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
