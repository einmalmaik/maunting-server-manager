/**
 * Bildarbeit für Screenshots, ganz in der Oberfläche: aus Base64 ein Blob,
 * zuschneiden, kopieren, Dateiname. Nichts davon geht ins Netz.
 */
import { seitenHost } from '../services/geraetKonfig'

/** Ein Ausschnitt in Anteilen des Bildes (0 bis 1); so stimmt er auf jedem Bildschirm. */
export interface Ausschnitt {
  x: number
  y: number
  breite: number
  hoehe: number
}

export function pngAusBase64(base64: string): Blob {
  const roh = atob(base64)
  const bytes = new Uint8Array(roh.length)
  for (let i = 0; i < roh.length; i++) bytes[i] = roh.charCodeAt(i)
  return new Blob([bytes], { type: 'image/png' })
}

export function base64AusBlob(blob: Blob): Promise<string> {
  return new Promise((fertig, fehler) => {
    const leser = new FileReader()
    leser.onload = () => fertig(String(leser.result).slice(String(leser.result).indexOf(',') + 1))
    leser.onerror = () => fehler(leser.error)
    leser.readAsDataURL(blob)
  })
}

/** Ganze Pixel im Bild, mindestens eines, nie über den Rand. */
export function pixel(a: Ausschnitt, breite: number, hoehe: number) {
  const x = Math.min(Math.max(Math.round(a.x * breite), 0), breite - 1)
  const y = Math.min(Math.max(Math.round(a.y * hoehe), 0), hoehe - 1)
  return {
    x,
    y,
    breite: Math.max(1, Math.min(Math.round(a.breite * breite), breite - x)),
    hoehe: Math.max(1, Math.min(Math.round(a.hoehe * hoehe), hoehe - y)),
  }
}

export async function zuschneiden(bild: Blob, a: Ausschnitt): Promise<Blob> {
  const quelle = await createImageBitmap(bild)
  try {
    const p = pixel(a, quelle.width, quelle.height)
    const flaeche = new OffscreenCanvas(p.breite, p.hoehe)
    flaeche.getContext('2d')!.drawImage(quelle, p.x, p.y, p.breite, p.hoehe, 0, 0, p.breite, p.hoehe)
    return await flaeche.convertToBlob({ type: 'image/png' })
  } finally {
    quelle.close()
  }
}

/** Kann diese WebView ein Bild in die Zwischenablage legen? */
export function kopierenMoeglich(): boolean {
  return typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write
}

export function kopieren(bild: Blob): Promise<void> {
  return navigator.clipboard.write([new ClipboardItem({ 'image/png': bild })])
}

/** „Screenshot example.org 2026-10-10 14-05-09.png“: Ortszeit, ohne Doppelpunkte (Windows). */
export function dateiname(url: string, wann: Date): string {
  const z = (n: number) => String(n).padStart(2, '0')
  const datum = `${wann.getFullYear()}-${z(wann.getMonth() + 1)}-${z(wann.getDate())}`
  const zeit = `${z(wann.getHours())}-${z(wann.getMinutes())}-${z(wann.getSeconds())}`
  const host = seitenHost(url)
  return `Screenshot ${host ? `${host} ` : ''}${datum} ${zeit}.png`
}
