// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { archivInhalt, tarInhalt, zipInhalt } from './zipLesen'
import { crc32, zipGrenzenPruefen, zipSchreiben, zipStrom, ZipZuGross } from './zipSchreiben'

const text = (s: string) => new TextEncoder().encode(s)

async function* stuecke(daten: Uint8Array, groesse: number) {
  for (let i = 0; i < daten.length; i += groesse) yield daten.slice(i, i + groesse)
}

async function sammeln(strom: AsyncIterable<Uint8Array>): Promise<Uint8Array> {
  const teile: Uint8Array[] = []
  for await (const t of strom) teile.push(t.slice())
  return new Uint8Array(await new Blob(teile as BlobPart[]).arrayBuffer())
}

/** Ein tar-Kopf mit Name, Größe und Art, Prüfsumme wie im Format. */
function tarKopf(name: string, groesse: number, art = '0', vorsatz = ''): Uint8Array {
  const kopf = new Uint8Array(512)
  kopf.set(text(name), 0)
  kopf.set(text(groesse.toString(8).padStart(11, '0')), 124)
  kopf[156] = art.charCodeAt(0)
  kopf.set(text('ustar\u000000'), 257)
  kopf.set(text(vorsatz), 345)
  kopf.set(text('        '), 148)
  const summe = kopf.reduce((a, b) => a + b, 0)
  kopf.set(text(summe.toString(8).padStart(6, '0') + '\u0000 '), 148)
  return kopf
}

function tar(eintraege: { name: string; inhalt?: Uint8Array; art?: string; vorsatz?: string }[]): Uint8Array {
  const teile: Uint8Array[] = []
  for (const e of eintraege) {
    const inhalt = e.inhalt ?? new Uint8Array(0)
    teile.push(tarKopf(e.name, inhalt.length, e.art, e.vorsatz))
    const block = new Uint8Array(Math.ceil(inhalt.length / 512) * 512)
    block.set(inhalt)
    teile.push(block)
  }
  teile.push(new Uint8Array(1024))
  const ganz = new Uint8Array(teile.reduce((a, t) => a + t.length, 0))
  let p = 0
  for (const t of teile) {
    ganz.set(t, p)
    p += t.length
  }
  return ganz
}

describe('crc32', () => {
  it('läuft über Stücke weiter wie über das Ganze', () => {
    const daten = text('Falsches Üben von Xylophonmusik quält jeden größeren Zwerg')
    expect(crc32(daten.subarray(10), crc32(daten.subarray(0, 10)))).toBe(crc32(daten))
    expect(crc32(text('123456789'))).toBe(0xcbf43926)
  })
})

describe('zipStrom', () => {
  it('schreibt ein Zip, dessen Verzeichnis Namen, Größen und Prüfsummen trägt', async () => {
    const gross = new Uint8Array(300_000).map((_, i) => i % 251)
    const zip = await sammeln(
      zipStrom([
        { pfad: 'Verträge/miete.txt', groesse: 5, teile: () => stuecke(text('Miete'), 2) },
        { pfad: 'bild.bin', groesse: gross.length, teile: () => stuecke(gross, 65_536) },
      ]),
    )
    expect(zipInhalt(zip)).toEqual([
      { pfad: 'Verträge/miete.txt', groesse: 5, ordner: false },
      { pfad: 'bild.bin', groesse: gross.length, ordner: false },
    ])
    // Die Prüfsumme im zentralen Verzeichnis ist die des Inhalts.
    const sicht = new DataView(zip.buffer)
    const ende = zip.length - 22
    const verzeichnis = sicht.getUint32(ende + 16, true)
    expect(sicht.getUint32(verzeichnis + 16, true)).toBe(crc32(text('Miete')))
  })

  it('bricht ab, wenn ein Inhalt nicht die angesagte Größe hat', async () => {
    await expect(sammeln(zipStrom([{ pfad: 'a', groesse: 9, teile: () => stuecke(text('kurz'), 2) }]))).rejects.toThrow('4 statt 9')
  })

  it('weist ein Archiv über 4 GiB vor dem ersten Byte ab', () => {
    expect(() => zipGrenzenPruefen([{ pfad: 'a', groesse: 3 * 1024 ** 3 }, { pfad: 'b', groesse: 2 * 1024 ** 3 }])).toThrow(ZipZuGross)
    expect(() => zipGrenzenPruefen(Array.from({ length: 70_000 }, (_, i) => ({ pfad: String(i), groesse: 0 })))).toThrow(ZipZuGross)
  })
})

describe('zipInhalt', () => {
  it('liest ein Zip aus zipSchreiben samt Ordnern', () => {
    return new Blob([zipSchreiben([{ pfad: 'ordner/', inhalt: '' }, { pfad: 'ordner/a.json', inhalt: '{}' }])])
      .arrayBuffer()
      .then((ab) =>
        expect(zipInhalt(new Uint8Array(ab))).toEqual([
          { pfad: 'ordner/', groesse: 0, ordner: true },
          { pfad: 'ordner/a.json', groesse: 2, ordner: false },
        ]),
      )
  })

  it('wirft bei Bytes ohne Zip-Verzeichnis', () => {
    expect(() => zipInhalt(new Uint8Array(100))).toThrow()
  })
})

describe('tarInhalt und archivInhalt', () => {
  const archiv = tar([
    { name: 'daten/', art: '5' },
    { name: 'a.txt', inhalt: text('Hallo'), vorsatz: 'daten' },
    { name: '././@LongLink', inhalt: text('daten/ein/sehr/langer/name.txt'), art: 'L' },
    { name: 'abgeschnitten', inhalt: text('x'.repeat(700)) },
  ])

  it('liest Namen, Vorsatz, GNU-Langnamen und Größen', () => {
    expect(tarInhalt(archiv)).toEqual([
      { pfad: 'daten/', groesse: 0, ordner: true },
      { pfad: 'daten/a.txt', groesse: 5, ordner: false },
      { pfad: 'daten/ein/sehr/langer/name.txt', groesse: 700, ordner: false },
    ])
  })

  it('erkennt tar.gz und zip an den ersten Bytes', async () => {
    const gz = new Uint8Array(await new Response(new Blob([archiv]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer())
    expect((await archivInhalt(gz)).map((e) => e.pfad)).toContain('daten/a.txt')
    expect((await archivInhalt(archiv)).length).toBe(3)
    await expect(archivInhalt(text('kein Archiv, nur Text, aber lang genug für die Kopfprüfung'.repeat(10)))).rejects.toThrow()
  })
})
