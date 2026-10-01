/**
 * Lichtbox: ein Bild oder Video im Vollbild, mit Blättern, Zoom und
 * Info-Leiste.
 *
 * Die Lichtbox zeigt nur an. Was angezeigt wird (Bild, Video, Ladeanzeige),
 * gibt der Aufrufer als `children`, ebenso die Aktionen in der Kopfleiste und
 * den Inhalt der Info-Leiste. Geblättert wird mit den Pfeiltasten, den
 * Knöpfen am Rand oder per Wischen; gezoomt per Doppelklick oder Mausrad, und
 * gezoomt verschiebt Ziehen den Ausschnitt statt zu blättern.
 *
 * Sie liegt per Portal an `body`, damit kein Stapelkontext sie abschneidet.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useZurueckSchliesst } from '@/hooks/useZurueckSchliesst'

export interface LichtboxProps {
  /** Kennung des gezeigten Elements; wechselt sie, springt der Zoom zurück. */
  kennung: string
  titel: string
  untertitel?: string
  /** Position in der Folge, z. B. 3 von 120. */
  position?: { index: number; anzahl: number }
  onSchliessen: () => void
  onVor?: () => void
  onZurueck?: () => void
  /** Knöpfe in der Kopfleiste, links vom Info- und Schließen-Knopf. */
  aktionen?: React.ReactNode
  /** Inhalt der Info-Leiste; ohne ihn gibt es keinen Info-Knopf. */
  info?: React.ReactNode
  /** Ob der Inhalt gezoomt werden darf (Bilder ja, Videos nein). */
  zoombar?: boolean
  /**
   * Der Inhalt füllt die Fläche und scrollt selbst (PDF, Listen, Tabellen).
   * Touch scrollt dann nativ; Wischen zum Blättern bleibt, solange der Inhalt
   * nicht seitlich scrollt.
   */
  rollbar?: boolean
  /** Wird beim ersten Hineinzoomen gerufen, etwa um das Original nachzuladen. */
  onZoom?: () => void
  children: React.ReactNode
}

const FOKUSSIERBAR = 'button:not([disabled]), a[href], video[controls], [tabindex]:not([tabindex="-1"])'
const WISCHEN_AB = 60
const ZOOM_MAX = 5
const ZOOM_DOPPEL = 2.5

