/**
 * Ein Menü, das am Telefon von unten hereinfährt und am Rechner mittig steht.
 *
 * **Warum von unten.** Auf einem Telefon reicht der Daumen das obere Drittel
 * des Bildschirms nicht; ein Menü in der Mitte zwingt zum Umgreifen, und mit
 * offener Tastatur ist es gar nicht mehr da. Alles Wichtige gehört nach unten.
 *
 * **Warum ein Portal.** `Shell.tsx` kappt jedes `z-50` im Inhaltsbereich auf
 * 10, damit Seiteninhalte die Navigation nicht überdecken. Ein Menü, das im
 * Inhaltsbereich hängt, läge damit **unter** der Kopfzeile. Gerendert wird
 * deshalb an `document.body`, außerhalb dieses Stapelkontexts.
 *
 * Der Inhalt ist Sache des Aufrufers — das hier ist die Hülle: Verdunkelung,
 * sichere Fläche am unteren Rand, Escape, Klick daneben, Fokusfalle.
 */

import React, { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

const FOKUSSIERBAR =
  'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export interface BlattmenueProps {
  offen: boolean
  onSchliessen: () => void
  /** Vorgelesen, wenn das Menü aufgeht. */
  titel: string
  children: React.ReactNode
  /** Zusätzliche Klassen für das Blatt selbst. */
  className?: string
}

export function Blattmenue({ offen, onSchliessen, titel, children, className = '' }: BlattmenueProps) {
  const blatt = useRef<HTMLDivElement>(null)
  const vorherigerFokus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!offen) return
    vorherigerFokus.current =
      typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null

    const beiTaste = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSchliessen()
        return
      }
      if (e.key !== 'Tab' || !blatt.current) return
      const ziele = Array.from(blatt.current.querySelectorAll<HTMLElement>(FOKUSSIERBAR))
      if (!ziele.length) return
      const erstes = ziele[0]
      const letztes = ziele[ziele.length - 1]
      if (e.shiftKey && document.activeElement === erstes) {
        e.preventDefault()
        letztes.focus()
      } else if (!e.shiftKey && document.activeElement === letztes) {
        e.preventDefault()
        erstes.focus()
      }
    }

    document.addEventListener('keydown', beiTaste)
    const zuFokussieren = blatt.current?.querySelector<HTMLElement>(FOKUSSIERBAR)
    zuFokussieren?.focus()

    return () => {
      document.removeEventListener('keydown', beiTaste)
      const zurueck = vorherigerFokus.current
      if (zurueck && zurueck.isConnected) zurueck.focus()
    }
  }, [offen, onSchliessen])

  if (!offen || typeof document === 'undefined') return null

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm animate-[fadeIn_.12s_ease-out]"
      onClick={onSchliessen}
      role="dialog"
      aria-modal="true"
      aria-label={titel}
    >
      <div
        ref={blatt}
        onClick={(e) => e.stopPropagation()}
        className={`w-full sm:max-w-sm bg-surface-container-low border-t sm:border border-outline-variant/30 rounded-t-3xl sm:rounded-2xl shadow-2xl
          pb-[env(safe-area-inset-bottom)] sm:pb-0 max-h-[80dvh] overflow-y-auto
          animate-[slideUp_.16s_ease-out] sm:animate-[fadeIn_.12s_ease-out] ${className}`}
      >
        {/* Der Griff. Reine Anzeige: er sagt „das hier lässt sich wegwischen“,
            und ohne ihn sieht ein Blatt von unten aus wie ein Fehler. */}
        <div className="sm:hidden flex justify-center pt-2.5 pb-1" aria-hidden="true">
          <span className="w-10 h-1 rounded-full bg-on-surface-variant/30" />
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

export interface BlatteintragProps {
  icon?: React.ReactNode
  label: string
  /** Zweite Zeile, für Einschränkungen und Hinweise. */
  hinweis?: string
  onClick: () => void
  /** Rot statt neutral — für Löschen und Ähnliches. */
  gefahr?: boolean
  disabled?: boolean
}

/** Eine Zeile im Blatt. 48 px hoch, damit sie der Daumen sicher trifft. */
export function Blatteintrag({ icon, label, hinweis, onClick, gefahr, disabled }: BlatteintragProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`w-full min-h-12 px-4 py-2.5 flex items-center gap-3 text-left text-sm transition-colors
        disabled:opacity-40 disabled:cursor-not-allowed
        ${gefahr ? 'text-destructive hover:bg-destructive/10' : 'text-on-surface hover:bg-surface-container-high'}`}
    >
      {icon && <span className="shrink-0 w-5 h-5 flex items-center justify-center">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate">{label}</span>
        {hinweis && <span className="block text-[11px] text-on-surface-variant truncate">{hinweis}</span>}
      </span>
    </button>
  )
}
