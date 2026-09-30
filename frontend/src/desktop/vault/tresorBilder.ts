/**
 * Vorschaubilder und Aufnahmedaten für Dateien im Tresor, auf dem Gerät
 * erzeugt, bevor etwas verschlüsselt wird.
 *
 * Miniatur (für das Raster) und Vorschau (für die Lichtbox) sind WebP und
 * müssen in ihre festen Größenklassen passen; sonst sinkt die Qualität
 * schrittweise. Was sich nicht darstellen lässt (HEIC in Chromium, kaputte
 * Dateien), bekommt kein Bild: die Blobs bleiben leer, aber gleich groß.
 *
 * EXIF liest ein kleiner eigener Leser aus dem JPEG-Kopf, nur Aufnahmezeit
 * und Kamera. Standortdaten bleiben in der Originaldatei und werden nicht
 * herausgezogen: nichts hier braucht sie.
 */

import { MINIATUR_GROESSE, VORSCHAU_GROESSE } from './tresorDatei'

export interface BildAngaben {
  vorschau?: Uint8Array
  miniatur?: Uint8Array
  breite?: number
  hoehe?: number
  /** Videos: Länge in Sekunden. */
  dauer?: number
  /** Aufnahmezeit aus EXIF (ms seit 1970, Ortszeit der Kamera als UTC gelesen). */
  aufgenommen?: number
  /** Hersteller und Modell aus EXIF. */
  kamera?: string
}

const MINIATUR_KANTE = 256
const VORSCHAU_KANTE = 1600
const EXIF_LESEN = 256 * 1024

type Quelle = ImageBitmap | HTMLVideoElement

function kanten(quelle: Quelle): [number, number] {
  return quelle instanceof HTMLVideoElement ? [quelle.videoWidth, quelle.videoHeight] : [quelle.width, quelle.height]
}

async function alsWebp(quelle: Quelle, kante: number, grenze: number): Promise<Uint8Array | undefined> {
  const [b, h] = kanten(quelle)
  if (!b || !h) return undefined
  const faktor = Math.min(1, kante / Math.max(b, h))
  const leinwand = document.createElement('canvas')
  leinwand.width = Math.max(1, Math.round(b * faktor))
  leinwand.height = Math.max(1, Math.round(h * faktor))
  const g = leinwand.getContext('2d')
  if (!g) return undefined
  g.drawImage(quelle, 0, 0, leinwand.width, leinwand.height)
  for (const qualitaet of [0.82, 0.7, 0.55, 0.4, 0.25]) {
    const blob = await new Promise<Blob | null>((r) => leinwand.toBlob(r, 'image/webp', qualitaet))
    if (blob && blob.size <= grenze) return new Uint8Array(await blob.arrayBuffer())
  }
  return undefined
}

async function videoBild(datei: Blob): Promise<{ video: HTMLVideoElement; url: string } | null> {
  const url = URL.createObjectURL(datei)
  const video = document.createElement('video')
  video.muted = true
  video.preload = 'auto'
  video.src = url
  try {
    await new Promise<void>((ok, fehler) => {
      video.onloadedmetadata = () => ok()
      video.onerror = () => fehler(new Error('video'))
      setTimeout(() => fehler(new Error('zeit')), 10_000)
    })
    video.currentTime = Math.min(1, (video.duration || 0) / 10)
    await new Promise<void>((ok, fehler) => {
      video.onseeked = () => ok()
      setTimeout(() => fehler(new Error('zeit')), 10_000)
    })
    return { video, url }
  } catch {
    URL.revokeObjectURL(url)
    return null
  }
}

