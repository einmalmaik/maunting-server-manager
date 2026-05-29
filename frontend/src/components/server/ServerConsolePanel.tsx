import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eraser, Send, Terminal } from 'lucide-react'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'

interface Props {
  serverId: number
}

type LineTone = 'error' | 'warn' | 'success' | 'info' | 'default'

const ANSI_RE = /\x1b\[[0-9;]*m/g

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
  const { t } = useTranslation()
  const canWrite = useHasPermission('server.console.write', serverId)
  const [logs, setLogs] = useState<string[]>([])
  const [hideAbove, setHideAbove] = useState(0)
  const [inputValue, setInputValue] = useState('')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // EventSource sendet automatisch Cookies (same-origin) und reconnectet bei
    // Netzwerk-Aussetzern. Keine zusaetzliche Polling-Logik noetig.
    const url = `/api/servers/${serverId}/console/stream`
    const es = new EventSource(url)
    
    // BATCHING FIX: UI-Freeze verhindern
    let buffer: string[] = []
    
    es.onmessage = (ev) => {
      buffer.push(ev.data)
    }
    
    const flushInterval = setInterval(() => {
      if (buffer.length > 0) {
        const toFlush = buffer
        buffer = []
        setLogs((prev) => {
          const next = [...prev, ...toFlush]
          return next.length > 2000 ? next.slice(-2000) : next
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
  }, [serverId])

  const visibleLogs = useMemo(
    () => logs.slice(hideAbove),
    [logs, hideAbove],
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

  return (
    <div className="msm-card">
      <div className="p-5 border-b border-outline flex items-center justify-between gap-3 flex-wrap">
        <div className="inline-flex items-center gap-3">
          <Terminal className="w-4 h-4 text-on-surface-variant" />
          <h3 className="font-headline text-body-md text-on-surface">{t('servers.console')}</h3>
        </div>
        <button
          onClick={() => setHideAbove(logs.length)}
          className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5"
          title={t('servers.consoleClearTitle')}
        >
          <Eraser className="w-3.5 h-3.5" />
          {t('servers.consoleClear')}
        </button>
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
              <div key={i} className={colorizeOutput(line)}>
                {cleanLine(line) || '\u00A0'}
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
