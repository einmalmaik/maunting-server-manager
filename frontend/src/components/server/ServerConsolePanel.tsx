import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eraser, Send, Terminal } from 'lucide-react'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'

interface Props {
  serverId: number
}

/** Klassifiziert eine Log-Zeile fuer das Farb-Coding.
 *  Reine Heuristik anhand gaengiger Log-Level-Tokens. */
function classifyLine(line: string): 'error' | 'warn' | 'info' | 'default' {
  const upper = line.toUpperCase()
  if (
    /\b(ERROR|FATAL|CRITICAL|EXCEPTION|TRACEBACK)\b/.test(upper) ||
    /\bERR\b/.test(upper)
  ) {
    return 'error'
  }
  if (/\bWARN(ING)?\b/.test(upper)) {
    return 'warn'
  }
  if (/\b(INFO|NOTICE|STARTED|READY|LISTENING)\b/.test(upper)) {
    return 'info'
  }
  return 'default'
}

const LINE_CLASS: Record<ReturnType<typeof classifyLine>, string> = {
  error: 'text-status-destructive',
  warn: 'text-status-warning',
  info: 'text-status-success',
  default: 'text-on-surface-variant',
}

/** Server-Konsole als eigener Tab.
 *
 *  - **Lesen:** Server-Sent Events ueber `/api/servers/:id/console/stream`
 *    (gestreamt mit `docker logs --follow`). Auto-Reconnect via Browser-
 *    EventSource bei Verbindungsabbruch.
 *  - **Eingabe:** sichtbar nur mit Permission `server.console.write`. Enter
 *    schickt POST an `/api/servers/:id/console/input`. Eingabe wird nicht
 *    geloggt — Inhalt kann sensibel sein (OAuth-Codes, RCON-Tokens).
 *  - **Lokal leeren:** versteckt den aktuellen Verlauf, neue Zeilen kommen
 *    weiterhin.
 *  - Farb-Coding pro Zeile (Error rot, Warning gelb, Info gruen).
 *  - Scrollbar visuell ausgeblendet, Scrollen funktioniert weiterhin.
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
    es.onmessage = (ev) => {
      // Backend liefert je SSE-Frame eine Log-Zeile. ``data:`` ist bereits
      // ohne Newline (siehe Router).
      setLogs((prev) => [...prev, ev.data])
    }
    es.addEventListener('end', () => {
      // Backend signalisiert Stream-Ende (Container weg). Verbindung schliessen.
      es.close()
    })
    es.onerror = () => {
      // Stille: Browser reconnected automatisch. Logs nicht spammen — der User
      // sieht die fehlende Verbindung daran, dass keine neuen Zeilen kommen.
    }
    return () => {
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
              <div key={i} className={LINE_CLASS[classifyLine(line)]}>
                {line || '\u00A0'}
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
