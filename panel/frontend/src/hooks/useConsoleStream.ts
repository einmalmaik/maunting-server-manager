import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { consoleApi } from '@/lib/api'
import type { ConsoleFrame, ConsoleLine, ConsoleSource } from '@/lib/types'
import { useUiLanguage } from '@/lib/ui-language'

const MAX_LINES = 5_000
const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const FILTER_DEBOUNCE_MS = 200

export type ConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'error'
  | 'closed'

export interface UseConsoleStreamReturn {
  lines: ConsoleLine[]
  filteredLines: ConsoleLine[]
  lineCount: number
  filter: string
  setFilter: (v: string) => void
  source: ConsoleSource
  setSource: (s: ConsoleSource) => void
  status: ConnectionStatus
  errorMessage: string | null
  clear: () => void
  connect: () => void
  disconnect: () => void
}

export function useConsoleStream(serverName: string | null): UseConsoleStreamReturn {
  const enabled = Boolean(serverName)
  const { copy } = useUiLanguage()
  const [lines, setLines] = useState<ConsoleLine[]>([])
  const [lineCount, setLineCount] = useState(0)
  const [filterRaw, setFilterRaw] = useState('')
  const [filterDebounced, setFilterDebounced] = useState('')
  const [source, setSourceState] = useState<ConsoleSource>('log')
  const [status, setStatus] = useState<ConnectionStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const backoffRef = useRef(INITIAL_BACKOFF_MS)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sourceRef = useRef<ConsoleSource>('log')
  const enabledRef = useRef(enabled)
  const unmountedRef = useRef(false)
  const connectingRef = useRef(false)
  const connectGenRef = useRef(0)

  useEffect(() => {
    enabledRef.current = enabled
  }, [enabled])

  useEffect(() => {
    const id = setTimeout(() => setFilterDebounced(filterRaw), FILTER_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [filterRaw])

  const filteredLines = useMemo(
    () =>
      filterDebounced
        ? lines.filter((line) => line.text.toLowerCase().includes(filterDebounced.toLowerCase()))
        : lines,
    [filterDebounced, lines],
  )

  const disconnect = useCallback((nextStatus: ConnectionStatus = 'closed') => {
    connectGenRef.current += 1
    connectingRef.current = false

    if (reconnectTimerRef.current != null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    if (wsRef.current) {
      wsRef.current.close(1000)
      wsRef.current = null
    }

    setStatus(nextStatus)
  }, [])

  const connect = useCallback(async () => {
    if (!enabledRef.current || unmountedRef.current || connectingRef.current) return
    connectingRef.current = true

    const gen = ++connectGenRef.current

    if (reconnectTimerRef.current != null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
      wsRef.current.close(1000)
    }

    setStatus('connecting')
    setErrorMessage(null)

    let token: string
    try {
      const resp = await consoleApi.getToken(sourceRef.current)
      token = resp.token
    } catch {
      connectingRef.current = false
      if (unmountedRef.current || !enabledRef.current) return
      setStatus('error')
      setErrorMessage(copy.console.tokenFailed)
      return
    }

    if (gen !== connectGenRef.current || unmountedRef.current || !enabledRef.current) {
      if (gen === connectGenRef.current) {
        connectingRef.current = false
      }
      return
    }

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/api/console/ws`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      if (gen !== connectGenRef.current || unmountedRef.current || !enabledRef.current) {
        ws.close(1000)
        return
      }
      ws.send(JSON.stringify({ type: 'auth', token }))
      connectingRef.current = false
      setStatus('connected')
      backoffRef.current = INITIAL_BACKOFF_MS
    }

    ws.onmessage = (ev: MessageEvent<string>) => {
      if (unmountedRef.current) return

      let frame: ConsoleFrame
      try {
        frame = JSON.parse(ev.data) as ConsoleFrame
      } catch {
        return
      }

      if (frame.type === 'line') {
        const incoming = frame.data
        if (!Array.isArray(incoming)) return
        setLines((prev) => {
          const next = [...prev, ...incoming]
          return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
        })
        setLineCount((count) => count + incoming.length)
        return
      }

      if (frame.type === 'error') {
        setErrorMessage(frame.data)
      }
    }

    ws.onclose = (ev: CloseEvent) => {
      if (unmountedRef.current || wsRef.current !== ws) return
      connectingRef.current = false

      if (ev.code === 4403) {
        setStatus('error')
        setErrorMessage(copy.console.authFailed)
        return
      }

      if (ev.code === 1000 || !enabledRef.current) {
        setStatus(enabledRef.current ? 'closed' : 'idle')
        return
      }

      setStatus('reconnecting')
      const delay = backoffRef.current
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS)
      reconnectTimerRef.current = setTimeout(() => {
        if (!unmountedRef.current && enabledRef.current) {
          void connect()
        }
      }, delay)
    }
  }, [copy.console.authFailed, copy.console.tokenFailed])

  const setSource = useCallback((nextSource: ConsoleSource) => {
    sourceRef.current = nextSource
    setSourceState(nextSource)
    setLines([])
    setLineCount(0)
    setErrorMessage(null)
    backoffRef.current = INITIAL_BACKOFF_MS

    if (reconnectTimerRef.current != null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    connectGenRef.current += 1
    connectingRef.current = false

    if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
      wsRef.current.close(1000)
      wsRef.current = null
    }

    if (enabledRef.current) {
      void connect()
    } else {
      setStatus('idle')
    }
  }, [connect])

  const clear = useCallback(() => {
    setLines([])
    setLineCount(0)
  }, [])

  useEffect(() => {
    unmountedRef.current = false
    return () => {
      unmountedRef.current = true
      if (reconnectTimerRef.current != null) {
        clearTimeout(reconnectTimerRef.current)
      }
      wsRef.current?.close(1000)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      disconnect('idle')
      setLines([])
      setLineCount(0)
      setErrorMessage(null)
      return
    }

    setLines([])
    setLineCount(0)
    setErrorMessage(null)
    backoffRef.current = INITIAL_BACKOFF_MS
    void connect()

    return () => {
      if (reconnectTimerRef.current != null) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.close(1000)
        wsRef.current = null
      }
      connectingRef.current = false
    }
  }, [connect, disconnect, enabled, serverName])

  return {
    lines,
    filteredLines,
    lineCount,
    filter: filterRaw,
    setFilter: setFilterRaw,
    source,
    setSource,
    status,
    errorMessage,
    clear,
    connect: () => {
      if (enabledRef.current) {
        void connect()
      }
    },
    disconnect: () => disconnect('closed'),
  }
}
