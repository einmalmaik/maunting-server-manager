import { describe, expect, it, vi } from 'vitest'

const aufrufe: string[] = []
vi.mock('./protokoll', async (original) => ({
  ...(await original<typeof import('./protokoll')>()),
  // Jede Antwort kommt etwas später, wie über die echte Brücke.
  rufen: vi.fn((_tab: string, methode: string) => new Promise((fertig) => setTimeout(() => (aufrufe.push(methode), fertig({})), 1))),
}))

const { ausschalten, einschalten } = await import('./werkzeuge')
const { useKonsole } = await import('./konsoleStore')

describe('Ein- und Ausschalten', () => {
  it('ein schneller Wechsel lässt die Bereiche am Ende an', async () => {
    // So mountet React im StrictMode, und so sieht ein schneller Tabwechsel aus.
    await Promise.all([einschalten('t'), ausschalten('t'), einschalten('t')])
    expect(aufrufe.lastIndexOf('CSS.enable')).toBeGreaterThan(aufrufe.lastIndexOf('CSS.disable'))
    expect(aufrufe.lastIndexOf('DOM.enable')).toBeGreaterThan(aufrufe.lastIndexOf('DOM.disable'))
  })

  it('Meldungen, die die Seite beim Einschalten noch einmal schickt, stehen nicht doppelt da', async () => {
    const meldung = { type: 'log', args: [{ type: 'string', value: 'geladen' }] }
    useKonsole.getState().ereignis('r', 'Runtime.consoleAPICalled', meldung)
    await ausschalten('r')
    await einschalten('r')
    // Auf `Runtime.enable` antwortet die Seite mit allen bisherigen Meldungen.
    useKonsole.getState().ereignis('r', 'Runtime.consoleAPICalled', meldung)
    expect(useKonsole.getState().eintraege.r.map((e) => e.anzahl)).toEqual([1])
  })
})
