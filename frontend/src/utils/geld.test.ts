import { describe, expect, it } from 'vitest'

import {
  betragFormatieren,
  eingabeInMicroUsd,
  microUsdInEingabe,
  type Kostenpolitik,
} from './geld'

const EURO: Kostenpolitik = { currency: 'EUR', usd_rate: '0.92' }
const DOLLAR: Kostenpolitik = { currency: 'USD', usd_rate: '1' }

// Intl setzt zwischen Zahl und Zeichen ein schmales geschütztes Leerzeichen.
// Für die Zusage zählt der Betrag, nicht das Trennzeichen.
const ohneLeerzeichen = (wert: string) => wert.replace(/\s/g, ' ')

describe('betragFormatieren', () => {
  it('zeigt den Betrag in der Anzeigewährung und daneben in Dollar', () => {
    // 2,00 USD gebucht, Kurs 0,92 → 1,84 EUR.
    const betrag = betragFormatieren(2_000_000, EURO, 'de')

    expect(ohneLeerzeichen(betrag.primaer)).toBe('1,84 €')
    expect(ohneLeerzeichen(betrag.sekundaer ?? '')).toBe('2,00 $')
  })

  it('lässt den zweiten Wert weg, wenn ohnehin in Dollar angezeigt wird', () => {
    // „2,00 $ (2,00 $)" hilft niemandem.
    expect(betragFormatieren(2_000_000, DOLLAR, 'de').sekundaer).toBeNull()
  })

  it('zeigt Kleinstbeträge, statt sie auf null zu runden', () => {
    // 0,0021 USD — was eine einzelne Anfrage typischerweise kostet. Mit zwei
    // Nachkommastellen stünde hier „0,00 €", und jede Zeile der Aufstellung
    // sähe gleich teuer aus.
    const betrag = betragFormatieren(2_100, DOLLAR, 'de')

    expect(ohneLeerzeichen(betrag.primaer)).toBe('0,0021 $')
  })

  it('fällt bei unlesbarem Kurs auf den gebuchten Betrag zurück', () => {
    // Ehrlicher als eine erfundene Zahl: lieber der Dollarbetrag mit
    // Euro-Zeichen als ein Betrag, den niemand nachrechnen kann.
    const betrag = betragFormatieren(2_000_000, { currency: 'EUR', usd_rate: 'kaputt' }, 'de')

    expect(ohneLeerzeichen(betrag.primaer)).toBe('2,00 €')
  })
})

describe('eingabeInMicroUsd', () => {
  it('nimmt „1,20" als Preis an — die Eingabe, die vorher nicht ging', () => {
    // In ganzen Cent lag zwischen 1 und 2 nichts. 1,20 EUR bei Kurs 0,92 sind
    // 1,304347… USD, aufgerundet auf die Microunit.
    expect(eingabeInMicroUsd('1,20', EURO)).toBe(1_304_348)
  })

  it('versteht Punkt und Komma gleichermaßen', () => {
    expect(eingabeInMicroUsd('3.50', DOLLAR)).toBe(eingabeInMicroUsd('3,50', DOLLAR))
  })

  it('unterscheidet „keine Eingabe" von „kostet nichts"', () => {
    // 0 wäre die Zusage, dass dieses Modell nichts kostet.
    expect(eingabeInMicroUsd('   ', DOLLAR)).toBeNull()
    expect(eingabeInMicroUsd('0', DOLLAR)).toBe(0)
    expect(eingabeInMicroUsd('unfug', DOLLAR)).toBeNull()
    expect(eingabeInMicroUsd('-2', DOLLAR)).toBeNull()
  })
})

describe('microUsdInEingabe', () => {
  it('kommt bei einem Hin und Zurück beim getippten Wert an', () => {
    const gespeichert = eingabeInMicroUsd('1,20', EURO)

    expect(microUsdInEingabe(gespeichert, EURO)).toBe('1.2')
  })

  it('liefert für „nichts hinterlegt" ein leeres Feld', () => {
    expect(microUsdInEingabe(null, EURO)).toBe('')
  })
})
