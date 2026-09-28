/**
 * Wohin die Kamera ohne MapTiler blickt: auf einen Ausschnitt des Kartenbilds.
 *
 * Ohne MapTiler gibt es keine Karte, die fliegen könnte. Die Kamera bewegt
 * dann den Ausschnitt, und das Panel holt für ihn ein neues Kartenbild —
 * Hineinzoomen zeigt wirklich mehr Einzelheiten, nicht dasselbe Bild größer.
 * Die Grenzen sind die des Panels (`ai_geo_image_service.image_bbox`): nicht
 * schmaler als 0,01°, nicht größer als 60° × 40°, innerhalb von Web Mercator.
 */
import type { AiRegionalAnalysis } from '@/api/ai'

/** `[West, Süd, Ost, Nord]` in Grad, wie die Bounding-Box der Analyse. */
export type Ausschnitt = [number, number, number, number]
/** Die drei Bildformen, die das Panel holt. */
export type Bildform = 'landscape' | 'portrait' | 'square'

const MIN_SPANNE = 0.01
const MAX_SPANNE_LAENGE = 60
const MAX_SPANNE_BREITE = 40
const MAX_BREITENGRAD = 85
/** Ein Zoomschritt zeigt gut ein Drittel des vorigen Ausschnitts. */
export const ZOOMSCHRITT = 0.35
/** Um eine Sehenswürdigkeit bleibt Umgebung: 0,02°, in Berlin gut 1,4 × 2,2 km. */
const SEHENSWUERDIGKEIT = 0.02

/** Die Bildform, die eine Fläche am wenigsten beschneidet. */
export function bildformFuer(breite: number, hoehe: number): Bildform {
  if (!(breite > 0) || !(hoehe > 0)) return 'landscape'
  const verhaeltnis = breite / hoehe
  if (verhaeltnis >= 1.3) return 'landscape'
  if (verhaeltnis <= 0.77) return 'portrait'
  return 'square'
}

function passe(mitte: number, spanne: number, meiste: number, unten: number, oben: number): [number, number] {
  const breite = Math.min(meiste, Math.max(MIN_SPANNE, spanne))
  let tief = mitte - breite / 2
  let hoch = mitte + breite / 2
  // Verschieben statt abschneiden: der Ausschnitt behält seine Größe.
  if (tief < unten) {
    hoch = Math.min(oben, hoch + (unten - tief))
    tief = unten
  }
  if (hoch > oben) {
    tief = Math.max(unten, tief - (hoch - oben))
    hoch = oben
  }
  return [tief, hoch]
}

const runde = (wert: number) => Math.round(wert * 1e5) / 1e5

/** Ein Ausschnitt um einen Punkt, in die Grenzen des Panels gelegt. */
export function ausschnittUm(laenge: number, breite: number, spanneLaenge: number, spanneBreite: number): Ausschnitt {
  const [west, ost] = passe(laenge, spanneLaenge, MAX_SPANNE_LAENGE, -180, 180)
  const [sued, nord] = passe(
    Math.min(MAX_BREITENGRAD, Math.max(-MAX_BREITENGRAD, breite)),
    spanneBreite,
    MAX_SPANNE_BREITE,
    -MAX_BREITENGRAD,
    MAX_BREITENGRAD,
  )
  return [runde(west), runde(sued), runde(ost), runde(nord)]
}

type Kamera = AiRegionalAnalysis['camera']
type Ort = AiRegionalAnalysis['coordinates'] | undefined

/**
 * Worauf die Kamera blickt: auf den Ort, wie die Karte mit MapTiler — nicht
 * auf die Mitte seiner Box. Die liegt nicht immer beim Ort: Hamburgs Box
 * reicht bis ins Wattenmeer vor Neuwerk, ihre Mitte liegt gut 50 km
 * nordwestlich der Stadt; die von Tokio reicht bis zu den Ogasawara-Inseln,
 * ihre Mitte liegt rund 1.000 km weit im Pazifik.
 */
function blickpunkt([west, sued, ost, nord]: Ausschnitt, ort: Ort): [number, number] {
  if (ort && Number.isFinite(ort.longitude) && Number.isFinite(ort.latitude)) {
    return [ort.longitude, ort.latitude]
  }
  return [(west + ost) / 2, (sued + nord) / 2]
}

/** `faktor`-mal so weit, um den Ort. */
export function gezoomt(ausschnitt: Ausschnitt, faktor: number, ort?: Ort): Ausschnitt {
  const [laenge, breite] = blickpunkt(ausschnitt, ort)
  return ausschnittUm(
    laenge,
    breite,
    (ausschnitt[2] - ausschnitt[0]) * faktor,
    (ausschnitt[3] - ausschnitt[1]) * faktor,
  )
}

/**
 * Wo eine Analyse beginnt: `focus` zeigt die Box des Ortes wie das Bild im
 * Reiter, `detail` zwei Zoomschritte näher um den Ort, `overview` so weit, wie
 * ein Kartenbild reicht.
 */
export function startAusschnitt(basis: Ausschnitt, modus?: NonNullable<Kamera>['mode'], ort?: Ort): Ausschnitt {
  if (modus === 'overview') {
    const [laenge, breite] = blickpunkt(basis, ort)
    return ausschnittUm(laenge, breite, MAX_SPANNE_LAENGE, MAX_SPANNE_BREITE)
  }
  if (modus === 'detail') return gezoomt(basis, ZOOMSCHRITT * ZOOMSCHRITT, ort)
  return gezoomt(basis, 1)
}

/**
 * Der Ausschnitt nach einem Kamerabefehl. Ein Befehl ohne Aktion ist eine
 * neue Analyse und beginnt wieder bei ihrem Start.
 */
export function kameraAusschnitt(jetzt: Ausschnitt, basis: Ausschnitt, kamera: Kamera, ort: Ort): Ausschnitt {
  switch (kamera?.action) {
    case 'zoom_in':
      return gezoomt(jetzt, ZOOMSCHRITT, ort)
    case 'zoom_out':
      return gezoomt(jetzt, 1 / ZOOMSCHRITT, ort)
    case 'overview':
      return startAusschnitt(basis, 'overview', ort)
    case 'focus_location': {
      if (!ort) return jetzt
      const [west, sued, ost, nord] = ort.bbox
      return ausschnittUm(
        ort.longitude,
        ort.latitude,
        Math.max(SEHENSWUERDIGKEIT, ost - west),
        Math.max(SEHENSWUERDIGKEIT, nord - sued),
      )
    }
    default:
      return startAusschnitt(basis, kamera?.mode, ort)
  }
}
