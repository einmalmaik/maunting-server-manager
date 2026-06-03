import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Clock, Copy, Search, Send, Terminal } from 'lucide-react'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'
import { type PanelTimeFormat } from '@/utils/timeFormat'

interface Props {
  serverId: number
}

type LineTone = 'error' | 'warn' | 'success' | 'info' | 'default'
type ConsoleLogLine = {
  marker: number
  text: string
  timestamp: string | null
  source: 'msm' | 'docker' | 'unknown'
}
type ConsoleFrame = {
  id?: number
  line?: string
  text?: string
  timestamp?: string
  source?: 'msm' | 'docker'
}

const ANSI_RE = /\x1b\[[0-9;]*m/g
const URL_RE = /(https?:\/\/[^\s<>"']+)/g
const MAX_LOG_LINES = 2000



// ZENTRALE Mapping-Funktion für Farbkodierung (KISS + wartbar).
// Alle Regex-Patterns AN EINER STELLE. Verwendet existierende Design-Token-Klassen
// (text-status-destructive/warning/success, text-secondary, text-on-surface-variant)
// per MauntingStudios Design-DNA. Keine custom Colors, keine verteilte Logik.
// Erweitert um Player-Events (joined/left etc.) für sichtbare Unterscheidung.
const LINE_PATTERNS: Array<[RegExp, LineTone]> = [
  [/\b(ERROR|FATAL|CRITICAL|EXCEPTION|TRACEBACK|ERR)\b/i, 'error'],
  [/\bWARN(ING)?\b/i, 'warn'],
  [/\b(OK|DONE|SUCCESS|SUCCESSFUL|COMPLETED)\b/i, 'success'],
  [/\b(INFO|NOTICE|STARTED|READY|LISTENING)\b/i, 'info'],
  // Player-Events (Pterodactyl-ähnlich, positiv als success)
  [/\b(joined|left|connected|disconnected|login|logout|player.*?(?:join|leave|connect|disconnect))\b/i, 'success'],
]

export function cleanLine(line: string): string {
  return line.replace(ANSI_RE, '')
}

export const LINE_CLASS: Record<LineTone, string> = {
  error: 'text-status-destructive',
  warn: 'text-status-warning',
  success: 'text-status-success',
  info: 'text-secondary',
  default: 'text-on-surface-variant',
}

/** Zentrale colorizeOutput: liefert die passende Token-Klasse für die Zeile (sicher für React-Text). */
export function colorizeOutput(line: string): string {
  const cleaned = cleanLine(line)
  if (cleaned.startsWith('> ')) {
    return 'text-accent font-semibold italic'
  }
  const tone = LINE_PATTERNS.find(([pattern]) => pattern.test(cleaned))?.[1] ?? 'default'
  return LINE_CLASS[tone]
}

export function displayConsoleLine(line: string, language: string): string {
  const cleaned = cleanLine(line)
  if (cleaned.startsWith('> ')) {
    return cleaned
  }
  if (!language.toLowerCase().startsWith('en') || !cleaned.startsWith('[MSM]')) {
    return cleaned
  }

  const replacements: Array<[RegExp, string | ((match: RegExpMatchArray) => string)]> = [
    [/^\[MSM\] Hinweis: Pull für (.+) fehlgeschlagen, nutze lokales Image$/, (m) => `[MSM] Notice: Pull for ${m[1]} failed, using local image`],
    [/^\[MSM\] Container (.+) gestartet$/, (m) => `[MSM] Container ${m[1]} started`],
    [/^\[MSM\] Container (.+) gestoppt$/, (m) => `[MSM] Container ${m[1]} stopped`],
    [/^\[MSM\] Container-Start fehlgeschlagen: (.+)$/, (m) => `[MSM] Container start failed: ${m[1]}`],
    [/^\[MSM\] SteamCMD startet für App (.+) \(Docker\)$/, (m) => `[MSM] SteamCMD starting for app ${m[1]} (Docker)`],
    [/^\[MSM\] SteamCMD abgeschlossen \(App (.+)\)\.$/, (m) => `[MSM] SteamCMD completed (app ${m[1]}).`],
    [/^\[MSM\] SteamCMD fehlgeschlagen: (.+)$/, (m) => `[MSM] SteamCMD failed: ${m[1]}`],
    [/^\[MSM\] Rootless Docker Daemon not running for user msm - Live-Container-Logs deaktiviert\.$/, '[MSM] Rootless Docker Daemon not running for user msm - live container logs disabled.'],
  ]

  for (const [pattern, replacement] of replacements) {
    const match = cleaned.match(pattern)
    if (!match) continue
    return typeof replacement === 'function' ? replacement(match) : replacement
  }
  return cleaned
}

function parseConsoleFrame(raw: string): Omit<ConsoleLogLine, 'marker'> & { id?: number } {
  try {
    const parsed = JSON.parse(raw) as ConsoleFrame
    return {
      text: parsed.line ?? parsed.text ?? raw,
      timestamp: parsed.timestamp ?? null,
      source: parsed.source ?? 'unknown',
      id: typeof parsed.id === 'number' ? parsed.id : undefined,
    }
  } catch {
    return { text: raw, timestamp: null, source: 'unknown' }
  }
}

function formatConsoleTime(value: string | null, format: PanelTimeFormat, locale: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: format === '12h',
  }).format(date)
}

function splitUrlToken(token: string): { href: string; suffix: string } {
  const match = token.match(/^(.+?)([),.;:!?]*)$/)
  return { href: match?.[1] ?? token, suffix: match?.[2] ?? '' }
}

