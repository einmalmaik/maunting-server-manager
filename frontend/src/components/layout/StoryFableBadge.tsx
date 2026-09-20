import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen } from 'lucide-react'
import { api } from '@/api/client'
import { SidebarDownloadBadge } from './SidebarDownloadBadge'

/**
 * Hinweis auf Maunting Story Fable, die Android-App aus demselben Haus.
 *
 * Steht bewusst ueber dem MSS-Download: Wer das Panel benutzt, kennt MSS bereits.
 *
 * Anders als {@link DesktopAppDownloadBadge} ist die Vorgabe **aus**. Der Download zeigt auf
 * ein GitHub-Release, das erst existiert, wenn eines veroeffentlicht wurde — bis dahin waere
 * der Link eine 404-Seite fuer jeden Panel-Nutzer, und kaputte Links sind in AGENTS.md 11.9
 * ausdruecklich verboten. Der Schalter steht in den Panel-Einstellungen unter "Allgemein".
 */
export function StoryFableBadge() {
  const { t } = useTranslation()
  const [enabled, setEnabled] = useState<boolean>(false)

  useEffect(() => {
    let active = true
    api<{ story_fable_download_enabled?: boolean }>('/settings/public')
      .then((data) => {
        if (active && data) {
          setEnabled(data.story_fable_download_enabled ?? false)
        }
      })
      .catch(() => {
        // Rueckfall: aus. Ein Hinweis, der ins Leere fuehrt, ist schlechter als keiner.
      })
    return () => {
      active = false
    }
  }, [])

  if (!enabled) return null

  return (
    <SidebarDownloadBadge
      href="https://github.com/einmalmaik/MFS/releases/latest/download/MauntingStoryFable.apk"
      icon={<BookOpen className="w-4 h-4" />}
      title="Story Fable"
      subtext={t('storyFableBadge.label')}
      tooltip={t('storyFableBadge.tooltip')}
    />
  )
}
