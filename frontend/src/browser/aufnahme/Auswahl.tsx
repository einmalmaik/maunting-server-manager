/**
 * „Bereich auswählen“: das Bild der Seite liegt an ihrer Stelle, darüber ein
 * Fadenkreuz. Ziehen mit Maus oder Finger wählt den Bereich, Loslassen nimmt
 * ihn. „Alles“ oder Enter nimmt das ganze Bild, Escape und Zurück brechen ab.
 *
 * Die native Seite liegt über der Oberfläche (AGENTS.md Punkt 121); solange
 * die Auswahl offen ist, ist sie verdeckt (`aria-modal`).
 */
import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { useZurueckSchliesst } from '@/Singra/UI/useZurueckSchliesst'

import { useAufnahme } from './ablauf'

/** Kleiner als das gilt ein Ziehen als versehentlicher Klick. */
const MINDESTENS_PX = 6

type Punkt = { x: number; y: number }

export function Auswahl({ adresse }: { adresse: string }) {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const [start, setStart] = useState<Punkt | null>(null)
  const [ende, setEnde] = useState<Punkt | null>(null)
  const { auswaehlen, schliessen } = useAufnahme.getState()
  useZurueckSchliesst(true, schliessen)
  useEffect(() => flaeche.current?.focus(), [])

  const punkt = (e: PointerEvent): Punkt => {
    const r = flaeche.current!.getBoundingClientRect()
    return { x: Math.min(Math.max(e.clientX - r.left, 0), r.width), y: Math.min(Math.max(e.clientY - r.top, 0), r.height) }
  }
  const rechteck =
    start && ende
      ? { x: Math.min(start.x, ende.x), y: Math.min(start.y, ende.y), breite: Math.abs(ende.x - start.x), hoehe: Math.abs(ende.y - start.y) }
      : null

  const loslassen = () => {
    const r = flaeche.current?.getBoundingClientRect()
    setStart(null)
    setEnde(null)
    if (!r || !rechteck || rechteck.breite < MINDESTENS_PX || rechteck.hoehe < MINDESTENS_PX) return
    void auswaehlen({ x: rechteck.x / r.width, y: rechteck.y / r.height, breite: rechteck.breite / r.width, hoehe: rechteck.hoehe / r.height })
  }

  return (
    <div
      ref={flaeche}
      role="dialog"
      aria-modal="true"
      aria-label={t('browser.aufnahme.auswahlName')}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === 'Escape') schliessen()
        else if (e.key === 'Enter') void auswaehlen()
        else return
        e.preventDefault()
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.currentTarget.setPointerCapture(e.pointerId)
        setStart(punkt(e))
        setEnde(punkt(e))
      }}
      onPointerMove={(e) => start && setEnde(punkt(e))}
      onPointerUp={loslassen}
      onPointerCancel={() => {
        setStart(null)
        setEnde(null)
      }}
      className="absolute inset-0 z-20 cursor-crosshair touch-none select-none overflow-hidden outline-none"
    >
      <img src={adresse} alt="" draggable={false} className="pointer-events-none absolute inset-0 h-full w-full" />
      {rechteck ? (
        <div
          className="pointer-events-none absolute border-2 border-primary"
          style={{ left: rechteck.x, top: rechteck.y, width: rechteck.breite, height: rechteck.hoehe, boxShadow: '0 0 0 9999px rgb(0 0 0 / 0.45)' }}
        />
      ) : (
        <div className="pointer-events-none absolute inset-0 bg-black/45" />
      )}
      {!rechteck && (
        <div
          className="absolute left-1/2 top-3 flex max-w-[calc(100%-1.5rem)] -translate-x-1/2 cursor-default flex-wrap items-center justify-center gap-2 rounded-xl bg-surface-container-high px-3 py-2 shadow-lg"
          onPointerDown={(e) => e.stopPropagation()}
        >
          <p className="text-body-sm text-on-surface">{t('browser.aufnahme.auswahlHinweis')}</p>
          <Button size="sm" variant="secondary" onClick={() => void auswaehlen()}>
            {t('browser.aufnahme.alles')}
          </Button>
          <Button size="sm" variant="ghost" onClick={schliessen}>
            {t('browser.aufnahme.abbrechen')}
          </Button>
        </div>
      )}
    </div>
  )
}