function renderLineContent(line: ConsoleLogLine, language: string, timeFormat: PanelTimeFormat, showTimestamps: boolean) {
  const display = displayConsoleLine(line.text, language)
  const time = showTimestamps ? formatConsoleTime(line.timestamp, timeFormat, language) : ''
  const withTime = time ? `[${time}] ${display}` : display
  const parts = withTime.split(URL_RE)
  return parts.map((part, index) => {
    if (!part.match(URL_RE)) return <span key={index}>{part}</span>
    const { href, suffix } = splitUrlToken(part)
    return (
      <span key={index}>
        <a href={href} target="_blank" rel="noopener noreferrer" className="underline decoration-secondary/70 underline-offset-2 hover:text-primary">
          {href}
        </a>
        {suffix}
      </span>
    )
  })
}

interface ConsoleLogLineDisplayProps {
  line: ConsoleLogLine
  language: string
  timeFormat: PanelTimeFormat
  showTimestamps: boolean
}

const ConsoleLogLineDisplay = React.memo(function ConsoleLogLineDisplay({
  line,
  language,
  timeFormat,
  showTimestamps,
}: ConsoleLogLineDisplayProps) {
  if (!displayConsoleLine(line.text, language)) {
    return <div className={colorizeOutput(line.text)}>{'\u00A0'}</div>
  }
  return (
    <div className={colorizeOutput(line.text)}>
      {renderLineContent(line, language, timeFormat, showTimestamps)}
    </div>
  )
})

