/**
 * useWebSocket — generischer React-Hook fuer WebSocket-Lifecycles.
 *
 * Kapselt die Boilerplate, die bei jedem langlebigen WS-Endpoint gleich
 * aussieht:
 *   - Connect/Close/Reconnect mit exponentiellem Backoff (default 1s/2s/5s/10s, max 10)
 *   - Ping-Heartbeat gegen Idle-Disconnect in Background-Tabs (default 25s)
 *   - Connection-Status ('connecting' | 'live' | 'reconnecting' | 'failed')
 *   - 1008-Close direkt als 'failed' (Reconnect aussichtslos)
 *   - Cleanup beim Unmount (kein Leak)
 *
 * Was der Hook NICHT macht (bewusst Consumer-Verantwortung):
 *   - Parsing der Message (Raw-String wird geliefert, Consumer parst)
 *   - Replay-Resume / last_id Tracking (Consumer uebergibt aktuelle URL via buildUrl)
 *   - Buffering / Batching / Rendering (Panel-spezifisch)
 *
 * Beispiel: Console-Stream mit Replay-Resume:
 *
 *   const lastIdRef = useRef<number | null>(null)
 *   const { status, send } = useWebSocket({
 *     buildUrl: () => lastIdRef.current !== null
 *       ? `/api/servers/42/console/ws?last_id=${lastIdRef.current}`
 *       : `/api/servers/42/console/ws`,
 *     onMessage: (raw) => { ... },
 *     onStatusChange: setConnStatus,
 *     onError: (kind) => toast.error(t(`connection.${kind}`)),
 *     heartbeat: { payload: JSON.stringify({ action: 'ping' }) },
 *   })
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { wsUrl } from '@/config/api'

export type ConnectionStatus = 'connecting' | 'live' | 'reconnecting' | 'failed'

export type ConnectionErrorKind = 'rejected' | 'failed' | 'reconnecting'

export interface UseWebSocketOptions {
  /** Baut die finale URL. Wird bei jedem Connect-Versuch aufgerufen, damit
   *  z. B. ein aktualisierter ``last_id`` automatisch im Reconnect landet. */
  buildUrl: () => string

  /** Empfaengt jeden Server-Frame als rohen String. Consumer parsed selbst. */
  onMessage: (raw: string) => void

  /** Optional: Status-Changes fuer UI-Banner. */
  onStatusChange?: (status: ConnectionStatus) => void

  /** Optional: Error-Events. ``'rejected'`` = 1008-Close (Origin/Auth), nicht
   *  reconnect-faehig. ``'failed'`` = Max-Attempts ueberschritten. */
  onError?: (kind: ConnectionErrorKind, info?: { attempts: number }) => void

  /** Optional: Ping-Heartbeat. Hook sendet ``payload`` automatisch alle
   *  ``intervalMs`` solange WS offen. Default interval: 25_000 ms. */
  heartbeat?: {
    payload: string
    intervalMs?: number
  }

  /** Optional: Backoff- und Max-Attempts-Konfiguration. */
  reconnect?: {
    delaysMs?: number[]
    maxAttempts?: number
  }
}

export interface UseWebSocketResult {
  status: ConnectionStatus
  /** Sendet einen Frame, falls WS offen. Liefert ``false`` wenn nicht offen. */
  send: (payload: string) => boolean
}

