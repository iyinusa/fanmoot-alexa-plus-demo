/**
 * A JSON-RPC payload, pretty-printed and coloured (W3-3).
 *
 * A tokenizer over `JSON.stringify(value, null, 2)` rather than a tree of
 * components: a `tools/list` answer is a few thousand tokens, and a flat run
 * of spans in one `<pre>` renders it in a frame. Redacted values — struck out
 * on the server before the frame was sent — are drawn as a mark of their own,
 * so the redaction is visible rather than looking like a strange string.
 */
import { memo, type ReactNode } from 'react'

const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

export const Json = memo(function Json({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2) ?? 'null'
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0
    if (at > last) parts.push(text.slice(last, at))
    const [whole, string, colon, literal, number] = match
    if (string && colon) {
      parts.push(
        <span key={at} className="text-accent">
          {string}
        </span>,
        colon,
      )
    } else if (string === '"[redacted]"') {
      parts.push(
        <span key={at} className="rounded bg-[var(--fm-amber)]/15 px-1 text-code-number" title="Redacted on the server">
          redacted
        </span>,
      )
    } else if (string) {
      parts.push(
        <span key={at} className="text-code-string">
          {string}
        </span>,
      )
    } else if (literal) {
      parts.push(
        <span key={at} className="text-muted italic">
          {literal}
        </span>,
      )
    } else if (number) {
      parts.push(
        <span key={at} className="text-code-number">
          {number}
        </span>,
      )
    } else {
      parts.push(whole)
    }
    last = at + whole.length
  }
  if (last < text.length) parts.push(text.slice(last))

  return (
    <pre className="fm-scroll overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-[1.55] text-ink">
      {parts}
    </pre>
  )
})
