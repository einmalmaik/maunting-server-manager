/**
 * Die Mitte der Regionsanalyse ohne MapTiler: das Kartenbild, groß — und die
 * Kamera bewegt seinen Ausschnitt.
 *
 * Jeder Kamerabefehl holt ein neues Kartenbild für den neuen Ausschnitt;
 * Hineinzoomen zeigt mehr Einzelheiten, nicht dasselbe Bild größer. Das
 * vorige Bild bleibt stehen, bis das neue geladen ist, dann blendet das neue
 * darüber ein. Die Bildform richtet sich nach der Fläche — hoch in der
 * schmalen Mitte des Desktops, quer auf einem breiten Schirm —, damit das Bild
 * sie füllt, ohne die Hälfte abzuschneiden.
 */
import { useEffect, useRef, useState } from 'react'
import { ImageOff, Layers, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis, AiSatelliteLayer } from '@/api/ai'
import { useBildquelle } from './Regionalbild'
import { bildformFuer, kameraAusschnitt, startAusschnitt, type Ausschnitt, type Bildform } from './kartenbildKamera'
import { kartenbildPfad } from './regionalAnalysis'

interface Stand {
  basis: string
  befehl?: string
  ausschnitt: Ausschnitt
}

/**
 * Der Ausschnitt, auf den die Kamera gerade blickt. Lebt in der Mitte selbst,
 * nicht in der Bühne: Die Bühne verschwindet, sobald MapTiler es noch einmal
 * versucht, und käme sonst beim alten Ausschnitt wieder.
 */
export function useKartenbildAusschnitt(
  basis: Ausschnitt | undefined,
  kamera: AiRegionalAnalysis['camera'],
  ort: AiRegionalAnalysis['coordinates'] | undefined,
): Ausschnitt | null {
  const [stand, setStand] = useState<Stand | null>(null)
  const schluessel = basis ? basis.join(',') : null
  const befehl = kamera?.command_id
  let jetzt = stand
  // Während des Renderns nachgeführt statt in einem Effekt: sonst ginge für
  // einen Takt der alte Ausschnitt als Anfrage hinaus.
  if (basis && schluessel !== stand?.basis) {
    jetzt = { basis: schluessel as string, befehl, ausschnitt: startAusschnitt(basis, kamera?.mode, ort) }
    setStand(jetzt)
  } else if (basis && stand && befehl && befehl !== stand.befehl) {
    jetzt = { ...stand, befehl, ausschnitt: kameraAusschnitt(stand.ausschnitt, basis, kamera, ort) }
    setStand(jetzt)
  }
  return basis && jetzt ? jetzt.ausschnitt : null
}

const UEBERBLENDUNG_MS = 600

interface KartenbildbuehneProps {
  layer: AiSatelliteLayer
  ausschnitt: Ausschnitt
  location: string
}

