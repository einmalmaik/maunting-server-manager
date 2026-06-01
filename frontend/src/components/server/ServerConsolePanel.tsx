import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Copy, Eraser, Send, Terminal } from 'lucide-react'
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
  line?: string
  text?: string
  timestamp?: string
  source?: 'msm' | 'docker'
}

const ANSI_RE = /\x1b\[[0-9;]*m/g
const URL_RE = /(https?:\/\/[^\s<>"']+)/g
const MAX_LOG_LINES = 2000

function clearMarkerKey(serverId: number): string {
  return `msm.console.clearThrough.${serverId}`
}

function readClearMarker(serverId: number): number {
  try {
    const raw = window.localStorage.getItem(clearMarkerKey(serverId))
    const parsed = raw ? Number.parseInt(raw, 10) : 0
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
  } catch {
    return 0
  }
}

function writeClearMarker(serverId: number, seq: number): void {
  try {
    window.localStorage.setItem(clearMarkerKey(serverId), String(seq))
  } catch {
    // Private mode / quota issues should not break console use.
  }
}

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
  const tone = LINE_PATTERNS.find(([pattern]) => pattern.test(cleaned))?.[1] ?? 'default'
  return LINE_CLASS[tone]
}

export function displayConsoleLine(line: string, language: string): string {
  const cleaned = cleanLine(line)
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

function parseConsoleFrame(raw: string): Omit<ConsoleLogLine, 'marker'> {
  try {
    const parsed = JSON.parse(raw) as ConsoleFrame
    return {
      text: parsed.line ?? parsed.text ?? raw,
      timestamp: parsed.timestamp ?? null,
      source: parsed.source ?? 'unknown',
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

function renderLineContent(line: ConsoleLogLine, language: string, timeFormat: PanelTimeFormat) {
  const display = displayConsoleLine(line.text, language)
  const time = formatConsoleTime(line.timestamp, timeFormat, language)
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

/** Server-Konsole als eigener Tab.
 *
 *  - **Direktanzeige ohne Reload:** Live-Stream (EventSource) startet sofort beim Mount
 *    (Tab-Oeffnen). Backend liefert KOMPLETTEN MSM-Log-Backlog SOFORT + docker logs --follow
 *    (inkl. --tail Buffer bei Container-Start fuer automatischen Re-Buffer).
 *  - Zentrale colorizeOutput (eine Stelle, alle Regex) mit DNA-Token-Klassen
 *    (destructive/warning/success/secondary) + Player-Events (joined/left...).
 *  - **Eingabe:** sichtbar nur mit Permission `server.console.write`. ...
 *  - Farbkodierung wartbar zentral (keine verteilte Einzellogik).
 */
export function ServerConsolePanel({ serverId }: Props) {
  const { t, i18n } = useTranslation()
  const canWrite = useHasPermission('server.console.write', serverId)
  const [logs, setLogs] = useState<ConsoleLogLine[]>([])
  const [hiddenThrough, setHiddenThrough] = useState(() => readClearMarker(serverId))
  const [timeFormat, setTimeFormat] = useState<PanelTimeFormat>('24h')
  const [streamVersion, setStreamVersion] = useState(0)
  const [inputValue, setInputValue] = useState('')
  const [sending, setSending] = useState(false)
  const [copiedLogs, setCopiedLogs] = useState(false)
  const nextSeqRef = useRef(0)
  const bufferRef = useRef<string[]>([])
  const activeStreamRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<{ time_format: PanelTimeFormat }>('/settings')
      .then((data) => setTimeFormat(data.time_format === '12h' ? '12h' : '24h'))
      .catch(() => setTimeFormat('24h'))
  }, [])

  useEffect(() => {
    activeStreamRef.current = streamVersion
    nextSeqRef.current = 0
    bufferRef.current = []
    setLogs([])
    const clearMarker = readClearMarker(serverId)
    nextSeqRef.current = clearMarker
    setHiddenThrough(clearMarker)

    // EventSource sendet automatisch Cookies (same-origin) und reconnectet bei
    // Netzwerk-Aussetzern. Keine zusaetzliche Polling-Logik noetig.
    const url = clearMarker > 0
      ? `/api/servers/${serverId}/console/stream?after=${clearMarker}`
      : `/api/servers/${serverId}/console/stream`
    const es = new EventSource(url)
    const streamToken = streamVersion
    
    // BATCHING FIX: UI-Freeze verhindern
    es.onmessage = (ev) => {
      if (activeStreamRef.current !== streamToken) return
      const eventMarker = Number.parseInt(ev.lastEventId || '', 10)
      const marker = Number.isFinite(eventMarker) && eventMarker > 0
        ? eventMarker
        : nextSeqRef.current + 1
      nextSeqRef.current = Math.max(nextSeqRef.current, marker)
      bufferRef.current.push(JSON.stringify({ marker, raw: ev.data }))
    }
    
    const flushInterval = setInterval(() => {
      if (activeStreamRef.current !== streamToken) return
      if (bufferRef.current.length > 0) {
        const toFlush = bufferRef.current
        bufferRef.current = []
        setLogs((prev) => {
          const mapped = toFlush.map((item) => {
            try {
              const parsed = JSON.parse(item) as { marker: number; raw: string }
              return { marker: parsed.marker, ...parseConsoleFrame(parsed.raw) }
            } catch {
              nextSeqRef.current += 1
              return { marker: nextSeqRef.current, ...parseConsoleFrame(item) }
            }
          })
          const next = [...prev, ...mapped]
          return next.length > MAX_LOG_LINES ? next.slice(-MAX_LOG_LINES) : next
        })
      }
    }, 50) // Alle 50ms gebatcht in den React-State flushen

    es.onerror = () => {
      // Stille: Browser reconnected automatisch.
    }
    return () => {
      clearInterval(flushInterval)
      es.close()
    }
  }, [serverId, streamVersion])

  const visibleLogs = useMemo(
    () => logs.filter((line) => line.marker > hiddenThrough),
    [logs, hiddenThrough],
  )

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [visibleLogs])

  const sendInput = async () => {
    const line = inputValue
    if (!line.trim()) return
    setSending(true)
    // Eingabefeld sofort leeren — auch wenn der POST fehlschlaegt, soll der
    // User nicht den Eindruck haben, dass das Feld haengt.
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

  const clearConsole = () => {
    bufferRef.current = []
    const seq = nextSeqRef.current
    setHiddenThrough(seq)
    writeClearMarker(serverId, seq)
    setLogs([])
    setStreamVersion((current) => current + 1)
  }

  const copyVisibleLogs = async () => {
    const text = visibleLogs
      .map((line) => {
        const time = formatConsoleTime(line.timestamp, timeFormat, i18n.language)
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
        </div>
        <div className="inline-flex items-center gap-2">
          <button
            type="button"
            onClick={() => void copyVisibleLogs()}
            disabled={visibleLogs.length === 0}
            className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
            title={t('servers.consoleCopyTitle')}
          >
            {copiedLogs ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            {copiedLogs ? t('common.copied') : t('servers.consoleCopy')}
          </button>
          <button
            type="button"
            onClick={clearConsole}
            className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5"
            title={t('servers.consoleClearTitle')}
          >
            <Eraser className="w-3.5 h-3.5" />
            {t('servers.consoleClear')}
          </button>
        </div>
      </div>
      <div className="p-5">
        <div
          ref={scrollRef}
          className="bg-surface-container-lowest border border-outline rounded-md p-4 h-[calc(100vh-380px)] min-h-[420px] overflow-auto font-mono text-xs whitespace-pre-wrap [&::-webkit-scrollbar]:hidden [scrollbar-width:none]"
        >
          {visibleLogs.length === 0 ? (
            <span className="text-on-surface-variant">{t('servers.noLogs')}</span>
          ) : (
            visibleLogs.map((line, i) => (
              <div key={`${line.marker}-${i}`} className={colorizeOutput(line.text)}>
                {displayConsoleLine(line.text, i18n.language)
                  ? renderLineContent(line, i18n.language, timeFormat)
                  : '\u00A0'}
              </div>
            ))
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
