/**
 * One of the server's `ui://` views, running for real (W2-6, HAD-8).
 *
 * This is the **host** half of the MCP Apps extension (`2026-01-26`,
 * SEP-1865). The HTML is not the demo's: it is exactly what `fanmoot-mcp`
 * served over `resources/read`, and it renders the same way it would in any
 * MCP Apps host. So a judge is looking at the artifact, not a mock-up of it.
 *
 * **Isolation.** The view runs inside `/sandbox` (app/sandbox.py), framed with
 * `sandbox="allow-scripts allow-forms"` and no `allow-same-origin`: an opaque
 * origin that cannot reach this page's DOM, storage or socket. The proxy puts
 * the view in an inner frame under a CSP built from the resource's own
 * `_meta.ui.csp`, and relays messages both ways.
 *
 * **The conversation**, in the spec's order:
 *
 *   proxy → host   ui/notifications/sandbox-proxy-ready
 *   host  → proxy  ui/notifications/sandbox-resource-ready  {html, csp}
 *   view  → host   ui/initialize            → hostCapabilities, hostContext
 *   view  → host   ui/notifications/initialized
 *   host  → view   ui/notifications/tool-input, then tool-result
 *
 * and whatever the view asks for afterwards: `tools/call` (the thread view's
 * reply composer — made by the server, on the visitor's MCP session),
 * `ui/open-link` (offered to the person, never opened behind their back),
 * `ui/message` (becomes the fan's next turn), `ui/request-display-mode`, and
 * `ui/update-model-context`. Anything else is refused as method-not-found
 * rather than guessed at.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { CallToolResult } from '@/lib/protocol'
import type { ViewPayload, ViewResource } from '@/lib/socket'
import type { Theme } from '@/lib/theme'

/** The spec version these bridge methods were read from — as the views announce. */
const PROTOCOL = '2026-01-26'
const MODES = ['inline', 'fullscreen']

export type DisplayMode = 'inline' | 'fullscreen'

type Rpc = { jsonrpc: '2.0'; id?: number | string; method?: string; params?: Record<string, unknown> }

export type ViewHost = {
  call: (name: string, args: Record<string, unknown>) => Promise<CallToolResult>
  /** The view asked to put words in the conversation. False when that cannot happen now. */
  message: (text: string) => boolean
  link: (url: string) => void
  displayMode: (mode: DisplayMode) => void
}

function csp(resource: ViewResource): unknown {
  const ui = resource.meta?.ui
  return ui && typeof ui === 'object' ? (ui as Record<string, unknown>).csp : undefined
}

