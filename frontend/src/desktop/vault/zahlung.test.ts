import { describe, expect, it } from 'vitest'

import { itemAusUmschlag, umschlagAusItem } from './vaultEintrag'
import { angabenAusFormular } from './ZahlungDialog'
import { ibanStimmt, istAbgelaufen, istZahlungAngaben, kartenmarke, luhnStimmt, nummerGruppiert, ohneSteuerzeichen, verdeckt, zahlungsmittel } from './zahlung'

const formular = {
  bezeichnung: 'Firmenkarte',
  inhaber: 'Ada Lovelace',
  nummer: '4111 1111 1111 1111',
  monat: '12',
  jahr: '2030',
  pruefnummer: '123',
  iban: 'DE89 3704 0044 0532 0130 00',
  bic: 'cobadeffxxx',
  notizen: '',
}

describe('Prüfsummen', () => {
  it('Luhn und IBAN erkennen einen Zahlendreher', () => {
    expect(luhnStimmt('4111111111111111')).toBe(true)
    expect(luhnStimmt('4111111111111121')).toBe(false)
    expect(luhnStimmt('4111')).toBe(false)
    expect(ibanStimmt('DE89370400440532013000')).toBe(true)
    expect(ibanStimmt('DE89370400440532013001')).toBe(false)
    expect(ibanStimmt('DE8937040044053201300')).toBe(false)
  })

  it('Marke, Gruppen und Ende', () => {
    expect(kartenmarke('4111111111111111')).toBe('visa')
    expect(kartenmarke('2221000000000009')).toBe('mastercard')
    expect(kartenmarke('378282246310005')).toBe('amex')
    expect(nummerGruppiert('378282246310005')).toBe('3782 822463 10005')
    expect(verdeckt({ art: 'konto', iban: 'DE89370400440532013000' })).toBe('•••• 3000')
    expect(istAbgelaufen({ art: 'karte', nummer: '4111111111111111', monat: 9, jahr: 2026 }, new Date(2026, 8, 30))).toBe(false)
    expect(istAbgelaufen({ art: 'karte', nummer: '4111111111111111', monat: 9, jahr: 2026 }, new Date(2026, 9, 1))).toBe(true)
  })
})

describe('Eingabe im Dialog', () => {
  it('nimmt eine gültige Karte und ein gültiges Konto in Normalform', () => {
    expect(angabenAusFormular('karte', formular)).toEqual({
      angaben: { art: 'karte', nummer: '4111111111111111', inhaber: 'Ada Lovelace', monat: 12, jahr: 2030, pruefnummer: '123' },
    })
    expect(angabenAusFormular('konto', formular)).toEqual({
      angaben: { art: 'konto', iban: 'DE89370400440532013000', inhaber: 'Ada Lovelace', bic: 'COBADEFFXXX' },
    })
  })

  it('nennt jedes falsche Feld', () => {
    expect(angabenAusFormular('karte', { ...formular, nummer: '4111 1111 1111 1112', jahr: '', pruefnummer: '12' })).toEqual({
      fehler: { nummer: 'nummer', jahr: 'ablauf', pruefnummer: 'pruefnummer' },
    })
    expect(angabenAusFormular('konto', { ...formular, iban: 'DE00 0000', bic: 'X' })).toEqual({ fehler: { iban: 'iban', bic: 'bic' } })
  })

  it('ein Name zeigt, was er ist', () => {
    expect(ohneSteuerzeichen(' Ada\u202Egnu\u200B ')).toBe('Adagnu')
    const ergebnis = angabenAusFormular('karte', { ...formular, inhaber: 'Ada\u202E' })
    expect('angaben' in ergebnis && ergebnis.angaben.inhaber).toBe('Ada')
  })
})

describe('Umschlag', () => {
  it('liest eine Karte zurück, wie sie geschrieben wurde', () => {
    const zahlung = { art: 'karte' as const, nummer: '4111111111111111', monat: 1, jahr: 2031 }
    const item = itemAusUmschlag('z1', 3, { service: 'Karte', category: 'zahlung', zahlung, createdAt: 1, updatedAt: 2 })
    expect(item.zahlung).toEqual(zahlung)
    expect(itemAusUmschlag('z1', 3, umschlagAusItem(item)).zahlung).toEqual(zahlung)
  })

  it('legt Ungültiges beiseite und schreibt es unverändert zurück', () => {
    const kaputt = { art: 'karte', nummer: '41 11' }
    const item = itemAusUmschlag('z1', 3, { service: 'Karte', category: 'zahlung', zahlung: kaputt })
    expect(item.zahlung).toBeUndefined()
    expect(istZahlungAngaben(kaputt)).toBe(false)
    expect(umschlagAusItem(item).zahlung).toEqual(kaputt)
  })

  it('in der Liste nur, was weder im Papierkorb noch im Archiv liegt', () => {
    const basis = { username: '', password: '', createdAt: 0, updatedAt: 0, revision: 1, category: 'zahlung' }
    const karte = { art: 'karte' as const, nummer: '4111111111111111', inhaber: 'Ada' }
    const liste = zahlungsmittel([
      { ...basis, id: 'a', service: 'Privat', zahlung: karte },
      { ...basis, id: 'b', service: 'Alt', zahlung: karte, trashedAt: 1 },
      { ...basis, id: 'c', service: 'Archiv', zahlung: karte, archivedAt: 1 },
      { ...basis, id: 'd', service: 'Login', category: 'login' },
    ])
    expect(liste.map((i) => i.id)).toEqual(['a'])
    expect(zahlungsmittel(liste, '1111').map((i) => i.id)).toEqual(['a'])
    expect(zahlungsmittel(liste, '4111 1111')).toEqual([])
  })
})
