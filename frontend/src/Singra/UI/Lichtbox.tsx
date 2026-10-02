/**
 * Lichtbox: ein Bild oder Video im Vollbild, mit Blättern, Zoom und
 * Info-Leiste.
 *
 * Die Lichtbox zeigt nur an. Was angezeigt wird (Bild, Video, Ladeanzeige),
 * gibt der Aufrufer als `children`, ebenso die Aktionen in der Kopfleiste und
 * den Inhalt der Info-Leiste. Geblättert wird mit den Pfeiltasten, den
 * Knöpfen am Rand oder per Wischen; gezoomt per Doppelklick, Mausrad, mit zwei
 * Fingern (der Punkt zwischen den Fingern bleibt unter ihnen) oder mit den
 * Tasten +, - und 0. Gezoomt verschiebt Ziehen den Ausschnitt statt zu blättern.
 *
 * Auf dem Telefon sind alle Knöpfe der Kopfleiste mindestens 44 px groß, auch
 * die, die der Aufrufer als `aktionen` mitgibt.
 *
 * Sie liegt per Portal an `body`, damit kein Stapelkontext sie abschneidet.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Info, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useZurueckSchliesst } from './useZurueckSchliesst'

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
/** Ein Schritt mit Mausrad oder Taste. */
const ZOOM_SCHRITT = 1.2

interface Zoom {
  stufe: number
  x: number
  y: number
}

/** Zwei Finger: Abstand und Bildpunkt (vom Mittelpunkt aus, ungezoomt) zu Beginn. */
interface Spreizen {
  abstand: number
  stufe: number
  punktX: number
  punktY: number
}

