import type React from 'react'
import type { Hintergrund } from '../services/einstellungenStore'

export interface BildHintergrund {
  id: string
  pfad: string
  titelKey: string
}

export const BILD_HINTERGRUENDE: BildHintergrund[] = [
  { id: 'erde', pfad: '/hintergruende/erde.webp', titelKey: 'browser.hintergrund.erde' },
  { id: 'sonnensystem', pfad: '/hintergruende/sonnensystem.webp', titelKey: 'browser.hintergrund.sonnensystem' },
  { id: 'bergwald', pfad: '/hintergruende/bergwald.webp', titelKey: 'browser.hintergrund.bergwald' },
  { id: 'ozean', pfad: '/hintergruende/ozean.webp', titelKey: 'browser.hintergrund.ozean' },
  { id: 'feld', pfad: '/hintergruende/feld.webp', titelKey: 'browser.hintergrund.feld' },
]

export const FARB_HINTERGRUENDE: Record<'schlicht' | 'nacht' | 'wald' | 'tiefsee', string> = {
  schlicht: 'none',
  nacht: 'linear-gradient(135deg, #090a1a 0%, #17153b 50%, #0c0d1b 100%)',
  wald: 'linear-gradient(135deg, #021a14 0%, #063c2c 50%, #03120e 100%)',
  tiefsee: 'linear-gradient(135deg, #050b14 0%, #0d2137 50%, #030712 100%)',
}
export const HINTERGRUENDE = FARB_HINTERGRUENDE


/** Alle waehlbaren Hintergrund-Schluessel in der Reihenfolge fuer die UI. */
export const HINTERGRUND_OPTIONEN: Hintergrund[] = [
  'wechsel',
  'erde',
  'sonnensystem',
  'bergwald',
  'ozean',
  'feld',
  'schlicht',
  'nacht',
  'wald',
  'tiefsee',
]

/** Liefert das alle 30 Minuten wechselnde Bild. */
export function aktuellesWechselBild(zeit: number = Date.now()): BildHintergrund {
  const intervall = 30 * 60 * 1000
  const index = Math.floor(zeit / intervall) % BILD_HINTERGRUENDE.length
  return BILD_HINTERGRUENDE[index]
}

/** Ermittelt den CSS-Hintergrundstil (Gradient oder Bild-URL). */
export function hintergrundStil(
  hintergrund: Hintergrund,
  eigenesBild: string | null,
  zeit: number = Date.now(),
): React.CSSProperties {
  if (hintergrund === 'eigen' && eigenesBild) {
    return { backgroundImage: `url("${eigenesBild}")`, backgroundSize: 'cover', backgroundPosition: 'center' }
  }
  if (hintergrund === 'wechsel') {
    const bild = aktuellesWechselBild(zeit)
    return { backgroundImage: `url("${bild.pfad}")`, backgroundSize: 'cover', backgroundPosition: 'center' }
  }
  const festesBild = BILD_HINTERGRUENDE.find((b) => b.id === hintergrund)
  if (festesBild) {
    return { backgroundImage: `url("${festesBild.pfad}")`, backgroundSize: 'cover', backgroundPosition: 'center' }
  }
  const verlauf = FARB_HINTERGRUENDE[hintergrund as keyof typeof FARB_HINTERGRUENDE] ?? 'none'
  return verlauf === 'none' ? {} : { background: verlauf }
}

/**
 * Konvertiert ein hochgeladenes Bild (PNG, JPG, etc.) clientseitig
 * direkt in verlustarmes WebP mit 85 % Qualitaet fuer minimale Speichergroesse.
 */
export function bildZuWebp(datei: File, qualitaet = 0.85): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const blobUrl = URL.createObjectURL(datei)
    img.onload = () => {
      URL.revokeObjectURL(blobUrl)
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Canvas Context nicht verfuegbar'))
        return
      }
      ctx.drawImage(img, 0, 0)
      resolve(canvas.toDataURL('image/webp', qualitaet))
    }
    img.onerror = () => {
      URL.revokeObjectURL(blobUrl)
      reject(new Error('Bild konnte nicht dekodiert werden'))
    }
    img.src = blobUrl
  })
}
