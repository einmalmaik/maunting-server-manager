import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eraser, Terminal } from 'lucide-react'
import { api } from '@/api/client'

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
 *  - Read-only Polling alle 3s (`/servers/:id/console?lines=400`).
 *  - Farb-Coding pro Zeile (Error rot, Warning gelb, Info grün, sonst neutral).
 *  - Lokal leeren via "Leeren"-Button (markiert den aktuellen Log-Stand als
 *    versteckt; neu eintreffende Zeilen werden weiterhin angezeigt).
 *  - Scrollbar visuell ausgeblendet (Scrollen funktioniert weiterhin).
 */
export function ServerConsolePanel({ serverId }: Props) {
  const { t } = useTranslation()
  const [logs, setLogs] = useState('')
  const [hideUpTo, setHideUpTo] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    const fetchLogs = async () => {
      try {
        const data = await api<{ logs: string }>(`/servers/${serverId}/console?lines=400`)
        if (!cancelled) setLogs(data.logs || '')
      } catch {
        // silent
      }
    }
    void fetchLogs()
    const handle = setInterval(fetchLogs, 3000)
    return () => {
      cancelled = true
      clearInterval(handle)
    }
  }, [serverId])

  const visibleLogs = useMemo(() => {
    if (!hideUpTo) return logs
    if (logs.startsWith(hideUpTo)) {
      return logs.slice(hideUpTo.length).replace(/^\n+/, '')
    }
    // Logs wurden serverseitig rotiert/neu geschnitten — Marker verwerfen.
    return logs
  }, [logs, hideUpTo])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [visibleLogs])

  const lines = visibleLogs.length > 0 ? visibleLogs.split('\n') : []

  return (
    <div className="msm-card">
      <div className="p-5 border-b border-outline flex items-center justify-between gap-3 flex-wrap">
        <div className="inline-flex items-center gap-3">
          <Terminal className="w-4 h-4 text-on-surface-variant" />
          <h3 className="font-headline text-body-md text-on-surface">{t('servers.console')}</h3>
        </div>
        <button
          onClick={() => setHideUpTo(logs)}
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
          className="bg-surface-container-lowest border border-outline rounded-md p-4 h-[calc(100vh-340px)] min-h-[420px] overflow-auto font-mono text-xs whitespace-pre-wrap [&::-webkit-scrollbar]:hidden [scrollbar-width:none]"
        >
          {lines.length === 0 ? (
            <span className="text-on-surface-variant">{t('servers.noLogs')}</span>
          ) : (
            lines.map((line, i) => (
              <div key={i} className={LINE_CLASS[classifyLine(line)]}>
                {line || '\u00A0'}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