export function Lichtbox({
  kennung,
  titel,
  untertitel,
  position,
  onSchliessen,
  onVor,
  onZurueck,
  aktionen,
  info,
  zoombar = false,
  rollbar = false,
  onZoom,
  children,
}: LichtboxProps) {
  const { t } = useTranslation()
  useZurueckSchliesst(true, onSchliessen)
  const rahmen = useRef<HTMLDivElement>(null)
  const vorherFokus = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )
  const [infoOffen, setInfoOffen] = useState(false)
  const [zoom, setZoom] = useState({ stufe: 1, x: 0, y: 0 })
  const zug = useRef<{ x: number; y: number; startX: number; startY: number; id: number } | null>(null)
  const gezoomtGemeldet = useRef(false)

  useEffect(() => {
    setZoom({ stufe: 1, x: 0, y: 0 })
    gezoomtGemeldet.current = false
  }, [kennung])

  const zoomSetzen = useCallback(
    (stufe: number, x = 0, y = 0) => {
      const s = Math.min(ZOOM_MAX, Math.max(1, stufe))
      setZoom(s === 1 ? { stufe: 1, x: 0, y: 0 } : { stufe: s, x, y })
      if (s > 1 && !gezoomtGemeldet.current) {
        gezoomtGemeldet.current = true
        onZoom?.()
      }
    },
    [onZoom],
  )

  useEffect(() => {
    const taste = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSchliessen()
      } else if (e.key === 'ArrowRight' && onVor && !(e.target instanceof HTMLVideoElement)) {
        e.preventDefault()
        onVor()
      } else if (e.key === 'ArrowLeft' && onZurueck && !(e.target instanceof HTMLVideoElement)) {
        e.preventDefault()
        onZurueck()
      } else if (e.key === 'Tab' && rahmen.current) {
        const ziele = Array.from(rahmen.current.querySelectorAll<HTMLElement>(FOKUSSIERBAR))
        if (ziele.length === 0) return
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
    }
    document.addEventListener('keydown', taste)
    return () => document.removeEventListener('keydown', taste)
  }, [onSchliessen, onVor, onZurueck])

  useEffect(() => {
    rahmen.current?.querySelector<HTMLElement>('[data-lichtbox-schliessen]')?.focus()
    const vorher = vorherFokus.current
    return () => {
      if (vorher?.isConnected) vorher.focus()
    }
  }, [])

  const zeigerRunter = (e: React.PointerEvent) => {
    // Die Bedienleiste eines Videos (Spulen) ist kein Wischen.
    if (e.button !== 0 || e.target instanceof HTMLVideoElement || e.target instanceof HTMLAudioElement) return
    zug.current = { x: e.clientX, y: e.clientY, startX: zoom.x, startY: zoom.y, id: e.pointerId }
  }

  const zeigerBewegt = (e: React.PointerEvent) => {
    const z = zug.current
    if (!z || z.id !== e.pointerId || zoom.stufe === 1) return
    setZoom((alt) => ({ ...alt, x: z.startX + e.clientX - z.x, y: z.startY + e.clientY - z.y }))
  }

  const zeigerHoch = (e: React.PointerEvent) => {
    const z = zug.current
    zug.current = null
    if (!z || z.id !== e.pointerId || zoom.stufe > 1) return
    const dx = e.clientX - z.x
    const dy = e.clientY - z.y
    if (Math.abs(dx) < WISCHEN_AB || Math.abs(dy) > Math.abs(dx)) return
    if (dx < 0) onVor?.()
    else onZurueck?.()
  }

  const rad = (e: React.WheelEvent) => {
    if (!zoombar) return
    zoomSetzen(zoom.stufe * (e.deltaY < 0 ? 1.2 : 1 / 1.2), zoom.x, zoom.y)
  }

  const randKnopf = 'text-white/85 hover:bg-white/10 hover:text-white'

  return createPortal(
    <div
      ref={rahmen}
      role="dialog"
      aria-modal="true"
      aria-label={titel}
      // Per Portal an body, also ohne die Ränder der App: Status- und Gestenleiste hält sie selbst frei.
      // Rollbarer Inhalt (Dokumente) ist hell und lückenhaft; dahinter darf die App nicht durchscheinen.
      className={`fixed inset-0 z-50 flex flex-col pt-[env(safe-area-inset-top,0px)] pb-[env(safe-area-inset-bottom,0px)] text-white animate-fade-in ${rollbar ? 'bg-black' : 'bg-black/95'}`}
    >
      <header className="flex items-center gap-2 px-3 py-2 sm:px-4">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{titel}</p>
          {(untertitel || position) && (
            <p className="truncate text-label-sm text-white/60">
              {[untertitel, position ? t('common.lichtbox.position', { index: position.index + 1, anzahl: position.anzahl }) : null]
                .filter(Boolean)
                .join(' · ')}
            </p>
          )}
        </div>
        {aktionen}
        {info && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={`${randKnopf} ${infoOffen ? 'bg-white/15' : ''}`}
            aria-label={t('common.lichtbox.info')}
            aria-pressed={infoOffen}
            onClick={() => setInfoOffen((o) => !o)}
          >
            <Info className="h-4 w-4" />
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={randKnopf}
          aria-label={t('common.close')}
          data-lichtbox-schliessen
          onClick={onSchliessen}
        >
          <X className="h-5 w-5" />
        </Button>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <div
          className={
            rollbar
              ? 'relative flex min-w-0 flex-1 overflow-hidden'
              : 'relative flex min-w-0 flex-1 touch-none select-none items-center justify-center overflow-hidden'
          }
          onPointerDown={zeigerRunter}
          onPointerMove={zeigerBewegt}
          onPointerUp={zeigerHoch}
          onPointerCancel={() => (zug.current = null)}
          onDoubleClick={() => zoombar && zoomSetzen(zoom.stufe > 1 ? 1 : ZOOM_DOPPEL)}
          onWheel={rad}
        >
          {rollbar ? (
            <div className="h-full w-full">{children}</div>
          ) : (
            <div
              className="flex h-full w-full items-center justify-center p-2 transition-transform duration-100 sm:p-6"
              style={{ transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.stufe})` }}
            >
              {children}
            </div>
          )}
          {onZurueck && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={`absolute left-2 top-1/2 hidden -translate-y-1/2 sm:inline-flex ${randKnopf}`}
              aria-label={t('common.lichtbox.zurueck')}
              onClick={onZurueck}
            >
              <ChevronLeft className="h-6 w-6" />
            </Button>
          )}
          {onVor && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={`absolute right-2 top-1/2 hidden -translate-y-1/2 sm:inline-flex ${randKnopf}`}
              aria-label={t('common.lichtbox.vor')}
              onClick={onVor}
            >
              <ChevronRight className="h-6 w-6" />
            </Button>
          )}
        </div>
        {info && infoOffen && (
          <aside
            aria-label={t('common.lichtbox.info')}
            className="absolute inset-x-0 bottom-0 max-h-[50%] overflow-y-auto border-t border-white/10 bg-black/85 p-4 text-sm backdrop-blur sm:static sm:max-h-none sm:w-80 sm:border-l sm:border-t-0"
          >
            {info}
          </aside>
        )}
      </div>
    </div>,
    document.body,
  )
}
