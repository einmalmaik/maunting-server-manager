import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Terminal } from 'lucide-react'

interface Props {
  serverId: number
}

/** Server-Konsole als eigener Tab. KISS:
 *  - Read-only Polling alle 3s (`/servers/:id/console?lines=400`).
 *  - Schreibender Console-Input wird bewusst NICHT angeboten: das Backend hat
 *    aktuell kein POST /console (siehe AGENTS.md "keine neue Komplexitaet ohne
 *    Nutzen"). Die `server.console.write`-Permission bleibt fuer Phase 5.
 */
export function ServerConsolePanel({ serverId }: Props) {
  const { t } = useTranslation()
  const [logs, setLogs] = useState('')
  const preRef = useRef<HTMLPreElement>(null)

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

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="msm-card">
      <div className="p-5 border-b border-outline flex items-center gap-3">
        <Terminal className="w-4 h-4 text-on-surface-variant" />
        <h3 className="font-headline text-body-md text-on-surface">{t('servers.console')}</h3>
      </div>
      <div className="p-5 space-y-3">
        <pre
          ref={preRef}
          className="bg-surface-darkest border border-outline rounded-md p-4 h-[480px] overflow-auto font-mono text-xs text-on-surface-variant whitespace-pre-wrap"
        >
          {logs || t('servers.noLogs')}
        </pre>
      </div>
    </div>
  )
}