export function Kartenbildbuehne({ layer, ausschnitt, location }: KartenbildbuehneProps) {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const [form, setForm] = useState<Bildform | null>(null)
  const [gezeigt, setGezeigt] = useState<string | null>(null)
  const [vorher, setVorher] = useState<string | null>(null)
  const [kaputt, setKaputt] = useState<string | null>(null)

  useEffect(() => {
    const element = flaeche.current
    if (!element) return
    // Eine verborgene Fläche (Handy: anderer Reiter) misst 0 × 0 — dann
    // bleibt die Form, sonst käme beim Umschalten jedes Mal ein neues Bild.
    const messen = () => {
      if (element.clientWidth > 0 && element.clientHeight > 0) {
        setForm(bildformFuer(element.clientWidth, element.clientHeight))
      }
    }
    messen()
    if (typeof ResizeObserver === 'undefined') {
      // Ohne Beobachter käme eine verborgene Fläche nie zu ihrem Bild.
      setForm((jetzt) => jetzt ?? 'landscape')
      return
    }
    const beobachter = new ResizeObserver(messen)
    beobachter.observe(element)
    return () => beobachter.disconnect()
  }, [])

  // Das vorige Bild bleibt nur, solange das neue darüber einblendet.
  useEffect(() => {
    if (!vorher) return
    const zeit = window.setTimeout(() => setVorher(null), UEBERBLENDUNG_MS)
    return () => window.clearTimeout(zeit)
  }, [vorher])

  // Erst gemessen, dann geholt: sonst käme das Bild zweimal, quer und hoch.
  const pfad = form ? kartenbildPfad(ausschnitt, form) : null
  // Zurück zum Bild, das gerade ausblendet: Es ist längst geladen und meldet
  // kein `load` mehr. Ohne diesen Tausch bliebe das neue vorn stehen und die
  // Mitte lüde ewig.
  if (pfad !== null && pfad === vorher && pfad !== gezeigt) {
    setVorher(gezeigt)
    setGezeigt(pfad)
  }
  const schichten = [...new Set([vorher, gezeigt, pfad])].filter((p): p is string => p !== null)
  const fehlt = pfad !== null && pfad === kaputt
  const laedt = pfad !== null && pfad !== gezeigt && !fehlt

  return (
    <div ref={flaeche} className="absolute inset-0 z-[5] overflow-hidden" aria-busy={laedt}>
      {schichten.map((schicht) => (
        <Bildschicht
          key={schicht}
          pfad={schicht}
          alt={schicht === gezeigt ? t('ai.geo.image.alt', { location }) : ''}
          sichtbar={schicht === gezeigt || schicht === vorher}
          onGeladen={() => {
            // Ein Ausschnitt, der einmal scheiterte, kann beim nächsten
            // Anlauf kommen — dann gilt er nicht mehr als kaputt.
            setKaputt((jetzt) => (jetzt === schicht ? null : jetzt))
            if (schicht === gezeigt) return
            setVorher(gezeigt)
            setGezeigt(schicht)
          }}
          onFehler={() => setKaputt(schicht)}
        />
      ))}

      {fehlt && !gezeigt && (
        <div className="absolute inset-0 grid place-items-center p-6">
          <div className="max-w-sm rounded-2xl border border-outline-variant/30 bg-surface-container-low/95 p-5 text-center shadow-lg backdrop-blur-md">
            <ImageOff className="mx-auto h-6 w-6 text-primary" aria-hidden="true" />
            <p className="mt-3 text-sm font-semibold text-on-surface">{t('ai.geo.image.unavailable')}</p>
          </div>
        </div>
      )}

      {(laedt || (fehlt && gezeigt)) && (
        <div className="pointer-events-none absolute right-3 top-3 z-10 rounded-xl border border-outline-variant/30 bg-surface-container-low/90 px-2.5 py-2 text-label-sm text-on-surface-variant shadow-sm backdrop-blur-md">
          <span className="flex items-center gap-1.5" role="status">
            {laedt
              ? <Loader2 className="h-3.5 w-3.5 text-primary motion-safe:animate-spin" aria-hidden="true" />
              : <ImageOff className="h-3.5 w-3.5 text-primary" aria-hidden="true" />}
            {laedt ? t('ai.geo.image.loading') : t('ai.geo.image.unavailable')}
          </span>
        </div>
      )}

      <div className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[min(22rem,calc(100%-1.5rem))] rounded-xl border border-outline-variant/30 bg-surface-container-low/90 px-3 py-2 text-label-sm shadow-sm backdrop-blur-md">
        <div className="flex items-center gap-1.5 font-medium text-on-surface">
          <Layers className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          {t('ai.geo.image.map')}
        </div>
        <p className="mt-0.5 text-on-surface-variant">{t('ai.geo.image.mosaic')}</p>
        {layer.attribution && (
          <p className="mt-1 text-label-sm leading-tight text-on-surface-variant">
            {t('ai.geo.image.source', { source: layer.attribution })}
          </p>
        )}
      </div>
    </div>
  )
}

interface BildschichtProps {
  pfad: string
  alt: string
  sichtbar: boolean
  onGeladen: () => void
  onFehler: () => void
}

function Bildschicht({ pfad, alt, sichtbar, onGeladen, onFehler }: BildschichtProps) {
  const { quelle, fehlgeschlagen, bildFehler } = useBildquelle(pfad)

  useEffect(() => {
    if (fehlgeschlagen) onFehler()
  }, [fehlgeschlagen, onFehler])

  if (!quelle) return null
  return (
    <img
      src={quelle}
      alt={alt}
      aria-hidden={alt ? undefined : true}
      onLoad={onGeladen}
      onError={bildFehler}
      className={`absolute inset-0 h-full w-full object-cover motion-safe:transition-opacity motion-safe:duration-500 ${
        sichtbar ? 'opacity-100' : 'opacity-0'
      }`}
    />
  )
}
