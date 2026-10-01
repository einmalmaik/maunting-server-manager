import { describe, expect, it } from 'vitest'
import { artBezeichnung, istText, vorschauArt } from './tresorAnzeige'

describe('vorschauArt', () => {
  it.each([
    ['image/png', 'a.png', 'bild'],
    ['image/svg+xml', 'logo.svg', 'bild'],
    ['', 'logo.svg', 'bild'],
    ['video/mp4', 'film.mp4', 'video'],
    ['audio/mpeg', 'lied.mp3', 'audio'],
    ['application/pdf', 'vertrag.pdf', 'pdf'],
    ['', 'Vertrag.PDF', 'pdf'],
    ['text/plain', 'liesmich.md', 'markdown'],
    ['', 'liesmich.md', 'markdown'],
    ['text/csv', 'konten.csv', 'tabelle'],
    ['application/vnd.ms-excel', 'konten.csv', 'office'],
    ['', 'daten.tsv', 'tabelle'],
    ['', 'server.yml', 'text'],
    ['', 'index.ts', 'text'],
    ['application/octet-stream', 'start.sh', 'text'],
    ['text/html', 'seite.html', 'text'],
    ['', 'Dockerfile', 'text'],
    ['', '.env', 'text'],
    ['application/zip', 'backup.zip', 'archiv'],
    ['', 'welt.tar.gz', 'archiv'],
    ['font/ttf', 'schrift.ttf', 'schrift'],
    ['', 'schrift.woff2', 'schrift'],
    ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'brief.docx', 'office'],
    ['', 'brief.odt', 'office'],
    ['', 'setup.exe', null],
    ['', 'ohne-endung', null],
  ])('%s + %s → %s', (typ, name, art) => {
    expect(vorschauArt(typ, name)).toBe(art)
  })
})

describe('artBezeichnung', () => {
  it('nennt Dokument, Tabellenblatt, Programm und Archiv', () => {
    expect(artBezeichnung('', 'brief.docx')).toBe('dokument')
    expect(artBezeichnung('', 'konten.xlsx')).toBe('tabellenblatt')
    expect(artBezeichnung('', 'folien.pptx')).toBe('praesentation')
    expect(artBezeichnung('', 'setup.exe')).toBe('programm')
    expect(artBezeichnung('', 'sicherung.7z')).toBe('archiv')
    expect(artBezeichnung('', 'unbekannt')).toBe('datei')
  })
})

describe('istText', () => {
  const enc = new TextEncoder()

  it('nimmt UTF-8 an, auch wenn das letzte Zeichen abgeschnitten ist', () => {
    expect(istText(enc.encode('Grüße aus Köln'))).toBe(true)
    const ganz = enc.encode('größer')
    expect(istText(ganz.subarray(0, 3))).toBe(true) // "g" + halbes "r" geht nicht, "gr" + erstes Byte von "ö"
  })

  it('lehnt NUL-Bytes und ungültiges UTF-8 ab', () => {
    expect(istText(new Uint8Array([0x48, 0x00, 0x49]))).toBe(false)
    expect(istText(new Uint8Array([0x48, 0xff, 0xfe, 0x49]))).toBe(false)
  })
})
