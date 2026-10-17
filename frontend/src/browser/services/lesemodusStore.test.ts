import { beforeEach, describe, expect, it } from 'vitest'
import { useLesemodusStore } from './lesemodusStore'
import type { ExtrahierterArtikel } from './lesemodusService'

describe('lesemodusStore', () => {
  beforeEach(() => {
    useLesemodusStore.setState({
      schriftgroesse: 'mittel',
      schriftart: 'sans',
      breite: 'standard',
      linksModus: 'dezent',
      artikelCache: {},
    })
  })

  it('ändert Schriftgröße, Schriftart, Zeilenbreite und Link-Modus', () => {
    const store = useLesemodusStore.getState()

    store.setSchriftgroesse('gross')
    expect(useLesemodusStore.getState().schriftgroesse).toBe('gross')

    store.setSchriftart('serif')
    expect(useLesemodusStore.getState().schriftart).toBe('serif')

    store.setBreite('breit')
    expect(useLesemodusStore.getState().breite).toBe('breit')

    expect(useLesemodusStore.getState().linksModus).toBe('dezent')
    store.setLinksModus('hervorgehoben')
    expect(useLesemodusStore.getState().linksModus).toBe('hervorgehoben')
    store.setLinksModus('aus')
    expect(useLesemodusStore.getState().linksModus).toBe('aus')
  })

  it('speichert und liefert Artikel im Cache und leert diesen auf Wunsch', () => {
    const mockArtikel: ExtrahierterArtikel = {
      titel: 'Test-Titel',
      autor: 'Autor A',
      datum: '2026-10-17',
      inhaltHtml: '<p>Inhalt</p>',
      reintext: 'Inhalt',
      wortanzahl: 1,
      lesedauerMinuten: 1,
      url: 'https://example.com/test',
    }

    const store = useLesemodusStore.getState()
    store.artikelCachen('tab-1:https://example.com/test', mockArtikel)

    expect(useLesemodusStore.getState().artikelAusCache('tab-1:https://example.com/test')).toEqual(mockArtikel)
    expect(useLesemodusStore.getState().artikelAusCache('unbekannt')).toBeUndefined()

    // Einzelnen Key leeren
    store.cacheLeeren('tab-1:https://example.com/test')
    expect(useLesemodusStore.getState().artikelAusCache('tab-1:https://example.com/test')).toBeUndefined()

    // Komplett leeren
    store.artikelCachen('k1', mockArtikel)
    store.artikelCachen('k2', mockArtikel)
    store.cacheLeeren()
    expect(useLesemodusStore.getState().artikelCache).toEqual({})
  })
})
