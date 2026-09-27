import { buttonClasses } from '@/components/ui/Button'

export interface Sprungziel {
  id: string
  label: string
}

interface SprungleisteProps {
  label: string
  ziele: readonly Sprungziel[]
}

/**
 * Klebende Leiste mit Sprungmarken zu den Abschnitten einer langen Seite.
 *
 * Sie klebt direkt unter der Kopfleiste: am Handy unter der 48-px-Topbar
 * (`top-12`), ab `lg` gibt es keine Topbar, also ganz oben (`top-0`). Bis
 * 27.09.2026 klebte sie bei `top-16` — auf dem Desktop scrollte der Inhalt
 * durch die 64-px-Lücke darüber und sah aus, als liefe er durch die Reiter.
 *
 * Die Zielüberschriften brauchen `SPRUNGZIEL_ABSTAND`, sonst landen sie beim
 * Anspringen unter der Leiste.
 */
export function Sprungleiste({ label, ziele }: SprungleisteProps) {
  return (
    <nav
      aria-label={label}
      className="sticky top-12 z-20 -mx-1 mb-6 flex gap-2 overflow-x-auto border-b border-outline-variant bg-background/95 px-1 py-2 backdrop-blur-xl [scrollbar-width:none] lg:top-0 [&::-webkit-scrollbar]:hidden"
    >
      {ziele.map(ziel => (
        <a key={ziel.id} href={`#${ziel.id}`} className={buttonClasses('secondary', 'sm', 'shrink-0')}>
          {ziel.label}
        </a>
      ))}
    </nav>
  )
}

/** Topbar (48 px, nur unter `lg`) plus Leiste (49 px) plus etwas Luft. */
export const SPRUNGZIEL_ABSTAND = 'scroll-mt-28 lg:scroll-mt-16'
