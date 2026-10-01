import { describe, expect, it } from 'vitest'
import { darfVerschieben, fassungenVon, pfadVon, zielOrdner } from './tresorOrdner'
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
    expect(zielOrdner(miete, alle)).toEqual([
      { value: 'f', label: 'Fotos' },
      { value: 'v', label: 'Verträge' },
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