export function ViewFrame({
  card,
  resource,
  host,
  theme,
  mode,
  version,
  title,
  interactive,
}: {
  card: ViewPayload
  resource: ViewResource
  host: ViewHost
  theme: Theme
  mode: DisplayMode
  version: string
  title: string
  /** False for a card further down the stack: seen, not operable. */
  interactive: boolean
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(180)
  const [ready, setReady] = useState(false)
  const latest = useRef({ host, theme, mode, card })
  latest.current = { host, theme, mode, card }
  const initialized = useRef(false)

  useLayoutEffect(() => {
    const element = frame.current
    if (!element) return
    initialized.current = false

    const post = (message: Rpc) => element.contentWindow?.postMessage(message, '*')
    const reply = (id: Rpc['id'], result: unknown) => post({ jsonrpc: '2.0', id, result } as Rpc)
    const refuse = (id: Rpc['id'], code: number, message: string) =>
      element.contentWindow?.postMessage({ jsonrpc: '2.0', id, error: { code, message } }, '*')

    const onMessage = (event: MessageEvent) => {
      if (event.source !== element.contentWindow) return
      const data = event.data as Rpc | null
      if (!data || data.jsonrpc !== '2.0' || typeof data.method !== 'string') return
      const { id, method } = data
      const params = data.params ?? {}
      const { host: currentHost, theme: currentTheme, mode: currentMode, card: currentCard } = latest.current

      switch (method) {
        case 'ui/notifications/sandbox-proxy-ready':
          post({
            jsonrpc: '2.0',
            method: 'ui/notifications/sandbox-resource-ready',
            params: { html: resource.html, csp: csp(resource), title },
          })
          return

        case 'ui/initialize':
          reply(id, {
            protocolVersion: PROTOCOL,
            hostCapabilities: { openLinks: {}, serverTools: {}, sandbox: { csp: csp(resource) ?? {} } },
            hostInfo: { name: 'fanmoot-alexa-plus-demo', version },
            hostContext: {
              toolInfo: { id: currentCard.call_id, tool: { name: currentCard.tool, inputSchema: { type: 'object' } } },
              theme: currentTheme,
              displayMode: currentMode,
              availableDisplayModes: MODES,
              containerDimensions: { maxWidth: Math.round(element.clientWidth) || 640 },
              locale: navigator.language,
              timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
              userAgent: 'fanmoot-alexa-plus-demo',
              platform: 'web',
              deviceCapabilities: {
                touch: window.matchMedia('(pointer: coarse)').matches,
                hover: window.matchMedia('(hover: hover)').matches,
              },
            },
          })
          return

        case 'ui/notifications/initialized':
          if (initialized.current) return
          initialized.current = true
          post({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: currentCard.input } })
          post({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: currentCard.result })
          setReady(true)
          return

        case 'ui/notifications/size-changed': {
          const next = Number(params.height)
          if (Number.isFinite(next) && next > 0) setHeight(Math.min(2400, Math.ceil(next)))
          return
        }

        case 'tools/call': {
          const name = typeof params.name === 'string' ? params.name : ''
          const args = params.arguments && typeof params.arguments === 'object' ? (params.arguments as Record<string, unknown>) : {}
          currentHost
            .call(name, args)
            .then((result) => reply(id, result))
            .catch((error: Error & { code?: number }) => refuse(id, error.code ?? -32000, error.message))
          return
        }

        case 'ui/open-link': {
          const url = typeof params.url === 'string' ? params.url : ''
          let parsed: URL | null = null
          try {
            parsed = new URL(url)
          } catch {
            parsed = null
          }
          if (!parsed || parsed.protocol !== 'https:') {
            refuse(id, -32602, 'Only https links can be opened.')
            return
          }
          currentHost.link(parsed.toString())
          reply(id, {})
          return
        }

        case 'ui/message': {
          const content = params.content as { type?: string; text?: string } | undefined
          const text = content?.type === 'text' && typeof content.text === 'string' ? content.text.trim() : ''
          if (!text) return refuse(id, -32602, 'A message needs some text.')
          if (currentHost.message(text)) reply(id, {})
          else refuse(id, -32000, 'Alexa is still answering the last one.')
          return
        }

        case 'ui/request-display-mode': {
          const wanted = params.mode === 'fullscreen' ? 'fullscreen' : 'inline'
          currentHost.displayMode(wanted)
          reply(id, { mode: wanted })
          return
        }

        case 'ui/update-model-context':
          // The agent already holds this tool's answer — it made the call —
          // so what the card is showing is acknowledged, not re-sent.
          reply(id, {})
          return

        default:
          if (id !== undefined) refuse(id, -32601, `${method} is not supported by this host.`)
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [resource, title, version])

  // Theme and display mode changes reach a running view as the spec says.
  useEffect(() => {
    if (!initialized.current) return
    frame.current?.contentWindow?.postMessage(
      { jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { theme, displayMode: mode } },
      '*',
    )
  }, [theme, mode])

  return (
    <iframe
      ref={frame}
      src="/sandbox"
      title={title}
      // No allow-same-origin: an opaque origin of its own (app/sandbox.py).
      sandbox="allow-scripts allow-forms"
      referrerPolicy="no-referrer"
      tabIndex={interactive ? 0 : -1}
      className="block w-full rounded-fm-lg bg-transparent transition-[height,opacity] duration-300 ease-[var(--fm-ease)]"
      style={{ height: mode === 'fullscreen' ? '100%' : height, opacity: ready ? 1 : 0 }}
    />
  )
}
