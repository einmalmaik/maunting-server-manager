import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Download } from 'lucide-react'
import { api } from '@/api/client'

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
    <div className="px-3 py-2">
      <a
        href="https://github.com/einmalmaik/MFS/releases/latest/download/MauntingStoryFable.apk"
        target="_blank"
        rel="noreferrer noopener"
        className="group flex items-center gap-3 p-2.5 rounded-xl bg-surface-container-high/60 hover:bg-surface-container-highest/80 border border-outline-variant/30 hover:border-primary/40 transition-all duration-200"
        title={t('storyFableBadge.tooltip', 'Maunting Story Fable als Android-App laden (APK, Sideload)')}
      >
        <div className="w-8 h-8 rounded-lg bg-primary/15 group-hover:bg-primary/25 text-primary flex items-center justify-center shrink-0 transition-colors">
          <BookOpen className="w-4 h-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-label-md text-label-md text-on-surface font-medium truncate flex items-center gap-1">
            <span>Story Fable</span>
            <Download className="w-3 h-3 text-primary opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
          <p className="text-[11px] text-on-surface-variant/80 truncate">
            {t('storyFableBadge.label', 'Die Geschichte vergisst nichts.')}
          </p>
        </div>
      </a>
    </div>
  )
}
