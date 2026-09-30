/**
 * Der EXIF-Leser der Tresor-Galerie: Aufnahmezeit und Kamera, sonst nichts.
 */

import { describe, expect, it } from 'vitest'
import { bildAngaben, exifLesen } from './tresorBilder'

/** Ein JPEG-Kopf mit EXIF: IFD0 (Hersteller, Modell, Zeiger), EXIF-IFD (DateTimeOriginal), GPS-Zeiger. */
function jpegMitExif(klein: boolean, hersteller: string, modell: string, zeit: string): Blob {
  const tiff: number[] = []
  const u16 = (v: number) => (klein ? [v & 0xff, v >> 8] : [v >> 8, v & 0xff])
  const u32 = (v: number) => (klein ? [v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, v >>> 24] : [v >>> 24, (v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff])
  const ascii = (s: string) => [...new TextEncoder().encode(s), 0]
  // Aufbau: Kopf (8) | IFD0 mit 4 Einträgen | EXIF-IFD mit 1 Eintrag | Texte
  const ifd0 = 8
  const ifd0Laenge = 2 + 4 * 12 + 4
  const exifIfd = ifd0 + ifd0Laenge
  const exifLaenge = 2 + 12 + 4
  let daten = exifIfd + exifLaenge
  const hBytes = ascii(hersteller)
  const mBytes = ascii(modell)
  const zBytes = ascii(zeit)
  const hPos = daten
  daten += hBytes.length
  const mPos = daten
  daten += mBytes.length
  const zPos = daten
  tiff.push(...(klein ? [0x49, 0x49] : [0x4d, 0x4d]), ...u16(42), ...u32(ifd0))
  tiff.push(...u16(4))
  tiff.push(...u16(0x010f), ...u16(2), ...u32(hBytes.length), ...u32(hPos))
  tiff.push(...u16(0x0110), ...u16(2), ...u32(mBytes.length), ...u32(mPos))
  tiff.push(...u16(0x8769), ...u16(4), ...u32(1), ...u32(exifIfd))
  tiff.push(...u16(0x8825), ...u16(4), ...u32(1), ...u32(0))
  tiff.push(...u32(0))
  tiff.push(...u16(1), ...u16(0x9003), ...u16(2), ...u32(zBytes.length), ...u32(zPos), ...u32(0))
  tiff.push(...hBytes, ...mBytes, ...zBytes)
  const app1 = [0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff]
  const laenge = app1.length + 2
  return new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe1, laenge >> 8, laenge & 0xff, ...app1, 0xff, 0xd9])], { type: 'image/jpeg' })
}

describe('EXIF', () => {
  it('liest Aufnahmezeit und Kamera, in beiden Bytereihenfolgen', async () => {
    for (const klein of [true, false]) {
      const exif = await exifLesen(jpegMitExif(klein, 'Google', 'Pixel 8 Pro', '2026:07:14 18:32:05'))
      expect(exif.kamera).toBe('Google Pixel 8 Pro')
      expect(exif.aufgenommen).toBe(Date.UTC(2026, 6, 14, 18, 32, 5))
    }
  })

  it('wiederholt den Hersteller nicht, wenn das Modell ihn schon nennt', async () => {
    const exif = await exifLesen(jpegMitExif(true, 'Canon', 'Canon EOS R6', '2020:01:02 03:04:05'))
    expect(exif.kamera).toBe('Canon EOS R6')
  })

  it('gibt bei kaputten oder fremden Daten nichts zurück, statt zu werfen', async () => {
    expect(await exifLesen(new Blob([new Uint8Array([1, 2, 3])]))).toEqual({})
    const kaputt = new Uint8Array(await jpegMitExif(true, 'X', 'Y', '2020:01:02 03:04:05').arrayBuffer()).slice(0, 30)
    expect(await exifLesen(new Blob([kaputt]))).toEqual({})
    expect(await bildAngaben(new Blob([kaputt], { type: 'image/jpeg' }))).toEqual({})
  })

  it('zieht keine Standortdaten heraus', async () => {
    const angaben = await bildAngaben(jpegMitExif(true, 'Google', 'Pixel 8', '2026:07:14 18:32:05'))
    expect(Object.keys(angaben).sort()).toEqual(['aufgenommen', 'kamera'])
  })
})
