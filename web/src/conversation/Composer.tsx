/**
 * How the fan talks to the device (W2-4): the mic, and a typed field beside
 * it for every browser without speech recognition and every room where
 * talking out loud is not an option. Both end in the same `turn` frame.
 *
 * Under them, the guided scenarios (W4, `conversation/Guide.tsx`), passed in
 * as `children` so the composer does not need to know how a tour runs.
 */
import { m } from 'framer-motion'
import { type FormEvent, type ReactNode, useState } from 'react'

import { CaptionsIcon, MicIcon, MicOffIcon, SendIcon, SpeakerIcon, SpeakerOffIcon, StopIcon } from '@/shell/Icons'

type Props = {
  running: boolean
  listening: boolean
  canListen: boolean
  micMuted: boolean
  interim: string
  /** False when it could not be sent (the socket is down): the words stay in the box. */
  ask: (text: string) => boolean
  cancel: () => void
  toggleListening: () => void
  captions: boolean
  setCaptions: (on: boolean) => void
  voiceOn: boolean
  canSpeak: boolean
  setVoiceOn: (on: boolean) => void
  children?: ReactNode
}

export function Composer(props: Props) {
  const [text, setText] = useState('')
  const { running, listening, canListen, micMuted } = props

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const question = text.trim()
    if (!question || running) return
    if (props.ask(question)) setText('')
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
        <m.button
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
            <m.span
              aria-hidden="true"
              className="absolute inset-0 rounded-full border-2 border-[var(--fm-green-bright)]"
              animate={{ scale: [1, 1.35], opacity: [0.8, 0] }}
              transition={{ duration: 1.2, repeat: Infinity, ease: 'easeOut' }}
            />
          )}
          {micMuted || !canListen ? <MicOffIcon size={24} /> : <MicIcon size={24} />}
        </m.button>

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
            aria-keyshortcuts="/"
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

      {props.children}
    </div>
  )
}
