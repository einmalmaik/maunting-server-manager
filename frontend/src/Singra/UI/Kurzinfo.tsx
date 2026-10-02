import type { ReactNode } from 'react'

export interface KurzinfoProps {
  /** Der Name, der unter dem Knopf erscheint. */
  text: string
  children: ReactNode
  /** Zusätzliche Klassen für die Blase, etwa `xl:hidden`, wenn der Name ab dort sichtbar dasteht. */
  className?: string
}

/**
 * Kurzer Name unter einem Knopf, der nur ein Symbol zeigt. Ersetzt das native
 * `title`: das zeichnet das Betriebssystem in seinem eigenen Stil, erst nach
 * einer Sekunde und am Finger nie.
 *
 * Der Text steht nur als CSS-Inhalt (`data-kurzinfo`), nicht als zweiter
 * Textknoten neben dem Namen des Knopfs.
 *
 * Die Blase erscheint beim Überfahren mit der Maus und beim Tastaturfokus,
 * nicht bei Berührung. Vorgelesen wird sie nicht: den Namen trägt der Knopf
 * selbst per `aria-label`, sonst hörte man ihn doppelt.
 *
 * Sie steht mittig unter dem Knopf und prüft keine Fensterränder. Für Knöpfe
 * direkt am Rand ist sie deshalb nicht gedacht.
 */
export function Kurzinfo({ text, children, className = '' }: KurzinfoProps) {
  return (
    <span className="group/kurzinfo relative inline-flex">
      {children}
      <span
        aria-hidden="true"
        data-kurzinfo={text}
        className={`pointer-events-none before:content-[attr(data-kurzinfo)] absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-outline-variant bg-surface-container-highest px-2 py-1 text-label-sm font-medium text-on-surface opacity-0 shadow-lg transition-opacity duration-150 motion-reduce:transition-none [@media(hover:hover)]:group-hover/kurzinfo:opacity-100 group-has-[:focus-visible]/kurzinfo:opacity-100 ${className}`}
      />
    </span>
  )
}
