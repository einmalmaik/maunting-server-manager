import { describe, expect, it } from 'vitest'
import { crc32, zipSchreiben } from './zipSchreiben'

const text = (s: string) => new TextEncoder().encode(s)

/** Liest ein Archiv so, wie ein Entpacker es tut: vom Ende über das Verzeichnis. */
function lies(puffer: ArrayBuffer): Map<string, string> {
  const sicht = new DataView(puffer)
  const ende = puffer.byteLength - 22
  expect(sicht.getUint32(ende, true)).toBe(0x06054b50)
  const anzahl = sicht.getUint16(ende + 10, true)
  let zeiger = sicht.getUint32(ende + 16, true)
  const dateien = new Map<string, string>()
  const dekodierer = new TextDecoder()
  for (let i = 0; i < anzahl; i++) {
    expect(sicht.getUint32(zeiger, true)).toBe(0x02014b50)
    const crc = sicht.getUint32(zeiger + 16, true)
    const groesse = sicht.getUint32(zeiger + 20, true)
    const namenslaenge = sicht.getUint16(zeiger + 28, true)
    const lokal = sicht.getUint32(zeiger + 42, true)
    const name = dekodierer.decode(new Uint8Array(puffer, zeiger + 46, namenslaenge))
    expect(sicht.getUint32(lokal, true)).toBe(0x04034b50)
    const start = lokal + 30 + sicht.getUint16(lokal + 26, true)
    const inhalt = new Uint8Array(puffer, start, groesse)
    expect(crc32(inhalt)).toBe(crc)
    dateien.set(name, dekodierer.decode(inhalt))
    zeiger += 46 + namenslaenge
  }
  return dateien
}

describe('zipSchreiben', () => {
  it('rechnet CRC32 wie zlib', () => {
    expect(crc32(text(''))).toBe(0)
    expect(crc32(text('123456789'))).toBe(0xcbf43926)
    expect(crc32(text('The quick brown fox jumps over the lazy dog'))).toBe(0x414fa339)
  })

  it('legt Dateien samt Umlauten im Namen lesbar ab', async () => {
    const blob = zipSchreiben([
      { pfad: 'LIESMICH.txt', inhalt: 'Hallo' },
      { pfad: 'server/notizen.json', inhalt: '[{"titel":"Größe"}]' },
      { pfad: 'dateien/bild.png', inhalt: new Uint8Array([0x89, 0x50, 0x4e, 0x47]) },
    ], new Date(2026, 8, 27, 12, 30, 10))
    const dateien = lies(await blob.arrayBuffer())
    expect([...dateien.keys()]).toEqual(['LIESMICH.txt', 'server/notizen.json', 'dateien/bild.png'])
    expect(dateien.get('server/notizen.json')).toBe('[{"titel":"Größe"}]')
    expect(blob.type).toBe('application/zip')
  })

  it('ein leeres Archiv ist nur das Ende', async () => {
    const blob = zipSchreiben([])
    expect(blob.size).toBe(22)
    expect(lies(await blob.arrayBuffer()).size).toBe(0)
  })
})
