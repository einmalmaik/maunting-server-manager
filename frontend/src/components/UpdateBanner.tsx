import { useEffect, useState } from 'react'
import { Download, X, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import type { VersionInfo } from '@/types'

export function UpdateBanner() {
  const { t } = useTranslation()
  const [version, setVersion] = useState<VersionInfo | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    api<VersionInfo>('/system/version')
      .then((data) => {
        if (data.update_available) {
          setVersion(data)
        }
      })
      .catch(() => {})
  }, [])

  if (!version || dismissed) return null

  return (
    <div className="msm-card border border-status-warning/30 bg-status-warning/5 p-4 mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Download className="w-5 h-5 text-status-warning flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-headline text-body-md text-on-surface">
              {t('updater.panelUpdateAvailable', 'Panel-Update verfügbar')}
            </h3>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t('updater.current')}: <span className="font-mono">{version.current_version}</span>
              {' → '}
              <span className="font-mono text-status-warning">{version.latest_version}</span>
            </p>
            <div className="flex items-center gap-3 mt-3">
              <a
                href={version.release_url || `https://github.com/${version.github_repo}/releases`}
                target="_blank"
                rel="noopener noreferrer"
                className="msm-btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-sm"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                {t('updater.viewRelease', 'Release ansehen')}
              </a>
              <span className="font-body-md text-xs text-on-surface-variant">
                {t('updater.manualUpdateCommand', 'Update auf dem Server:')}{' '}
                <code className="font-mono bg-surface-container-high px-1 py-0.5 rounded">
                  sudo bash /opt/msm/update.sh
                </code>
              </span>
            </div>
          </div>
        </div>
        <button
          onClick={() => setDismissed(true)}
          className="text-on-surface-variant hover:text-on-surface transition-colors"
          aria-label={t('common.close', 'Schließen')}
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
