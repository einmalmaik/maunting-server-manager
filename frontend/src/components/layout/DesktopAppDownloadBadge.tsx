import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, Monitor, Smartphone } from 'lucide-react'
import { api } from '@/api/client'

export function DesktopAppDownloadBadge() {
  const { t } = useTranslation()
  const [enabled, setEnabled] = useState<boolean>(true)

  useEffect(() => {
    let active = true
    api<{ desktop_app_download_enabled?: boolean }>('/settings/public')
      .then((data) => {
        if (active && data) {
          setEnabled(data.desktop_app_download_enabled ?? true)
        }
      })
      .catch(() => {
        // Fallback: aktiviert lassen
      })
    return () => {
      active = false
    }
  }, [])

  if (!enabled) return null

  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  const downloadUrl = isAndroid
    ? 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem.apk'
    : 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem-Setup.exe'

  const titleText = isAndroid ? 'MSS Mobile App' : 'MSS Desktop'
  const subtext = isAndroid
    ? t('desktopBadge.androidLabel', 'App für Android (.apk)')
    : t('desktopBadge.label', 'Desktop-App für Windows')
  const tooltip = isAndroid
    ? t('desktopBadge.androidTooltip', 'Maunting Smart System Android-App herunterladen')
    : t('desktopBadge.tooltip', 'Maunting Smart System Desktop-App herunterladen')

  return (
    <div className="px-3 py-2">
      <a
        href={downloadUrl}
        target="_blank"
        rel="noreferrer noopener"
        className="group flex items-center gap-3 p-2.5 rounded-xl bg-surface-container-high/60 hover:bg-surface-container-highest/80 border border-outline-variant/30 hover:border-primary/40 transition-all duration-200"
        title={tooltip}
      >
        <div className="w-8 h-8 rounded-lg bg-primary/15 group-hover:bg-primary/25 text-primary flex items-center justify-center shrink-0 transition-colors">
          {isAndroid ? <Smartphone className="w-4 h-4" /> : <Monitor className="w-4 h-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-label-md text-label-md text-on-surface font-medium truncate flex items-center gap-1">
            <span>{titleText}</span>
            <Download className="w-3 h-3 text-primary opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
          <p className="text-[11px] text-on-surface-variant/80 truncate">
            {subtext}
          </p>
        </div>
      </a>
    </div>
  )
}
