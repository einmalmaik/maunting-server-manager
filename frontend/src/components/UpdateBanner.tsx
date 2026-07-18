import { useEffect, useState } from 'react'
import { Download, X, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import type { GitUpdateStatus } from '@/types'

export function UpdateBanner() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<GitUpdateStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [updating, setUpdating] = useState(false)

  const fetchStatus = () => {
    api<GitUpdateStatus>('/system/update/status')
      .then((data) => {
        if (data.update_available && !data.updates_automatic) {
          setStatus(data)
        } else {
          setStatus(null)
        }
      })
      .catch(() => {})
  }

  useEffect(() => {
    fetchStatus()
    // Poll updates status every 5 minutes
    const tId = setInterval(fetchStatus, 300000)
    return () => clearInterval(tId)
  }, [])

  const handleUpdate = async () => {
    if (updating) return
    setUpdating(true)
    try {
      await api('/system/update/panel', { method: 'POST' })
      toast.success(t('updater.updateStarted', 'Update wird ausgeführt. Das Panel startet gleich neu.'))
      
      // Poll until panel is back online and updated
      let checkCount = 0
      const interval = setInterval(async () => {
        checkCount++
        try {
          const check = await api<GitUpdateStatus>('/system/update/status')
          if (check.ok && !check.update_available) {
            clearInterval(interval)
            toast.success(t('updater.updateSuccess', 'Update erfolgreich abgeschlossen!'))
            window.location.reload()
          }
        } catch {
          // Ignore connection errors during reboot
          if (checkCount > 40) { // 2 minutes timeout
            clearInterval(interval)
            setUpdating(false)
            toast.error(t('updater.updateTimeout', 'Update-Timeout. Bitte lade die Seite manuell neu.'))
          }
        }
      }, 3000)
    } catch (err: any) {
      toast.error(err.message)
      setUpdating(false)
    }
  }

  if (!status || dismissed) return null

  return (
    <div className="msm-card border border-status-warning/30 bg-status-warning/5 p-4 mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Download className={`w-5 h-5 text-status-warning flex-shrink-0 mt-0.5 ${updating ? 'animate-bounce' : ''}`} />
          <div>
            <h3 className="font-headline text-body-md text-on-surface">
              {t('updater.panelUpdateAvailable', 'Panel-Update verfügbar')}
            </h3>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t('updater.current')}: <span className="font-mono">{status.local_sha}</span>
              {' → '}
              <span className="font-mono text-status-warning">{status.remote_sha}</span>
              {` (${t('updater.branch', 'Branch')}: ${status.branch})`}
            </p>
            <div className="flex items-center gap-3 mt-3">
              <button
                onClick={handleUpdate}
                disabled={updating}
                className="msm-btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm disabled:opacity-60"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${updating ? 'animate-spin' : ''}`} />
                {updating ? t('updater.updating', 'Update läuft...') : t('updater.startUpdate', 'Update starten')}
              </button>
              <span className="font-body-md text-xs text-on-surface-variant">
                {t('updater.manualUpdateCommand', 'Oder manuell auf dem Server:')}{' '}
                <code className="font-mono bg-surface-container-high px-1 py-0.5 rounded">
                  sudo bash /opt/msm/update.sh
                </code>
              </span>
            </div>
          </div>
        </div>
        {!updating && (
          <button
            onClick={() => setDismissed(true)}
            className="text-on-surface-variant hover:text-on-surface transition-colors"
            aria-label={t('common.close', 'Schließen')}
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  )
}