export function ServerConsolePanel({ serverId }: Props) {
  const { t, i18n } = useTranslation()
  const canWrite = useHasPermission('server.console.write', serverId)
  const [logs, setLogs] = useState<ConsoleLogLine[]>([])
  const [timeFormat, setTimeFormat] = useState<PanelTimeFormat>('24h')

  const [inputValue, setInputValue] = useState('')
  const [sending, setSending] = useState(false)
  const [copiedLogs, setCopiedLogs] = useState(false)
  const nextSeqRef = useRef(0)
  const bufferRef = useRef<string[]>([])
  // Letzte vom Server empfangene Zeilen-ID. Wird bei Reconnect als
  // ?last_id=<n> an den Server geschickt, damit nur verpasste Zeilen
  // nachgeliefert werden (statt komplettem Backlog).
  const lastServerIdRef = useRef<number | null>(null)
  // Verbindungsstatus fuer sichtbares UI-Feedback ('connecting' zwischen
  // Mount und erstem onopen, 'live' solange WS offen, 'reconnecting'
  // nach Disconnect, 'failed' nach Ueberschreiten der Max-Attempts).
  const [connStatus, setConnStatus] = useState<'connecting' | 'live' | 'reconnecting' | 'failed'>('connecting')
  const scrollRef = useRef<HTMLDivElement>(null)

  // Neue States fuer verbesserte UI/UX
  const [autoscroll, setAutoscroll] = useState(true)
  const [hasNewLines, setHasNewLines] = useState(false)
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState<number>(-1)
  const [searchQuery, setSearchQuery] = useState('')
  const [showTimestamps, setShowTimestamps] = useState(true)

  const autoscrollRef = useRef(true)
  autoscrollRef.current = autoscroll

  useEffect(() => {
    api<{ time_format: PanelTimeFormat }>('/settings')
      .then((data) => setTimeFormat(data.time_format === '12h' ? '12h' : '24h'))
      .catch(() => setTimeFormat('24h'))
  }, [])

  useEffect(() => {
    nextSeqRef.current = 0
    bufferRef.current = []
    lastServerIdRef.current = null
    setLogs([])
    let cancelled = false
    let ws: WebSocket | null = null
    let reconnectTimer: number | null = null
    let pingTimer: number | null = null
    let reconnectAttempt = 0

    const clearTimers = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (pingTimer !== null) {
        window.clearInterval(pingTimer)
        pingTimer = null
      }
    }

    // Max-Attempts: nach Ueberschreiten wird der Loop abgebrochen und der
    // User ueber einen einmaligen Toast informiert. Ohne Cap wuerde der
    // Client bei dauerhaft abgelehnter Verbindung (z. B. falscher Origin)
    // alle 10s einen neuen WS-Handshake produzieren.
    const MAX_RECONNECT_ATTEMPTS = 10

    const flushBuffer = () => {
      if (bufferRef.current.length === 0) return
      const toFlush = bufferRef.current
      bufferRef.current = []
      if (!autoscrollRef.current) {
        setHasNewLines(true)
      }
      setLogs((prev) => {
        const mapped = toFlush.map((item) => {
          try {
            const parsed = JSON.parse(item) as { marker: number; raw: string; id?: number }
            const frame = parseConsoleFrame(parsed.raw)
            // Server-uebermittelte ID uebernehmen wenn vorhanden (WS liefert sie).
            const marker = typeof frame.id === 'number' ? frame.id : parsed.marker
            const { id: _id, ...rest } = frame
            return { marker, ...rest }
          } catch {
            nextSeqRef.current += 1
            return { marker: nextSeqRef.current, ...parseConsoleFrame(item) }
          }
        })
        const next = [...prev, ...mapped]
        return next.length > MAX_LOG_LINES ? next.slice(-MAX_LOG_LINES) : next
      })
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      if (reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
        // Dauerhaft fehlgeschlagen: Loop abbrechen, einmalig toasten.
        setConnStatus('failed')
        toast.error(t('servers.consoleConnectionFailed'))
        return
      }
      setConnStatus('reconnecting')
      if (reconnectAttempt === 0) {
        // Beim allerersten Reconnect (nicht jeder weitere) einen Hinweis-Toast.
        toast.error(t('servers.consoleReconnecting'))
      }
      // Backoff: 1s, 2s, 5s, 10s (cap).
      const delays = [1000, 2000, 5000, 10000]
      const delay = delays[Math.min(reconnectAttempt, delays.length - 1)]
      reconnectAttempt += 1
      reconnectTimer = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (cancelled) return
      setConnStatus('connecting')
      // Reconnect-Resume: letzte empfangene ID mitschicken, damit der Server
      // nur Zeilen mit id > last_id nochmal sendet (statt komplettem Backlog).
      const lastId = lastServerIdRef.current
      const url = lastId !== null
        ? `/api/servers/${serverId}/console/ws?last_id=${lastId}`
        : `/api/servers/${serverId}/console/ws`
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}${url}`)

      ws.onopen = () => {
        reconnectAttempt = 0
        setConnStatus('live')
        // 25s-Ping-Heartbeat haelt die Verbindung in Background-Tabs wach
        // (Browser schliessen sonst inaktive WS nach 60s).
        if (pingTimer !== null) window.clearInterval(pingTimer)
        pingTimer = window.setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify({ action: 'ping' })) } catch { /* ignore */ }
          }
        }, 25_000)
      }

      ws.onmessage = (ev) => {
        const raw = typeof ev.data === 'string' ? ev.data : ''
        if (!raw) return
        try {
          const frame = parseConsoleFrame(raw)
          if (typeof frame.id === 'number') {
            lastServerIdRef.current = frame.id
            nextSeqRef.current = Math.max(nextSeqRef.current, frame.id)
          }
          bufferRef.current.push(JSON.stringify({ marker: frame.id ?? nextSeqRef.current + 1, raw }))
        } catch {
          bufferRef.current.push(raw)
        }
      }

      ws.onerror = () => {
        // Browser reconnected automatisch bei transienten Fehlern; bei hartem
        // Disconnect triggert onclose direkt. Beides ist OK.
      }

      ws.onclose = (ev) => {
        if (pingTimer !== null) {
          window.clearInterval(pingTimer)
          pingTimer = null
        }
        if (cancelled) return
        // 1008 = policy violation (z. B. Origin nicht erlaubt, Auth fehlt).
        // In dem Fall ist Reconnect aussichtslos, direkt failed melden.
        if (ev.code === 1008) {
          setConnStatus('failed')
          toast.error(t('servers.consoleConnectionRejected'))
          return
        }
        scheduleReconnect()
      }
    }

    const flushInterval = setInterval(flushBuffer, 50)
    connect()

    return () => {
      cancelled = true
      clearInterval(flushInterval)
      clearTimers()
      if (ws) {
        ws.onclose = null
        try { ws.close() } catch { /* ignore */ }
      }
    }
  }, [serverId])

  const filteredLogs = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    if (!query) return logs
    return logs.filter((line) =>
      displayConsoleLine(line.text, i18n.language).toLowerCase().includes(query)
    )
  }, [logs, searchQuery, i18n.language])

  useEffect(() => {
    if (scrollRef.current && autoscroll) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [filteredLogs, autoscroll])

  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50
    setAutoscroll(isAtBottom)
    if (isAtBottom) {
      setHasNewLines(false)
    }
  }

  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      setAutoscroll(true)
      setHasNewLines(false)
    }
  }

  const sendInput = async () => {
    const line = inputValue
    if (!line.trim()) return
    setSending(true)
    
    // Command History updaten
    setHistory((prev) => [line, ...prev.filter((item) => item !== line)].slice(0, 50))
    setHistoryIndex(-1)

    // Echo lokal hinzufügen
    const nextSeq = nextSeqRef.current + 1
    nextSeqRef.current = nextSeq
    setLogs((prev) => [
      ...prev,
      {
        marker: nextSeq,
        text: `> ${line}`,
        timestamp: new Date().toISOString(),
        source: 'msm',
      },
    ])

    setInputValue('')
    try {
      await api<{ ok: boolean }>(`/servers/${serverId}/console/input`, {
        method: 'POST',
        body: JSON.stringify({ line }),
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : t('servers.consoleInputFailed')
      toast.error(message)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (history.length === 0) return
      const nextIndex = historyIndex + 1
      if (nextIndex < history.length) {
        setHistoryIndex(nextIndex)
        setInputValue(history[nextIndex])
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      const nextIndex = historyIndex - 1
      if (nextIndex >= 0) {
        setHistoryIndex(nextIndex)
        setInputValue(history[nextIndex])
      } else {
        setHistoryIndex(-1)
        setInputValue('')
      }
    }
  }

  const copyVisibleLogs = async () => {
    const query = searchQuery.trim().toLowerCase()
    const targetLogs = query ? filteredLogs : logs
    const text = targetLogs
      .map((line) => {
        const time = showTimestamps ? formatConsoleTime(line.timestamp, timeFormat, i18n.language) : ''
        const display = displayConsoleLine(line.text, i18n.language)
        return time ? `[${time}] ${display}` : display
      })
      .join('\n')
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedLogs(true)
      window.setTimeout(() => setCopiedLogs(false), 1500)
    } catch {
      toast.error(t('servers.consoleCopyFailed'))
    }
  }

  return (
    <div className="msm-card">
      <div className="p-5 border-b border-outline flex items-center justify-between gap-3 flex-wrap">
        <div className="inline-flex items-center gap-3">
          <Terminal className="w-4 h-4 text-on-surface-variant" />
          <h3 className="font-headline text-body-md text-on-surface">{t('servers.console')}</h3>
          {connStatus !== 'live' && (
            <span
              data-testid="console-conn-status"
              className={`text-[10px] uppercase tracking-wide font-semibold px-2 py-0.5 rounded-full border ${
                connStatus === 'failed'
                  ? 'text-status-destructive border-status-destructive/40 bg-status-destructive/10'
                  : connStatus === 'reconnecting'
                    ? 'text-status-warning border-status-warning/40 bg-status-warning/10'
                    : 'text-on-surface-variant border-outline bg-surface-container-low'
              }`}
            >
              {connStatus === 'connecting' && t('servers.consoleConnecting')}
              {connStatus === 'reconnecting' && t('servers.consoleReconnecting')}
              {connStatus === 'failed' && t('servers.consoleConnectionFailed')}
            </span>
          )}
        </div>

        {/* Suchfeld */}
        <div className="flex-1 max-w-xs md:mx-4 my-1">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-on-surface-variant" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Logs durchsuchen..."
              className="w-full bg-surface-container-lowest border border-outline rounded-md pl-8 pr-3 py-1.5 font-mono text-xs text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        <div className="inline-flex items-center gap-2">
          {/* Zeitstempel umschalten */}
          <button
            type="button"
            onClick={() => setShowTimestamps(!showTimestamps)}
            className={`msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 ${showTimestamps ? 'bg-secondary/15 text-primary border-primary/20' : ''}`}
            title="Zeitstempel umschalten"
          >
            <Clock className="w-3.5 h-3.5" />
            {showTimestamps ? 'Zeitstempel an' : 'Zeitstempel aus'}
          </button>
          
          <button
            type="button"
            onClick={() => void copyVisibleLogs()}
            disabled={filteredLogs.length === 0}
            className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
            title={t('servers.consoleCopyTitle')}
          >
            {copiedLogs ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            {copiedLogs ? t('common.copied') : t('servers.consoleCopy')}
          </button>
        </div>
      </div>
      <div className="p-5">
        <div className="relative">
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="bg-surface-container-lowest border border-outline rounded-md p-4 h-[calc(100vh-380px)] min-h-[420px] overflow-auto font-mono text-xs whitespace-pre-wrap"
          >
            {filteredLogs.length === 0 ? (
              <span className="text-on-surface-variant">{t('servers.noLogs')}</span>
            ) : (
              filteredLogs.map((line, i) => (
                <ConsoleLogLineDisplay
                  key={`${line.marker}-${i}`}
                  line={line}
                  language={i18n.language}
                  timeFormat={timeFormat}
                  showTimestamps={showTimestamps}
                />
              ))
            )}
          </div>

          {hasNewLines && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="absolute bottom-4 right-6 bg-primary text-on-primary hover:bg-primary/90 px-3.5 py-2 rounded-full text-xs font-semibold shadow-lg inline-flex items-center gap-1.5 transition-all duration-200 animate-bounce"
            >
              Neue Zeilen vorhanden ↓
            </button>
          )}
        </div>

        {canWrite && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void sendInput()
            }}
            className="mt-3 flex items-center gap-2"
            data-testid="console-input-form"
          >
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('servers.consolePlaceholder')}
              disabled={sending}
              maxLength={1024}
              autoComplete="off"
              spellCheck={false}
              className="flex-1 bg-surface-container-lowest border border-outline rounded-md px-3 py-2 font-mono text-xs text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
              data-testid="console-input"
            />
            <button
              type="submit"
              disabled={sending || !inputValue.trim()}
              className="msm-btn-primary px-3 py-2 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
              data-testid="console-send"
            >
              <Send className="w-3.5 h-3.5" />
              {t('servers.consoleSend')}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
