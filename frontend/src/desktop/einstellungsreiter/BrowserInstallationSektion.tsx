import { Globe, Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/Singra/UI'
import { oeffneBrowser } from '../tauri'

/**
 * Einstellungskachel in MSS (Desktop & Android) zur Installation des Maunting Secure Browsers.
 *
 * Öffnet unter Windows direkt das Setup-Paket und unter Android den APK-Download
 * über den Standard-Download-Mechanismus des Betriebssystems.
 */
export function BrowserInstallationSektion() {
  const { t } = useTranslation()
  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  const downloadUrl = isAndroid
    ? 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser.apk'
    : 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser-Setup.exe'

  return (
    <div className="border-t border-outline-variant/40 pt-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0">
            <Globe className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-on-surface">Maunting Secure Browser</p>
            <p className="text-xs text-on-surface-variant truncate">
              {isAndroid
                ? t('mss.app.browserDownloadSubtextAndroid')
                : t('mss.app.browserDownloadSubtext')}
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void oeffneBrowser(downloadUrl)}
          className="shrink-0 flex items-center gap-1.5"
        >
          <Download className="w-3.5 h-3.5" />
          <span>
            {isAndroid
              ? t('mss.app.browserLadenAndroid')
              : t('mss.app.browserInstallieren')}
          </span>
        </Button>
      </div>
    </div>
  )
}
