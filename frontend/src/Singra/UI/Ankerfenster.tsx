/**
 * Ein kleines Fenster an einem Knopf: Inhalt, der kein Menü ist (Schalter,
 * Zahlen, ein kurzer Text). Menüs nehmen `ActionMenu`, Auswahlen `Dropdown`.
 *
 * Liegt per Portal an `body` und hält sich über `useAnkerLage` im Fenster.
 * Schließt mit Escape (in der Capture-Phase, vor einem Dialog darunter), mit
 * einem Klick daneben und mit Zurück. Den Fokus nimmt es beim Öffnen und gibt
 * ihn dem Auslöser zurück.
 */
import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import { useAnkerLage } from './Ankerlage'
import { useZurueckSchliesst } from './useZurueckSchliesst'

export interface AnkerfensterProps {
  offen: boolean
  onSchliessen: () => void
  anker: RefObject<HTMLElement | null>
  /** Der Name des Fensters für Screenreader. */
  label: string
  children: ReactNode
  ausrichtung?: 'start' | 'ende'
  className?: string
}

export function Ankerfenster({ offen, onSchliessen, anker, label, children, ausrichtung = 'start', className = '' }: AnkerfensterProps) {
  const fenster = useRef<HTMLDivElement>(null)
  const lage = useAnkerLage(offen, anker, fenster, { ausrichtung })
  const schliessen = useRef(onSchliessen)
  schliessen.current = onSchliessen
  useZurueckSchliesst(offen, onSchliessen)

  useEffect(() => {
    if (!offen) return
    const ausloeser = anker.current
    const daneben = (e: PointerEvent) => {
      const ziel = e.target as Node
      if (!fenster.current?.contains(ziel) && !ausloeser?.contains(ziel)) schliessen.current()
    }
    const escape = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      e.preventDefault()
      schliessen.current()
      ausloeser?.focus()
    }
    const bild = requestAnimationFrame(() => fenster.current?.focus())
    document.addEventListener('pointerdown', daneben)
    document.addEventListener('keydown', escape, true)
    return () => {
      cancelAnimationFrame(bild)
      document.removeEventListener('pointerdown', daneben)
      document.removeEventListener('keydown', escape, true)
    }
  }, [offen, anker])

  if (!offen) return null
  return createPortal(
    <div
      ref={fenster}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      data-ankerfenster=""
      style={lage}
      className={`max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-lg border border-outline-variant bg-surface-container-high p-4 text-on-surface shadow-xl outline-none ${className}`}
    >
      {children}
    </div>,
    document.body,
  )
}
