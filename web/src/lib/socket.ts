/**
 * The browser's end of `/ws` (app/ws.py).
 *
 * W0 is the transport: connect, read `hello`, measure a round trip with
 * `ping`, and reconnect with backoff when the socket drops. The turn protocol
 * (W1-5) is built on top of this hook rather than beside it.
 */
import { useEffect, useRef, useState } from 'react'

export type SocketStatus = 'connecting' | 'open' | 'closed'

export type Hello = {
  type: 'hello'
  session_id: string
  service: string
  version: string
  wire: number
  mcp_url: string
}

type Frame = Hello | { type: 'pong'; t: number | null } | { type: 'error'; code: string; message: string }

/** Same origin, so the CSP's `connect-src 'self'` covers it and no URL is configured. */
function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/ws`
}

/** 0.5 s, 1 s, 2 s … capped at 10 s: quick on a blip, quiet on an outage. */
const backoff = (attempt: number) => Math.min(10_000, 500 * 2 ** attempt)

export function useDemoSocket() {
  const [status, setStatus] = useState<SocketStatus>('connecting')
  const [hello, setHello] = useState<Hello | null>(null)
  const [rttMs, setRttMs] = useState<number | null>(null)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let attempt = 0
    let retry: number | undefined
    let pinger: number | undefined
    let disposed = false

    const connect = () => {
      setStatus('connecting')
      const socket = new WebSocket(socketUrl())
      socketRef.current = socket

      socket.onopen = () => {
        attempt = 0
        setStatus('open')
        const ping = () => socket.send(JSON.stringify({ type: 'ping', t: performance.now() }))
        ping()
        pinger = window.setInterval(ping, 15_000)
      }

      socket.onmessage = (event) => {
        let frame: Frame
        try {
          frame = JSON.parse(String(event.data)) as Frame
        } catch {
          return
        }
        if (frame.type === 'hello') setHello(frame)
        else if (frame.type === 'pong' && typeof frame.t === 'number') {
          setRttMs(Math.round(performance.now() - frame.t))
        }
      }

      socket.onclose = () => {
        window.clearInterval(pinger)
        if (disposed) return
        setStatus('closed')
        retry = window.setTimeout(connect, backoff(attempt++))
      }
    }

    connect()
    return () => {
      // StrictMode mounts twice in development; the first socket is closed
      // here and must not schedule a reconnect behind the second.
      disposed = true
      window.clearTimeout(retry)
      window.clearInterval(pinger)
      socketRef.current?.close()
    }
  }, [])

  return { status, hello, rttMs }
}
