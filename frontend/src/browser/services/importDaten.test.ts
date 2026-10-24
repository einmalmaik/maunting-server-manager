import { describe, expect, it } from 'vitest'

import { VORGABE_SCHNELLZUGRIFFE, type Schnellzugriff } from './einstellungenStore'
import {
  bereinigeBilder,
  herkunft,
  kappeAufBudgets,
  lesezeichenZusammenfuehren,
  ordnerPfad,
  schnellzugriffeZusammenfuehren,
  verlaufZusammenfuehren,
  webAdresse,
} from './importDaten'

const PNG = 'data:image/png;base64,iVBORw0KGgo='

describe('Browserimport: Adressen und Ordner', () => {
  it('nimmt nur http(s) ohne Zugangsdaten', () => {
    expect(webAdresse(' https://a.example/x ')).toBe('https://a.example/x')
    expect(herkunft('https://a.example:8443/x?q=1')).toBe('https://a.example:8443')
    for (const schlecht of ['javascript:alert(1)', 'file:///C:/a', 'https://u:p@a.example/', '', `https://a.example/${'x'.repeat(2048)}`]) {
      expect(webAdresse(schlecht)).toBeNull()
    }
    expect(herkunft('msb://einstellungen')).toBeNull()
  })

  it('bereinigt Ordnerpfade und begrenzt Tiefe und Länge', () => {
    expect(ordnerPfad([' Lesezeichenleiste ', null, 42, 'Dev\n'])).toEqual(['Lesezeichenleiste', 'Dev'])
    const tief = ordnerPfad(Array.from({ length: 30 }, (_, i) => `Ebene ${i}`))
    expect(tief).toHaveLength(24)
    expect(ordnerPfad('kein Array')).toEqual([])
  })
})

describe('Browserimport: Lesezeichen', () => {
  it('stellt Importe voran, behält Bestehendes und dedupliziert je Ordner', () => {
    const bestehend = [{ url: 'https://alt.example/', titel: 'Alt', zeit: 10, ordner: ['Lesezeichenleiste'] }]
    const neu = [
      { url: 'https://neu.example/', titel: 'Neu', ordner: ['Lesezeichenleiste'], zeit: 20 },
      { url: 'https://neu.example/', titel: 'Doppelt', ordner: ['Lesezeichenleiste'], zeit: 21 },
      { url: 'https://neu.example/', titel: 'Anderswo', ordner: ['Weitere Lesezeichen'], zeit: 22 },
      { url: 'javascript:alert(1)', titel: 'Böse', ordner: [], zeit: 23 },
    ]
    const ergebnis = lesezeichenZusammenfuehren(bestehend, neu)
    expect(ergebnis.uebernommen).toBe(2)
    expect(ergebnis.gekuerzt).toBe(false)
    expect(ergebnis.lesezeichen.map((e) => [e.url, e.ordner?.join('/'), e.favicon])).toEqual([
      ['https://neu.example/', 'Lesezeichenleiste', 'https://neu.example'],
      ['https://neu.example/', 'Weitere Lesezeichen', 'https://neu.example'],
      ['https://alt.example/', 'Lesezeichenleiste', undefined],
    ])
  })

  it('verdrängt keine bestehenden Einträge, wenn die Liste voll ist', () => {
    const bestehend = [
      { url: 'https://a.example/', titel: 'A', zeit: 1 },
      { url: 'https://b.example/', titel: 'B', zeit: 2 },
    ]
    const neu = Array.from({ length: 5 }, (_, i) => ({ url: `https://neu${i}.example/`, titel: `N${i}`, ordner: [], zeit: i }))
    const ergebnis = lesezeichenZusammenfuehren(bestehend, neu, 3)
    expect(ergebnis.lesezeichen).toHaveLength(3)
    expect(ergebnis.lesezeichen.slice(-2)).toEqual(bestehend)
    expect(ergebnis.uebernommen).toBe(1)
    expect(ergebnis.gekuerzt).toBe(true)
  })
})