/** Erzeugt Miniatur und Vorschau und liest die Aufnahmedaten. Scheitert nie; im Zweifel fehlt etwas. */
export async function bildAngaben(datei: Blob): Promise<BildAngaben> {
  const angaben: BildAngaben = {}
  if (datei.type === 'image/jpeg') Object.assign(angaben, await exifLesen(datei).catch(() => ({})))
  if (typeof document === 'undefined') return angaben
  try {
    if (datei.type.startsWith('image/') && typeof createImageBitmap === 'function') {
      const bild = await createImageBitmap(datei, { imageOrientation: 'from-image' })
      try {
        angaben.breite = bild.width
        angaben.hoehe = bild.height
        angaben.vorschau = await alsWebp(bild, VORSCHAU_KANTE, VORSCHAU_GROESSE)
        angaben.miniatur = await alsWebp(bild, MINIATUR_KANTE, MINIATUR_GROESSE)
      } finally {
        bild.close()
      }
    } else if (datei.type.startsWith('video/')) {
      const bild = await videoBild(datei)
      if (bild) {
        try {
          angaben.breite = bild.video.videoWidth
          angaben.hoehe = bild.video.videoHeight
          angaben.dauer = Number.isFinite(bild.video.duration) ? bild.video.duration : undefined
          angaben.vorschau = await alsWebp(bild.video, VORSCHAU_KANTE, VORSCHAU_GROESSE)
          angaben.miniatur = await alsWebp(bild.video, MINIATUR_KANTE, MINIATUR_GROESSE)
        } finally {
          bild.video.removeAttribute('src')
          URL.revokeObjectURL(bild.url)
        }
      }
    }
  } catch {
    // Nicht darstellbar: keine Bilder.
  }
  return angaben
}

/** Aufnahmezeit und Kamera aus dem EXIF-Block eines JPEG. */
export async function exifLesen(datei: Blob): Promise<Pick<BildAngaben, 'aufgenommen' | 'kamera'>> {
  const daten = new DataView(await datei.slice(0, EXIF_LESEN).arrayBuffer())
  if (daten.byteLength < 4 || daten.getUint16(0) !== 0xffd8) return {}
  let pos = 2
  while (pos + 4 <= daten.byteLength) {
    const marke = daten.getUint16(pos)
    const laenge = daten.getUint16(pos + 2)
    if ((marke & 0xff00) !== 0xff00 || marke === 0xffda) break
    if (marke === 0xffe1 && pos + 10 <= daten.byteLength && daten.getUint32(pos + 4) === 0x45786966) {
      return tiffLesen(daten, pos + 10, Math.min(daten.byteLength, pos + 2 + laenge))
    }
    pos += 2 + laenge
  }
  return {}
}

function tiffLesen(d: DataView, start: number, ende: number): Pick<BildAngaben, 'aufgenommen' | 'kamera'> {
  if (start + 8 > ende) return {}
  const klein = d.getUint16(start) === 0x4949
  const u16 = (p: number) => d.getUint16(p, klein)
  const u32 = (p: number) => d.getUint32(p, klein)
  const text = (p: number, n: number) => {
    let s = ''
    for (let i = 0; i < n && p + i < ende; i++) {
      const c = d.getUint8(p + i)
      if (c === 0) break
      s += String.fromCharCode(c)
    }
    return s.trim()
  }
  /** Die ASCII-Einträge eines IFD, dazu der Zeiger auf den EXIF-IFD. */
  const ifd = (versatz: number) => {
    const felder = new Map<number, string>()
    let exif: number | undefined
    const p = start + versatz
    if (p + 2 > ende) return { felder, exif }
    const anzahl = u16(p)
    for (let i = 0; i < anzahl; i++) {
      const e = p + 2 + i * 12
      if (e + 12 > ende) break
      const tag = u16(e)
      const typ = u16(e + 2)
      const n = u32(e + 4)
      if (tag === 0x8769) exif = u32(e + 8)
      else if (typ === 2) felder.set(tag, text(n <= 4 ? e + 8 : start + u32(e + 8), n))
    }
    return { felder, exif }
  }
  const erster = ifd(u32(start + 4))
  const ergebnis: Pick<BildAngaben, 'aufgenommen' | 'kamera'> = {}
  const hersteller = erster.felder.get(0x010f) ?? ''
  const modell = erster.felder.get(0x0110) ?? ''
  const kamera = modell.toLowerCase().startsWith(hersteller.toLowerCase()) ? modell : `${hersteller} ${modell}`.trim()
  if (kamera) ergebnis.kamera = kamera.slice(0, 80)
  const zeit = erster.exif !== undefined ? ifd(erster.exif).felder.get(0x9003) : erster.felder.get(0x0132)
  const teile = zeit?.match(/^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/)
  if (teile) {
    const [, j, mo, t, h, mi, s] = teile.map(Number)
    const wert = Date.UTC(j, mo - 1, t, h, mi, s)
    if (Number.isFinite(wert) && j > 1970) ergebnis.aufgenommen = wert
  }
  return ergebnis
}
