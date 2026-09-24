/**
 * How the fan talks to the device (W2-4): the mic, and a typed field beside
 * it for every browser without speech recognition and every room where
 * talking out loud is not an option. Both end in the same `turn` frame.
 *
 * Under them, "Try saying…" — the example phrases `fanmoot-mcp`'s
 * `alexa/addon.json` carries (S8-1). Each one drives a real turn.
 */
import { motion } from 'framer-motion'
import { type FormEvent, useState } from 'react'

import { CaptionsIcon, MicIcon, MicOffIcon, SendIcon, SpeakerIcon, SpeakerOffIcon, StopIcon } from '@/shell/Icons'

export const SUGGESTIONS = [
  "What's happening with Arsenal today?",
  'What are fans saying about the derby?',
  'I want to tell them Saka was offside',
  'Predict Arsenal two, Chelsea one',
]

type Props = {
  running: boolean
  listening: boolean
  canListen: boolean
  micMuted: boolean
  interim: string
  ask: (text: string) => void
  cancel: () => void
  toggleListening: () => void
  captions: boolean
  setCaptions: (on: boolean) => void
  voiceOn: boolean
  canSpeak: boolean
  setVoiceOn: (on: boolean) => void
  showSuggestions: boolean
}

export function Composer(props: Props) {
  const [text, setText] = useState('')
  const { running, listening, canListen, micMuted } = props

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const question = text.trim()
    if (!question || running) return
    props.ask(question)
    setText('')
  }

  const micLabel = micMuted
    ? 'The microphone is switched off on the device'
    : !canListen
      ? 'Speech recognition is not available in this browser — type instead'
      : listening
        ? 'Stop listening'
        : 'Talk to Alexa'

  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="flex items-center gap-2">
        <motion.button
          type="button"
          onClick={props.toggleListening}
          disabled={!canListen || micMuted || running}
          aria-label={micLabel}
          title={micLabel}
          aria-pressed={listening}
          whileTap={{ scale: 0.92 }}
          className={`relative grid size-14 shrink-0 place-items-center rounded-full text-white shadow-lg transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
            micMuted ? 'bg-[var(--fm-red)]' : listening ? 'bg-[var(--fm-green)]' : 'bg-[var(--fm-blue)] hover:bg-[var(--fm-blue-deep)]'
          }`}
        >
          {listening && (
            <motion.span
              aria-hidden="true"
              className="absolute inset-0 rounded-full border-2 border-[var(--fm-green-bright)]"
              animate={{ scale: [1, 1.35], opacity: [0.8, 0] }}
              transition={{ duration: 1.2, repeat: Infinity, ease: 'easeOut' }}
            />
          )}
          {micMuted || !canListen ? <MicOffIcon size={24} /> : <MicIcon size={24} />}
        </motion.button>

        <label htmlFor="ask" className="sr-only">
          Or type to Alexa
        </label>
        <div className="relative min-w-0 flex-1">
          <input
            id="ask"
            value={listening ? props.interim : text}
            onChange={(event) => setText(event.target.value)}
            readOnly={listening}
            placeholder={listening ? 'Listening…' : 'Ask Alexa…'}
            maxLength={1000}
            autoComplete="off"
            enterKeyHint="send"
            className="fm-tap h-14 w-full rounded-full border border-line bg-surface/90 pl-5 pr-14 text-base text-ink shadow-sm backdrop-blur placeholder:text-faint focus:border-[var(--fm-accent)]"
          />
          {running ? (
            <button
              type="button"
              onClick={props.cancel}
              aria-label="Stop the answer"
              className="absolute right-2 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full border border-line text-ink hover:bg-raised"
            >
              <StopIcon size={18} />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!text.trim() || listening}
              aria-label="Send"
              className="absolute right-2 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-accent text-[var(--fm-accent-text)] transition-opacity disabled:opacity-30"
            >
              <SendIcon size={18} />
            </button>
          )}
        </div>

        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => props.setCaptions(!props.captions)}
            aria-pressed={props.captions}
            aria-label={props.captions ? 'Turn captions off' : 'Turn captions on'}
            title="Captions"
            className={`grid size-11 place-items-center rounded-full border transition-colors ${
              props.captions ? 'border-transparent bg-raised text-ink' : 'border-line text-faint'
            }`}
          >
            <CaptionsIcon size={20} />
          </button>
          <button
            type="button"
            onClick={() => props.setVoiceOn(!props.voiceOn)}
            disabled={!props.canSpeak}
            aria-pressed={props.voiceOn}
            aria-label={props.voiceOn ? "Mute Alexa's voice" : "Turn Alexa's voice on"}
            title={props.canSpeak ? 'Voice' : 'Speech is not available in this browser'}
            className={`grid size-11 place-items-center rounded-full border transition-colors disabled:opacity-40 ${
              props.voiceOn ? 'border-transparent bg-raised text-ink' : 'border-line text-faint'
            }`}
          >
            {props.voiceOn ? <SpeakerIcon size={20} /> : <SpeakerOffIcon size={20} />}
          </button>
        </div>
      </form>

      {props.showSuggestions && (
        // One row that scrolls sideways on a phone, so the suggestions never
        // cost the screen its height (W2-8).
        <div className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] md:mx-0 md:flex-wrap md:overflow-visible md:px-0 md:pb-0">
          <span className="shrink-0 text-xs text-faint">Try saying</span>
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              disabled={running}
              onClick={() => props.ask(suggestion)}
              className="shrink-0 rounded-full border border-line bg-surface/60 px-3 py-1.5 text-xs text-muted transition-colors hover:border-[var(--fm-accent)] hover:text-ink disabled:opacity-50"
            >
              “{suggestion}”
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
