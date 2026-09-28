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

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreVertical } from 'lucide-react'

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

/**
 * Wie der Knopf aussieht, der das Blatt öffnet.
 *
 * Zwei Fassungen, weil er an zwei Orten steht: in einer Leiste mit anderen
 * Knöpfen (`leiste`) und frei über dem Chatbild des Messengers, wo er einen
 * eigenen Grund braucht, um lesbar zu bleiben (`schwebend`). Eine Fassung mit
 * durchgereichten Klassen ginge nicht — zwei Tailwind-Klassen derselben
 * Eigenschaft streiten sich nach Reihenfolge im Stylesheet, nicht nach
 * Reihenfolge im Attribut, und das Ergebnis wäre Zufall.
 */
export type BlattknopfVariante = 'leiste' | 'schwebend'

const KNOPF_KLASSEN: Record<BlattknopfVariante, string> = {
  // 44 px am Telefon, 36 px am Rechner: kleiner trifft der Daumen nicht mehr
  // zuverlässig, und ein Menü, das man dreimal antippen muss, ist keins.
  leiste:
    'h-11 w-11 sm:h-9 sm:w-9 rounded-lg border border-outline-variant/30 ' +
    'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60',
  schwebend:
    'h-11 w-11 sm:h-8 sm:w-8 rounded-md bg-surface-container-high/85 hover:bg-surface-container-high ' +
    'backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-sm',
}

export interface BlattknopfProps {
  /** Vorgelesen und als Tooltip am Knopf. */
  label: string
  /** Überschrift des Blatts; vorgelesen beim Öffnen. */
  titel: string
  /**
   * Die Zeilen. Als Funktion aufgerufen bekommt sie `schliessen` — jede Zeile
   * schliesst das Blatt selbst, bevor sie ihr Fenster öffnet.
   */
  children: React.ReactNode | ((schliessen: () => void) => React.ReactNode)
  /** Sichtbare Zeile über den Einträgen, z. B. der Name des Chats. */
  ueberschrift?: string
  /** Ein anderes Zeichen als die drei Punkte. */
  icon?: React.ReactNode
  variante?: BlattknopfVariante
  disabled?: boolean
  /** Nur Lage und Abstand — nie Grösse oder Farbe, dafür ist `variante` da. */
  className?: string
}

/**
 * Der Knopf mit seinem Blatt: ein Bauteil statt zweier Einbauten.
 *
 * Vorher baute jede Fläche das selbst — Zustand hier, Auslöser dort, Blatt
 * ganz woanders. Wer eine Zeile hinzufügen wollte, musste drei Stellen finden.
 * Jetzt steht beides beieinander, und weil `Blattmenue` an `document.body`
 * zeichnet, darf der Knopf stehen, wo er hingehört.
 */
export function Blattknopf({
  label,
  titel,
  children,
  ueberschrift,
  icon,
  variante = 'leiste',
  disabled,
  className = '',
}: BlattknopfProps) {
  const [offen, setzeOffen] = useState(false)
  const schliessen = useCallback(() => setzeOffen(false), [])

  return (
    <>
      <button
        type="button"
        onClick={() => setzeOffen(true)}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={offen}
        aria-label={label}
        title={label}
        className={`inline-flex shrink-0 items-center justify-center transition-colors
          disabled:opacity-40 disabled:cursor-not-allowed ${KNOPF_KLASSEN[variante]} ${className}`}
      >
        {icon ?? <MoreVertical className="w-4 h-4" aria-hidden="true" />}
      </button>

      <Blattmenue offen={offen} onSchliessen={schliessen} titel={titel}>
        {ueberschrift && (
          <div className="px-4 pt-2 pb-1 text-xs font-semibold text-on-surface-variant truncate">
            {ueberschrift}
          </div>
        )}
        <div className="pb-2">
          {typeof children === 'function' ? children(schliessen) : children}
        </div>
      </Blattmenue>
    </>
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
        ${gefahr ? 'text-status-destructive hover:bg-status-destructive/10' : 'text-on-surface hover:bg-surface-container-high'}`}
    >
      {icon && <span className="shrink-0 w-5 h-5 flex items-center justify-center">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate">{label}</span>
        {hinweis && <span className="block text-label-sm text-on-surface-variant truncate">{hinweis}</span>}
      </span>
    </button>
  )
}
