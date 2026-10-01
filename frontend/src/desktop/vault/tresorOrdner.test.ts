import { describe, expect, it } from 'vitest'
import { dateienUnter, darfAlleVerschieben, darfVerschieben, fassungenVon, obersteAuswahl, pfadVon, zielOrdner } from './tresorOrdner'
import type { VaultItem } from './vaultStore'

const ordner = (id: string, service: string, eltern?: string) => ({ id, service, category: 'ordner', ordner: eltern }) as VaultItem
const datei = (id: string, eltern?: string) => ({ id, service: `${id}.txt`, category: 'datei', ordner: eltern }) as VaultItem

const vertraege = ordner('v', 'Verträge')
const miete = ordner('m', 'Miete', 'v')
const alt = ordner('a', '2024', 'm')
const fotos = ordner('f', 'Fotos')
const alle = [vertraege, miete, alt, fotos]

describe('tresorOrdner', () => {
  it('kennt den Weg vom Stamm bis zu einem Ordner', () => {
    expect(pfadVon('a', alle).map((o) => o.id)).toEqual(['v', 'm', 'a'])
    expect(pfadVon(undefined, alle)).toEqual([])
  })

  it('lässt keinen Ordner in sich selbst oder einen Unterordner wandern', () => {
    expect(darfVerschieben(vertraege, 'v', alle)).toBe(false)
    expect(darfVerschieben(vertraege, 'a', alle)).toBe(false)
    expect(darfVerschieben(vertraege, 'f', alle)).toBe(true)
    expect(darfVerschieben(miete, undefined, alle)).toBe(true)
  })

  it('verschiebt nicht dorthin, wo ein Eintrag schon liegt', () => {
    expect(darfVerschieben(datei('d', 'm'), 'm', alle)).toBe(false)
    expect(darfVerschieben(datei('d'), undefined, alle)).toBe(false)
    expect(darfVerschieben(datei('d'), 'a', alle)).toBe(true)
  })

  it('bietet im Dialog nur erlaubte Ziele mit vollem Pfad an', () => {
    expect(zielOrdner([miete], alle)).toEqual([
      { value: 'f', label: 'Fotos' },
      { value: 'v', label: 'Verträge' },
    ])
    // Mehrere gewählte Ordner: keiner ihrer Teilbäume ist ein Ziel.
    expect(zielOrdner([miete, fotos], alle)).toEqual([{ value: 'v', label: 'Verträge' }])
  })

  it('verschiebt mehrere nur, wenn keiner ein Ordner auf dem Weg ist und einer den Ort wechselt', () => {
    expect(darfAlleVerschieben([datei('d', 'f'), miete], 'f', alle)).toBe(true)
    expect(darfAlleVerschieben([datei('d', 'f')], 'f', alle)).toBe(false)
    expect(darfAlleVerschieben([datei('d'), vertraege], 'a', alle)).toBe(false)
  })

  it('lässt von der Auswahl weg, was in einem gewählten Ordner liegt', () => {
    const kind = datei('k', 'a')
    const draussen = datei('d', 'f')
    const items = [...alle, kind, draussen]
    expect(obersteAuswahl(['m', 'k', 'd'], items).map((i) => i.id)).toEqual(['m', 'd'])
    expect(obersteAuswahl(['k'], items).map((i) => i.id)).toEqual(['k'])
  })

  it('sammelt Dateien rekursiv mit Pfad und macht gleiche Namen eindeutig', () => {
    const mitDatei = (id: string, name: string, eltern?: string) => ({ ...datei(id, eltern), service: name, datei: {} }) as VaultItem
    const items = [...alle, mitDatei('x', 'vertrag.pdf', 'm'), mitDatei('y', 'Vertrag.pdf', 'm'), mitDatei('z', 'plan.txt', 'a'), mitDatei('o', 'ohne/strich.txt')]
    expect(dateienUnter([miete, items[7]], items).map((d) => d.pfad)).toEqual([
      'Miete/2024/plan.txt',
      'Miete/vertrag.pdf',
      'Miete/Vertrag (2).pdf',
      'ohne_strich.txt',
    ])
  })

  it('macht aus früheren Fassungen die Liste für die Versionsansicht', () => {
    const item = {
      id: 'd',
      createdAt: 1,
      datei: { frueher: [{ typ: 'text/plain', ersetzt: 50, original: { id: 'o2', echt: 20 } }, { typ: 'text/plain', ersetzt: 10, original: { id: 'o1', echt: 10 } }] },
    } as unknown as VaultItem
    expect(fassungenVon(item)).toEqual([
      { id: 'o2', zeit: 50, groesse: 20 },
      { id: 'o1', zeit: 10, groesse: 10 },
    ])
  })
})
