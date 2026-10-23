import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { tabsSpeichern, setzeUnterfenster } = await import('./tabsAblage')
const { leererTab } = await import('./tab')
const { useTabsStore } = await import('./tabsStore')

type Gespeichert = { tabs: { id: string; url: string; titel: string; favicon: string | null }[]; aktivId?: string }

const aktuell = () => useTabsStore.getState()
const merge = (gespeichert: unknown) => tabsSpeichern.merge!(gespeichert, aktuell())
const partial = (zustand = aktuell()) => tabsSpeichern.partialize!(zustand) as Gespeichert

afterEach(() => setzeUnterfenster(false))

describe('Gespeicherte Tabs lesen', () => {
  it('verwirft alles, was kein Tab-Array ist', () => {
    expect(merge(null).tabs).toEqual(aktuell().tabs)
    expect(merge({ tabs: 'kaputt', aktivId: 'tab-a' }).tabs).toEqual(aktuell().tabs)
    const gemischt = merge({ tabs: [null, 42, { id: 7 }, { id: 'tab-ok', url: 'https://ok.example/' }], aktivId: 'tab-ok' })
    expect(gemischt.tabs.map((t) => t.id)).toEqual(['tab-ok'])
  })

  it('lässt nur gültige Tab-Kennungen durch', () => {
    const gespeichert = {
      tabs: [
        { id: 'main', url: 'https://main.example/' },
        { id: 'tab-', url: 'https://leer.example/' },
        { id: 'tab-../main', url: 'https://traversal.example/' },
        { id: `tab-${'a'.repeat(41)}`, url: 'https://lang.example/' },
        { id: 'tab-ok', url: 'https://ok.example/' },
      ],
      aktivId: 'tab-ok',
    }

    expect(merge(gespeichert).tabs.map((t) => t.id)).toEqual(['tab-ok'])
  })

  it('ersetzt falsche Typen durch die Vorgaben und hebt alte msb-Adressen', () => {
    const gespeichert = {
      tabs: [
        {
          id: 'tab-a',
          url: 'msb://einstellungen/suche',
          titel: 5,
          favicon: { böse: true },
          workspaceId: 7,
          elternId: 9,
        },
      ],
      aktivId: 'tab-a',
    }

    const tab = merge(gespeichert).tabs[0]

    expect(tab).toMatchObject({
      id: 'tab-a',
      url: 'pontis://einstellungen/suche',
      titel: '',
      favicon: null,
      workspaceId: 'standard',
      elternId: null,
    })
  })

  it('gibt bei einem unbekannten aktiven Tab den laufenden Stand zurück', () => {
    const gespeichert = { tabs: [{ id: 'tab-a', url: 'https://a.example/' }], aktivId: 'tab-weg' }

    expect(merge(gespeichert)).toBe(aktuell())
  })
})

describe('Tabs speichern', () => {
  it('speichert nie private Tabs und wählt keinen von ihnen als aktiv', () => {
    const privat = { ...leererTab('tab-privat', true), url: 'https://geheim.example/' }
    const offen = { ...leererTab('tab-offen'), url: 'https://offen.example/' }
    const zustand = { ...aktuell(), tabs: [privat, offen], aktivId: 'tab-privat' }

    const gespeichert = partial(zustand)

    expect(gespeichert.tabs.map((t) => t.id)).toEqual(['tab-offen'])
    expect(gespeichert.aktivId).toBe('tab-offen')
  })

  it('kürzt zu lange Adressen auf die Herkunft und zu lange Titel', () => {
    const lang = { ...leererTab('tab-lang'), url: `https://lang.example/${'a'.repeat(4096)}`, titel: 'T'.repeat(400) }

    const tab = partial({ ...aktuell(), tabs: [lang], aktivId: 'tab-lang' }).tabs[0]

    expect(tab.url).toBe('https://lang.example/')
    expect(tab.titel.length).toBe(300)
    expect(tab.titel.endsWith('…')).toBe(true)
  })

  it('verwirft ein überlanges Favicon und eine Adresse ohne Herkunft', () => {
    const gross = { ...leererTab('tab-gross'), url: `kaputt ${'a'.repeat(4096)}`, favicon: `data:image/png;base64,${'a'.repeat(70_000)}` }

    const tab = partial({ ...aktuell(), tabs: [gross], aktivId: 'tab-gross' }).tabs[0]

    expect(tab.url).toBe('')
    expect(tab.favicon).toBeNull()
  })

  it('schreibt im Unterfenster nichts', () => {
    setzeUnterfenster(true)

    expect(partial()).toEqual({})
    expect(merge({ tabs: [{ id: 'tab-a', url: 'https://a.example/' }], aktivId: 'tab-a' })).toBe(aktuell())
  })
})

describe('Stand aus einer älteren Fassung', () => {
  it('nimmt Version 1 unverändert an, damit merge die Felder ergänzen kann', () => {
    const alt = { tabs: [{ id: 'tab-a', url: 'https://a.example/' }], aktivId: 'tab-a' }

    expect(tabsSpeichern.migrate!(alt, 1)).toBe(alt)
  })
})
