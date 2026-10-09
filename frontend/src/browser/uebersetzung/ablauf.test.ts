import { beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({ tabUebersetzen: vi.fn() }))
const engine = vi.hoisted(() => ({ bereitmachen: vi.fn(), uebersetzen: vi.fn() }))
const fehlt = vi.hoisted(() => ({ wert: 0 }))

vi.mock('../services/nativ', () => ({ nativ }))
vi.mock('./engine', () => engine)
vi.mock('./modelle', async (original) => ({
  ...(await original<typeof import('./modelle')>()),
  fehlendeGroesse: async () => fehlt.wert,
}))

import { useUebersetzung } from './ablauf'

const TAB = { id: 'tab-a', url: 'https://example.org/seite' }
const zustand = () => useUebersetzung.getState()
const stand = () => zustand().eintraege[TAB.id]?.stand

/** Eine Seite, die ihre Stücke nacheinander herausgibt, wie `seite.js`. */
function seite(stuecke: string[][], sprache = 'fr') {
  let pos = 0
  nativ.tabUebersetzen.mockImplementation(async (id: string, _fuer: string, nr: number, schritt: string) => {
    if (schritt === 'original') return
    if (schritt === 'start') pos = 0
    const texte = stuecke[pos++] ?? []
    queueMicrotask(() => zustand().texte({ art: 'texte', id, nr, sprache, texte }))
  })
}
const schritte = () => nativ.tabUebersetzen.mock.calls.map((c) => c[3])
const warten = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  zustand().seiteWeg(TAB.id)
  nativ.tabUebersetzen.mockReset()
  engine.bereitmachen.mockReset().mockResolvedValue(undefined)
  engine.uebersetzen.mockReset().mockImplementation(async (_p: string[], texte: string[]) => texte.map((t) => t.toUpperCase()))
  fehlt.wert = 0
})

describe('Ablauf einer Übersetzung', () => {
  it('erkennt die Sprache und übersetzt Stück für Stück, wenn die Modelle da sind', async () => {
    seite([['un'], ['deux', 'trois']])
    await zustand().oeffnen(TAB, 'de')
    expect(zustand().eintraege[TAB.id]).toMatchObject({ von: 'fr', nach: 'de', stand: { art: 'fertig' } })
    expect(engine.bereitmachen.mock.calls[0][0]).toEqual(['fr-en', 'en-de'])
    expect(schritte()).toEqual(['start', 'start', 'weiter', 'weiter'])
    expect(nativ.tabUebersetzen.mock.calls[2][4]).toEqual(['UN'])
    expect(nativ.tabUebersetzen.mock.calls[3][4]).toEqual(['DEUX', 'TROIS'])
  })

  it('lädt nichts ohne Zustimmung und nennt die Größe', async () => {
    fehlt.wert = 37_000_000
    seite([['un']])
    await zustand().oeffnen(TAB, 'de')
    expect(stand()).toEqual({ art: 'wahl', fehlt: 37_000_000 })
    expect(engine.bereitmachen).not.toHaveBeenCalled()
  })

  it('schlägt Englisch vor, wenn die Seite schon in der Sprache der App ist', async () => {
    fehlt.wert = 1
    seite([['Hallo']], 'de-DE')
    await zustand().oeffnen(TAB, 'de')
    expect(zustand().eintraege[TAB.id]).toMatchObject({ von: 'de', nach: 'en' })
  })

  it('nimmt keine Antwort, nach der nicht gefragt wurde', async () => {
    fehlt.wert = 1
    nativ.tabUebersetzen.mockResolvedValue(undefined)
    const offen = zustand().oeffnen(TAB, 'de')
    await warten()
    const nr = nativ.tabUebersetzen.mock.calls[0][2] as number
    zustand().texte({ art: 'texte', id: TAB.id, nr: nr + 1, sprache: 'fr', texte: [] })
    zustand().texte({ art: 'texte', id: 'tab-b', nr, sprache: 'fr', texte: [] })
    await warten()
    expect(stand()).toEqual({ art: 'erkennen' })
    zustand().texte({ art: 'texte', id: TAB.id, nr, sprache: 'fr', texte: [] })
    await offen
    expect(zustand().eintraege[TAB.id]?.von).toBe('fr')
  })

  it('hört bei einem Seitenwechsel auf und schickt nichts mehr an die Seite', async () => {
    seite([['un'], ['deux']])
    let freigeben: () => void = () => {}
    engine.uebersetzen.mockImplementationOnce(
      (_p: string[], texte: string[]) => new Promise((r) => (freigeben = () => r(texte.map((t) => t.toUpperCase())))),
    )
    const lauf = zustand().oeffnen(TAB, 'de')
    await vi.waitFor(() => expect(engine.uebersetzen).toHaveBeenCalled())
    zustand().seiteWeg(TAB.id)
    freigeben()
    await lauf
    expect(schritte()).toEqual(['start', 'start'])
    expect(zustand().eintraege[TAB.id]).toBeUndefined()
  })

  it('stellt mit „Original“ her und beendet den Lauf', async () => {
    seite([['un'], ['deux']])
    let freigeben: () => void = () => {}
    engine.uebersetzen.mockImplementationOnce(
      (_p: string[], texte: string[]) => new Promise((r) => (freigeben = () => r(texte.map((t) => t.toUpperCase())))),
    )
    const lauf = zustand().oeffnen(TAB, 'de')
    await vi.waitFor(() => expect(engine.uebersetzen).toHaveBeenCalled())
    zustand().original(TAB.id)
    freigeben()
    await lauf
    expect(schritte()).toEqual(['start', 'start', 'original'])
    expect(stand()).toEqual({ art: 'wahl', fehlt: 0 })
  })

  it('meldet einen Fehler beim Laden mit seinem Grund', async () => {
    seite([['un']])
    engine.bereitmachen.mockRejectedValue(new Error('pruefsumme'))
    await zustand().oeffnen(TAB, 'de')
    expect(stand()).toEqual({ art: 'fehler', grund: 'pruefsumme' })
  })

  it('übersetzt keine eigenen Seiten', async () => {
    await zustand().oeffnen({ id: TAB.id, url: 'msb://einstellungen' }, 'de')
    expect(nativ.tabUebersetzen).not.toHaveBeenCalled()
    expect(zustand().eintraege[TAB.id]).toBeUndefined()
  })
})