function abstandUndMitte(a: { x: number; y: number }, b: { x: number; y: number }) {
  return { abstand: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}

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
  const [zoom, setZoom] = useState<Zoom>({ stufe: 1, x: 0, y: 0 })
  // Zeigerereignisse kommen schneller, als React neu zeichnet: der Stand für Gesten liegt hier.
  const zoomJetzt = useRef(zoom)
  zoomJetzt.current = zoom
  // `wischen: false` nach dem Spreizen: der übrige Finger verschiebt, blättert aber nicht.
  const zug = useRef<{ x: number; y: number; startX: number; startY: number; id: number; wischen: boolean } | null>(null)
  const zeiger = useRef(new Map<number, { x: number; y: number }>())
  const spreizen = useRef<Spreizen | null>(null)
  const gezoomtGemeldet = useRef(false)

  useEffect(() => {
    const zurueck = { stufe: 1, x: 0, y: 0 }
    zoomJetzt.current = zurueck
    setZoom(zurueck)
    spreizen.current = null
    gezoomtGemeldet.current = false
  }, [kennung])

  const zoomSetzen = useCallback(
    (stufe: number, x = 0, y = 0) => {
      const s = Math.min(ZOOM_MAX, Math.max(1, stufe))
      const neu = s === 1 ? { stufe: 1, x: 0, y: 0 } : { stufe: s, x, y }
      zoomJetzt.current = neu
      setZoom(neu)
      if (s > 1 && !gezoomtGemeldet.current) {
        gezoomtGemeldet.current = true
        onZoom?.()
      }
    },
    [onZoom],
  )

  /** Zoomt um `faktor`; was in der Mitte der Fläche liegt, bleibt dort. */
  const zoomUm = useCallback(
    (faktor: number) => {
      const z = zoomJetzt.current
      const s = Math.min(ZOOM_MAX, Math.max(1, z.stufe * faktor))
      zoomSetzen(s, (z.x * s) / z.stufe, (z.y * s) / z.stufe)
    },
    [zoomSetzen],
  )

  useEffect(() => {
    const taste = (e: KeyboardEvent) => {
      // Wie `DialogContent`: Tasten gehören dem obersten offenen Dialog. Unter
      // einer Rückfrage blätterte ArrowRight bis 02.10.2026 weiter.
      const dialoge = document.querySelectorAll('[aria-modal="true"]')
      if (rahmen.current !== dialoge[dialoge.length - 1]) return
      const eingabe =
        e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || (e.target instanceof HTMLElement && e.target.isContentEditable)
      // Strg/Cmd mit + und - zoomt die Seite; das bleibt dem Browser.
      if (zoombar && !eingabe && !e.ctrlKey && !e.metaKey && !e.altKey && ['+', '=', '-', '0'].includes(e.key)) {
        e.preventDefault()
        if (e.key === '0') zoomSetzen(1)
        else zoomUm(e.key === '-' ? 1 / ZOOM_SCHRITT : ZOOM_SCHRITT)
      } else if (e.key === 'Escape') {
        // Ein Menü in der Kopfleiste hat Escape schon für sich genommen.
        if (e.defaultPrevented) return
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
  }, [onSchliessen, onVor, onZurueck, zoombar, zoomSetzen, zoomUm])

  useEffect(() => {
    rahmen.current?.querySelector<HTMLElement>('[data-lichtbox-schliessen]')?.focus()
    const vorher = vorherFokus.current
    return () => {
      if (vorher?.isConnected) vorher.focus()
    }
  }, [])

  /** Abstand der beiden Finger und ihre Mitte, gemessen vom Mittelpunkt der Fläche. */
  const zweiFinger = (flaeche: Element) => {
    const [a, b] = Array.from(zeiger.current.values())
    const box = flaeche.getBoundingClientRect()
    const m = abstandUndMitte(a, b)
    return { abstand: m.abstand, x: m.x - (box.left + box.width / 2), y: m.y - (box.top + box.height / 2) }
  }

  const zeigerRunter = (e: React.PointerEvent) => {
    // Die Bedienleiste eines Videos (Spulen) ist kein Wischen.
    if (e.button !== 0 || e.target instanceof HTMLVideoElement || e.target instanceof HTMLAudioElement) return
    // Ein erster Finger heißt: kein anderer liegt mehr auf (ein verpasstes Hochheben zählt nicht).
    if (e.isPrimary) zeiger.current.clear()
    zeiger.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (zeiger.current.size === 1) {
      const z = zoomJetzt.current
      zug.current = { x: e.clientX, y: e.clientY, startX: z.x, startY: z.y, id: e.pointerId, wischen: true }
      return
    }
    // Ein zweiter Finger beendet Wischen und Verschieben; mit zwei Fingern wird gezoomt.
    zug.current = null
    if (!zoombar || zeiger.current.size !== 2) return
    const m = zweiFinger(e.currentTarget)
    if (m.abstand < 1) return
    const z = zoomJetzt.current
    spreizen.current = { abstand: m.abstand, stufe: z.stufe, punktX: (m.x - z.x) / z.stufe, punktY: (m.y - z.y) / z.stufe }
  }

  const zeigerBewegt = (e: React.PointerEvent) => {
    if (!zeiger.current.has(e.pointerId)) return
    zeiger.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const s = spreizen.current
    if (s && zeiger.current.size === 2) {
      // Der Bildpunkt, der zu Beginn zwischen den Fingern lag, bleibt zwischen ihnen.
      const m = zweiFinger(e.currentTarget)
      const stufe = Math.min(ZOOM_MAX, Math.max(1, (s.stufe * m.abstand) / s.abstand))
      zoomSetzen(stufe, m.x - stufe * s.punktX, m.y - stufe * s.punktY)
      return
    }
    const z = zug.current
    if (!z || z.id !== e.pointerId || zoomJetzt.current.stufe === 1) return
    const neu = { ...zoomJetzt.current, x: z.startX + e.clientX - z.x, y: z.startY + e.clientY - z.y }
    zoomJetzt.current = neu
    setZoom(neu)
  }

  const zeigerHoch = (e: React.PointerEvent) => {
    zeiger.current.delete(e.pointerId)
    if (spreizen.current) {
      if (zeiger.current.size >= 2) return
      spreizen.current = null
      const rest = Array.from(zeiger.current.entries())[0]
      const z = zoomJetzt.current
      zug.current = rest ? { x: rest[1].x, y: rest[1].y, startX: z.x, startY: z.y, id: rest[0], wischen: false } : null
      return
    }
    const z = zug.current
    if (!z || z.id !== e.pointerId) return
    zug.current = null
    if (!z.wischen || zoomJetzt.current.stufe > 1) return
    const dx = e.clientX - z.x
    const dy = e.clientY - z.y
    if (Math.abs(dx) < WISCHEN_AB || Math.abs(dy) > Math.abs(dx)) return
    if (dx < 0) onVor?.()
    else onZurueck?.()
  }

  const zeigerAbbruch = (e: React.PointerEvent) => {
    zeiger.current.delete(e.pointerId)
    spreizen.current = null
    zug.current = null
  }

  const rad = (e: React.WheelEvent) => {
    if (!zoombar) return
    zoomUm(e.deltaY < 0 ? ZOOM_SCHRITT : 1 / ZOOM_SCHRITT)
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
      className={`fixed inset-0 z-50 flex flex-col pt-[env(safe-area-inset-top,0px)] pb-[var(--msm-unten-sicher)] text-white animate-fade-in ${rollbar ? 'bg-black' : 'bg-black/95'}`}
    >
      {/* min-h/min-w statt h/w: die Höhe aus `size` gewinnt sonst je nach Stylesheet gegen das className. */}
      {/* Deckend: unter 95 % Schwarz schien die Kopfzeile der App genau unter den Knöpfen durch (Emulator, 02.10.2026). */}
      <header className="relative z-10 flex items-center gap-2 bg-black px-3 py-2 sm:px-4 max-sm:[&_button]:min-h-11 max-sm:[&_button]:min-w-11">
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
          onPointerCancel={zeigerAbbruch}
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
