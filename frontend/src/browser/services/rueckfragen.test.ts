import { beforeEach, describe, expect, it, vi } from 'vitest'

const aufrufe: unknown[] = []
vi.mock('@tauri-apps/api/core', () => ({ invoke: (befehl: string, args: unknown) => (aufrufe.push([befehl, args]), Promise.resolve(null)) }))

const { useRueckfragen, herkunftsName } = await import('./rueckfragen')
type Frage = Parameters<ReturnType<typeof useRueckfragen.getState>['aufnehmen']>[0]

const menue = (nr: number, id = 'tab-a'): Frage =>
  ({ art: 'kontextmenue', id, nr, x: 0, y: 0, link: null, bild: null, auswahl: null, bearbeitbar: false, eintraege: [] }) as unknown as Frage
const dialog = (nr: number, id = 'tab-a'): Frage => ({ art: 'dialog', id, nr, typ: 'alert', text: 'x', vorgabe: '', herkunft: 'https://a.example' }) as unknown as Frage

describe('Rückfragen', () => {
  beforeEach(() => {
    useRueckfragen.setState({ offen: [] })
    aufrufe.length = 0
    Object.defineProperty(window, '__TAURI_INTERNALS__', { value: {}, configurable: true })
  })

  it('ersetzt ein offenes Menü desselben Tabs, aber keinen Dialog und kein Menü eines anderen Tabs', () => {
    const r = useRueckfragen.getState()
    r.aufnehmen(dialog(1))
    r.aufnehmen(menue(2))
    r.aufnehmen(menue(3, 'tab-b'))
    r.aufnehmen(menue(4))
    expect(useRueckfragen.getState().offen.map((f) => f.nr)).toEqual([1, 3, 4])
  })

  it('antwortet auf jede Frage höchstens einmal', () => {
    const r = useRueckfragen.getState()
    r.aufnehmen(dialog(7))
    r.antworten(7, { art: 'ok' } as never)
    r.antworten(7, { art: 'ok' } as never)
    r.antworten(99, { art: 'ok' } as never)
    expect(aufrufe.filter(([b]) => b === 'tab_antworten')).toHaveLength(1)
    expect(useRueckfragen.getState().offen).toEqual([])
  })

  it('vergisst die Fragen eines geschlossenen Tabs', () => {
    const r = useRueckfragen.getState()
    r.aufnehmen(dialog(1))
    r.aufnehmen(dialog(2, 'tab-b'))
    r.tabWeg('tab-a')
    expect(useRueckfragen.getState().offen.map((f) => f.nr)).toEqual([2])
  })

  it('nennt die Herkunft lesbar', () => {
    expect(herkunftsName('https://a.example:8443/pfad')).toBe('a.example:8443')
    expect(herkunftsName('kein url')).toBe('kein url')
  })
})