const DEFAULT_HEARTBEAT_INTERVAL_MS = 25_000
const DEFAULT_RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000]
const DEFAULT_MAX_RECONNECT_ATTEMPTS = 10

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketResult {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const pingTimerRef = useRef<number | null>(null)
  const reconnectAttemptRef = useRef(0)
  const cancelledRef = useRef(false)

  // Options ueber Refs einfangen, damit der useEffect nur einmal pro
  // Mount laeuft. Sonst wuerde jede neue Closure (z. B. weil Consumer-State
  // sich aendert) den WS-Connect neu starten.
  const buildUrlRef = useRef(options.buildUrl)
  buildUrlRef.current = options.buildUrl
  const onMessageRef = useRef(options.onMessage)
  onMessageRef.current = options.onMessage
  const onStatusChangeRef = useRef(options.onStatusChange)
  onStatusChangeRef.current = options.onStatusChange
  const onErrorRef = useRef(options.onError)
  onErrorRef.current = options.onError
  const heartbeatRef = useRef(options.heartbeat)
  heartbeatRef.current = options.heartbeat
  const reconnectRef = useRef(options.reconnect)
  reconnectRef.current = options.reconnect

  // Status-Setter mit optionalem Callback
  const setStatusAndNotify = useCallback((next: ConnectionStatus) => {
    setStatus(next)
    onStatusChangeRef.current?.(next)
  }, [])

  useEffect(() => {
    cancelledRef.current = false
    reconnectAttemptRef.current = 0

    const clearTimers = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (pingTimerRef.current !== null) {
        window.clearInterval(pingTimerRef.current)
        pingTimerRef.current = null
      }
    }

    const scheduleReconnect = () => {
      if (cancelledRef.current) return
      const cfg = reconnectRef.current
      const delays = cfg?.delaysMs ?? DEFAULT_RECONNECT_DELAYS_MS
      const maxAttempts = cfg?.maxAttempts ?? DEFAULT_MAX_RECONNECT_ATTEMPTS

      if (reconnectAttemptRef.current >= maxAttempts) {
        setStatusAndNotify('failed')
        onErrorRef.current?.('failed', { attempts: reconnectAttemptRef.current })
        return
      }
      setStatusAndNotify('reconnecting')
      if (reconnectAttemptRef.current === 0) {
        // Beim allerersten Reconnect (nicht jeder weitere) Callback feuern,
        // damit der Consumer genau einen Toast zeigt, nicht einen pro Versuch.
        onErrorRef.current?.('reconnecting', { attempts: 1 })
      }
      const delay = delays[Math.min(reconnectAttemptRef.current, delays.length - 1)]
      reconnectAttemptRef.current += 1
      reconnectTimerRef.current = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (cancelledRef.current) return
      setStatusAndNotify('connecting')
      // buildUrl may return a path (`/api/...`) or an absolute ws(s) URL.
      const ws = new WebSocket(wsUrl(buildUrlRef.current()))
      wsRef.current = ws

      ws.onopen = () => {
        reconnectAttemptRef.current = 0
        setStatusAndNotify('live')
        const hb = heartbeatRef.current
        if (hb) {
          if (pingTimerRef.current !== null) window.clearInterval(pingTimerRef.current)
          const interval = hb.intervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS
          pingTimerRef.current = window.setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              try {
                ws.send(hb.payload)
              } catch {
                /* ignore */
              }
            }
          }, interval)
        }
      }

      ws.onmessage = (ev) => {
        const raw = typeof ev.data === 'string' ? ev.data : ''
        if (raw) onMessageRef.current(raw)
      }

      ws.onerror = () => {
        // Browser reconnected automatisch bei transienten Fehlern; bei hartem
        // Disconnect triggert onclose direkt. Beides ist OK.
      }

      ws.onclose = (ev) => {
        if (pingTimerRef.current !== null) {
          window.clearInterval(pingTimerRef.current)
          pingTimerRef.current = null
        }
        if (cancelledRef.current) return
        // 1008 = policy violation (z. B. Origin nicht erlaubt, Auth fehlt).
        // In dem Fall ist Reconnect aussichtslos, direkt failed melden.
        if (ev.code === 1008) {
          setStatusAndNotify('failed')
          onErrorRef.current?.('rejected', { attempts: reconnectAttemptRef.current })
          return
        }
        scheduleReconnect()
      }
    }

    connect()

    return () => {
      cancelledRef.current = true
      clearTimers()
      if (wsRef.current) {
        wsRef.current.onclose = null
        try {
          wsRef.current.close()
        } catch {
          /* ignore */
        }
        wsRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Refs kapseln alle Optionen; nur Mount/Unmount triggert.
  }, [])

  const send = useCallback((payload: string): boolean => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(payload)
        return true
      } catch {
        return false
      }
    }
    return false
  }, [])

  return { status, send }
}
