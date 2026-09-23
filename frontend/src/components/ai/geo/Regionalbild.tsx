/**
 * Das Bild einer Regionsanalyse: die neueste Sentinel-2-Szene oder das
 * Kartenbild — mit dem, was es ist, und wem es gehört.
 *
 * Ein Kartenbild ist ein Mosaik aus vielen Aufnahmen. Es steht deshalb als
 * „Mosaik, kein Überflug" da und nie mit einem Datum; eine Szene zeigt ihren
 * Aufnahmezeitpunkt und die Bewölkung.
 *
 * Das Panel holt das Bild (`/api/ai/geo/image`), der Browser fragt nie ArcGIS
 * oder Copernicus selbst. Im Web lädt ein gewöhnliches `<img>` von der eigenen
 * Herkunft, mit dem Sitzungscookie. In der Desktop-App und bei getrenntem
 * Frontend trägt ein `<img>` kein Bearer-Token — dort kommt das Bild über
 * `apiStream` als Blob. Nur dort: die CSP des Panels erlaubt `blob:` nicht.
 */
import { useEffect, useState } from 'react'
import { ImageOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiSatelliteLayer } from '@/api/ai'
import { apiStream } from '@/api/client'
import { apiUrl, getIsAbsoluteApi } from '@/config/api'
import { regionalbildPfad } from './regionalAnalysis'

function brauchtBlob(): boolean {
  if (getIsAbsoluteApi()) return true
  return typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)
}

interface Bildquelle {
  /** Was im `src` des `<img>` steht; `null`, solange nichts zu laden ist. */
  quelle: string | null
  /** Endgültig: Das Panel hat für diesen Pfad kein Bild. */
  fehlgeschlagen: boolean
  /** Gehört an `onError` des `<img>`. */
  bildFehler: () => void
}

interface Blobstand {
  pfad: string
  quelle: string | null
  fehlgeschlagen: boolean
}

interface Nachfrage {
  pfad: string
  stand: 'fragt' | 'wieder' | 'fehlt'
}

/** Ein zweiter Anlauf braucht eine neue Adresse; dieselbe hielte der Browser für erledigt. */
const zweiterAnlauf = (pfad: string) => `${pfad}${pfad.includes('?') ? '&' : '?'}versuch=2`

/**
 * Die Quelle eines Panelbilds für ein `<img>`. Ein Blob gehört zu seinem Pfad
 * und wird freigegeben, sobald der Pfad wechselt oder das Bild verschwindet.
 *
 * Im Web trägt das `<img>` das Sitzungscookie, und das lebt 15 Minuten. Nach
 * einer langen Sprachsitzung ohne andere Anfrage bekommt es ein 401, und ein
 * `<img>` frischt keine Sitzung auf. Ein Fehler fragt deshalb einmal über
 * `apiStream` nach — das frischt sie auf — und lädt dann neu; erst wenn auch
 * das scheitert, fehlt das Bild.
 */