describe('Browserimport: Verlauf', () => {
  it('aktualisiert je Adresse den neuesten Eintrag und behält ältere Besuche', () => {
    const bestehend = [
      { url: 'https://a.example/', titel: 'Alt', zeit: 100 },
      { url: 'https://a.example/', titel: 'Älter', zeit: 50 },
      { url: 'https://b.example/', titel: 'B', zeit: 200 },
    ]
    const neu = [
      { url: 'https://a.example/', titel: 'Neu', zeit: 150, anzahl: 9 },
      { url: 'https://c.example/', titel: 'C', zeit: 250, anzahl: 2 },
      { url: 'https://a.example/', titel: 'Noch neuer', zeit: 120, anzahl: 3 },
    ]
    const ergebnis = verlaufZusammenfuehren(bestehend, neu)
    expect(ergebnis.uebernommen).toBe(2)
    expect(ergebnis.verlauf.map((e) => [e.url, e.titel, e.zeit, e.anzahl])).toEqual([
      ['https://c.example/', 'C', 250, 2],
      ['https://b.example/', 'B', 200, undefined],
      ['https://a.example/', 'Neu', 150, 9],
      ['https://a.example/', 'Älter', 50, undefined],
    ])
  })

  it('kappt nach Zeit und meldet es', () => {
    const bestehend = [{ url: 'https://alt.example/', titel: 'Alt', zeit: 1 }]
    const neu = [
      { url: 'https://a.example/', titel: 'A', zeit: 3, anzahl: 1 },
      { url: 'https://b.example/', titel: 'B', zeit: 2, anzahl: 1 },
    ]
    const ergebnis = verlaufZusammenfuehren(bestehend, neu, 2)
    expect(ergebnis.verlauf.map((e) => e.url)).toEqual(['https://a.example/', 'https://b.example/'])
    expect(ergebnis.gekuerzt).toBe(true)
  })
})

describe('Browserimport: Top-Sites', () => {
  const eigene: Schnellzugriff[] = [{ url: 'https://eigene.example/', titel: 'Eigene' }]

  it('ersetzt unveränderte Standardkacheln', () => {
    const ergebnis = schnellzugriffeZusammenfuehren(
      VORGABE_SCHNELLZUGRIFFE,
      [{ url: 'https://x.example/', titel: 'X' }],
      VORGABE_SCHNELLZUGRIFFE,
      4,
    )
    expect(ergebnis.schnellzugriffe).toEqual([{ url: 'https://x.example/', titel: 'X' }])
    expect(ergebnis.uebernommen).toBe(1)
  })

  it('lässt eigene Kacheln stehen und füllt dahinter auf', () => {
    const ergebnis = schnellzugriffeZusammenfuehren(
      eigene,
      [
        { url: 'https://eigene.example/', titel: 'Doppelt' },
        { url: 'https://x.example/', titel: 'X' },
      ],
      VORGABE_SCHNELLZUGRIFFE,
      2,
    )
    expect(ergebnis.schnellzugriffe.map((e) => e.url)).toEqual(['https://eigene.example/', 'https://x.example/'])
    expect(ergebnis.uebernommen).toBe(1)
  })
})

describe('Browserimport: Bilder und Speicher', () => {
  it('nimmt nur PNG-Datenadressen zu Web-Herkünften, je Herkunft einmal', () => {
    const bilder = bereinigeBilder([
      { herkunft: 'https://a.example/pfad', daten: PNG },
      { herkunft: 'https://a.example', daten: PNG },
      { herkunft: 'javascript:alert(1)', daten: PNG },
      { herkunft: 'https://b.example', daten: 'data:image/svg+xml;base64,PHN2Zz4=' },
      { herkunft: 'https://c.example', daten: 'data:image/png;base64,AAA' },
      { herkunft: 'https://d.example', daten: `${PNG}${'A'.repeat(9000)}` },
      'kaputt',
    ])
    expect(bilder).toEqual({ 'https://a.example': PNG })
  })

  it('kappt zuerst den ältesten importierten Verlauf, dann Lesezeichen', () => {
    const lesezeichen = [{ url: 'https://a.example/', titel: 'A'.repeat(200), ordner: [], zeit: 1 }]
    const verlauf = [
      { url: 'https://b.example/', titel: 'B'.repeat(200), zeit: 3, anzahl: 1 },
      { url: 'https://c.example/', titel: 'C'.repeat(200), zeit: 2, anzahl: 1 },
    ]
    const ergebnis = kappeAufBudgets(lesezeichen, verlauf, 0, 330)
    expect(ergebnis.verlauf).toHaveLength(0)
    expect(ergebnis.lesezeichen).toHaveLength(1)
    expect(ergebnis.gekuerzt).toBe(true)

    const leer = kappeAufBudgets(lesezeichen, verlauf, 0, 10)
    expect(leer.lesezeichen).toHaveLength(0)
    expect(leer.verlauf).toHaveLength(0)
  })
})
