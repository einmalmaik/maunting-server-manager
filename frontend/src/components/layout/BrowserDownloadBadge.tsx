import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Smartphone } from 'lucide-react'
import { api } from '@/api/client'
import { geraeteArt } from '@/desktop/geraeteArt'
import { SidebarDownloadBadge } from './SidebarDownloadBadge'

/**
 * Hinweis auf den Maunting Secure Browser (MSB) fuer Windows und Android.
 *
 * Steht ueber dem MSS-Download.
 * Wer das Panel bereits im MSB benutzt (geraeteArt() === 'browser'), sieht den
 * Hinweis nicht — wer schon im Browser surft, muss ihn nicht nochmals laden.
 */
export function BrowserDownloadBadge() {
  const { t } = useTranslation()
  const [enabled, setEnabled] = useState<boolean>(true)

  useEffect(() => {
    let active = true
    api<{ browser_download_enabled?: boolean }>('/settings/public')
      .then((data) => {
        if (active && data) {
          setEnabled(data.browser_download_enabled ?? true)
        }
      })
      .catch(() => {
        // Rueckfall: aktiviert lassen
      })
    return () => {
      active = false
    }
  }, [])

  if (!enabled || geraeteArt() === 'browser') return null

  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  const downloadUrl = isAndroid
    ? 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser.apk'
    : 'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser-Setup.exe'

  const titleText = isAndroid ? 'MSB Mobile' : 'MSB Browser'
  const subtext = isAndroid
    ? t('browserBadge.androidLabel')
    : t('browserBadge.label')
  const tooltip = isAndroid
    ? t('browserBadge.androidTooltip')
    : t('browserBadge.tooltip')

  return (
    <SidebarDownloadBadge
      href={downloadUrl}
      icon={isAndroid ? <Smartphone className="w-4 h-4" /> : <Globe className="w-4 h-4" />}
      title={titleText}
      subtext={subtext}
      tooltip={tooltip}
    />
  )
}