export function useBildquelle(pfad: string | null): Bildquelle {
  const direkt = !brauchtBlob()
  const [blob, setBlob] = useState<Blobstand | null>(null)
  const [nachfrage, setNachfrage] = useState<Nachfrage | null>(null)

  useEffect(() => {
    if (!pfad || direkt) return
    let aktiv = true
    let blobUrl: string | null = null
    const abbruch = new AbortController()
    void apiStream(pfad, { method: 'GET', headers: { Accept: 'image/*' }, signal: abbruch.signal })
      .then(async (antwort) => {
        if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`)
        const inhalt = await antwort.blob()
        if (!aktiv) return
        blobUrl = URL.createObjectURL(inhalt)
        setBlob({ pfad, quelle: blobUrl, fehlgeschlagen: false })
      })
      .catch(() => {
        if (aktiv) setBlob({ pfad, quelle: null, fehlgeschlagen: true })
      })
    return () => {
      aktiv = false
      abbruch.abort()
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [pfad, direkt])

  const bildFehler = () => {
    if (!pfad) return
    if (!direkt) {
      // Ein Blob, den das `<img>` nicht lesen kann, wird beim nächsten Mal
      // nicht besser.
      setBlob((jetzt) => (jetzt?.pfad === pfad ? { ...jetzt, quelle: null, fehlgeschlagen: true } : jetzt))
      return
    }
    if (nachfrage?.pfad === pfad) {
      if (nachfrage.stand === 'wieder') setNachfrage({ pfad, stand: 'fehlt' })
      return
    }
    setNachfrage({ pfad, stand: 'fragt' })
    void Promise.resolve()
      .then(() => apiStream(pfad, { method: 'GET', headers: { Accept: 'image/*' } }))
      .then((antwort) => {
        // Nur die Auskunft zählt; das Bild selbst lädt gleich das `<img>`.
        void antwort.body?.cancel().catch(() => undefined)
        return antwort.ok
      })
      .catch(() => false)
      .then((da) => {
        setNachfrage((jetzt) => (jetzt?.pfad === pfad ? { pfad, stand: da ? 'wieder' : 'fehlt' } : jetzt))
      })
  }

  if (!pfad) return { quelle: null, fehlgeschlagen: true, bildFehler }
  if (direkt) {
    const stand = nachfrage?.pfad === pfad ? nachfrage.stand : null
    if (stand === 'fehlt') return { quelle: null, fehlgeschlagen: true, bildFehler }
    if (stand === 'fragt') return { quelle: null, fehlgeschlagen: false, bildFehler }
    return { quelle: apiUrl(stand === 'wieder' ? zweiterAnlauf(pfad) : pfad), fehlgeschlagen: false, bildFehler }
  }
  // Ein Blob für einen anderen Pfad gilt nicht mehr, auch nicht für einen Takt.
  return blob?.pfad === pfad
    ? { quelle: blob.quelle, fehlgeschlagen: blob.fehlgeschlagen, bildFehler }
    : { quelle: null, fehlgeschlagen: false, bildFehler }
}

interface RegionalbildProps {
  layer: AiSatelliteLayer
  location: string
  className?: string
}

type Ladezustand = 'laedt' | 'da' | 'fehlt'

export function Regionalbild({ layer, location, className = '' }: RegionalbildProps) {
  const { t, i18n } = useTranslation()
  const { quelle, fehlgeschlagen, bildFehler } = useBildquelle(regionalbildPfad(layer))
  const [geladen, setGeladen] = useState<string | null>(null)
  const zustand: Ladezustand = fehlgeschlagen ? 'fehlt' : quelle !== null && geladen === quelle ? 'da' : 'laedt'

  const szene = layer.kind === 'scene'
  const zeitpunkt = szene && layer.captured_at ? new Date(layer.captured_at) : null
  const datum = zeitpunkt && !Number.isNaN(zeitpunkt.getTime())
    ? zeitpunkt.toLocaleDateString(i18n.language, { day: 'numeric', month: 'long', year: 'numeric' })
    : null
  const titel = szene ? layer.mission || t('ai.geo.image.scene') : t('ai.geo.image.map')
  const angabe = szene
    ? [
        datum ? t('ai.geo.image.capturedOn', { date: datum }) : t('ai.geo.captureTimeUnknown'),
        typeof layer.cloud_cover_percent === 'number'
          ? t('ai.geo.image.clouds', { percent: Math.round(layer.cloud_cover_percent) })
          : null,
      ].filter(Boolean).join(', ')
    : t('ai.geo.image.mosaic')

  return (
    <figure
      className={`relative aspect-video overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest ${className}`}
      aria-busy={zustand === 'laedt'}
    >
      {quelle && zustand !== 'fehlt' && (
        <img
          src={quelle}
          alt={t('ai.geo.image.alt', { location })}
          onLoad={() => setGeladen(quelle)}
          onError={bildFehler}
          className={`h-full w-full object-cover motion-safe:transition-opacity motion-safe:duration-500 ${
            zustand === 'da' ? 'opacity-100' : 'opacity-0'
          }`}
        />
      )}
      {zustand === 'laedt' && (
        <div className="absolute inset-0 animate-pulse bg-surface-container-high/60" aria-hidden="true" />
      )}
      {zustand === 'fehlt' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center p-6 text-center text-on-surface-variant">
          <ImageOff className="h-7 w-7 text-primary/70" aria-hidden="true" />
          <p className="mt-2 text-xs font-medium text-on-surface">{t('ai.geo.image.unavailable')}</p>
        </div>
      )}
      {/* Untereinander statt nebeneinander: im schmalen Reiter bräche die
          Quelle sonst neben der Angabe in drei Zeilen. */}
      <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-surface-container-lowest/95 via-surface-container-lowest/75 to-transparent px-3 pb-2 pt-10 text-label-sm leading-snug">
        <span className="block font-semibold text-on-surface">{titel}</span>
        <span className="block text-on-surface-variant">{angabe}</span>
        {layer.attribution && (
          <span className="mt-0.5 block leading-tight text-on-surface-variant">
            {t('ai.geo.image.source', { source: layer.attribution })}
          </span>
        )}
      </figcaption>
    </figure>
  )
}
