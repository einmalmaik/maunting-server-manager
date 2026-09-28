import type { ReactNode } from 'react'
import { Download } from 'lucide-react'

interface SidebarDownloadBadgeProps {
  href: string
  icon: ReactNode
  title: string
  subtext: string
  tooltip: string
}

/**
 * Die Kachel am Fuss der Seitenleiste, mit der sich eine App herunterladen laesst.
 *
 * Existiert, weil {@link DesktopAppDownloadBadge} und {@link StoryFableBadge} dieselben gut
 * zwei Dutzend Tailwind-Klassen Zeichen fuer Zeichen doppelt trugen. Eine Kachel haette sich
 * dadurch bei jeder Gestaltungsaenderung von der anderen entfernt, ohne dass das jemandem
 * auffaellt — zwei Kacheln direkt uebereinander, die nicht mehr gleich aussehen.
 *
 * Bewusst nur die Darstellung: Ob eine Kachel ueberhaupt erscheint, entscheidet jede Seite
 * selbst. Die Vorgaben sind verschieden (MSS an, Story Fable aus), und genau darin liegt eine
 * Aussage, die keine gemeinsame Abstraktion verwischen darf.
 */
export function SidebarDownloadBadge({ href, icon, title, subtext, tooltip }: SidebarDownloadBadgeProps) {
  return (
    <div className="px-3 py-2">
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        className="group flex items-center gap-3 p-2.5 rounded-xl bg-surface-container-high/60 hover:bg-surface-container-highest/80 border border-outline-variant/30 hover:border-primary/40 transition-all duration-200"
        title={tooltip}
      >
        <div className="w-8 h-8 rounded-lg bg-primary/15 group-hover:bg-primary/25 text-primary flex items-center justify-center shrink-0 transition-colors">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-label-md text-label-md text-on-surface font-medium truncate flex items-center gap-1">
            <span>{title}</span>
            <Download className="w-3 h-3 text-primary opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
          <p className="text-label-sm text-on-surface-variant/80 truncate">{subtext}</p>
        </div>
      </a>
    </div>
  )
}
