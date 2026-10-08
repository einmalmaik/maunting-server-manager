/** Werkzeugleiste und Symbolknopf der Entwicklerwerkzeuge. */
import { useState, type ReactNode } from 'react'
import { RotateCw, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

export function Leiste({ children }: { children: ReactNode }) {
  return <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-outline-variant px-2 py-1">{children}</div>
}

interface KnopfProps {
  text: string
  onClick: () => void
  children: ReactNode
  /** Schalter: gedrückt oder nicht. */
  an?: boolean
  disabled?: boolean
}

/** Halbe Höchstbreite der Kurzinfo (`max-w-64`). */
const HALBE_BLASE = 128

/**
 * Wo die Blase hängt, gemessen beim Zeigen: nahe am linken Rand des Panels
 * bündig links, nahe am rechten bündig rechts. Links vom Panel liegt die
 * Seite, ein Fenster über der Oberfläche, und verdeckte den Anfang der Blase.
 */
export function blasenSeite(knopf: DOMRect, bereich: DOMRect): 'mitte' | 'anfang' | 'ende' {
  const mitte = knopf.left + knopf.width / 2
  if (mitte - HALBE_BLASE < bereich.left) return 'anfang'
  if (mitte + HALBE_BLASE > bereich.right) return 'ende'
  return 'mitte'
}

/**
 * 44 px nur am Finger. Im MSB fragen Breakpoints das Panel ab, nicht das
 * Gerät (`vite.breitenAlsContainer.ts`): `max-md:` machte jedes schmale Panel
 * zur Fingerfläche.
 */
export function Knopf({ text, onClick, children, an, disabled }: KnopfProps) {
  const [seite, setSeite] = useState<'mitte' | 'anfang' | 'ende'>('mitte')
  const messen = (e: { currentTarget: HTMLElement }) => {
    const bereich = e.currentTarget.closest('.msb-bereich') ?? document.body
    setSeite(blasenSeite(e.currentTarget.getBoundingClientRect(), bereich.getBoundingClientRect()))
  }
  return (
    <Kurzinfo text={text} lage="unten" seite={seite}>
      <button
        type="button"
        onPointerEnter={messen}
        onFocus={messen}
        aria-label={text}
        aria-pressed={an}
        disabled={disabled}
        onClick={onClick}
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md disabled:opacity-40 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 ${
          an ? 'bg-primary/15 text-primary' : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
        }`}
      >
        {children}
      </button>
    </Kurzinfo>
  )
}

/** Aktualisieren und Leeren, für die Bereiche der Anwendung. */
export function AnwendungLeiste({ laden, leeren }: { laden: () => void; leeren?: () => void }) {
  const { t } = useTranslation()
  return (
    <Leiste>
      <Knopf text={t('browser.entwickler.anwendung.aktualisieren')} onClick={laden}>
        <RotateCw className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      {leeren && (
        <Knopf text={t('browser.entwickler.anwendung.leeren')} onClick={leeren}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      )}
    </Leiste>
  )
}
