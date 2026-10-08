import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen } = vi.hoisted(() => ({ gerufen: [] as { befehl: string; args: unknown }[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: unknown) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))

const { useTabsStore } = await import('./tabsStore')
const { useEinstellungenStore } = await import('./einstellungenStore')
const { useVerlaufFrist, useVerlaufStore } = await import('./verlaufStore')
const { kuerzelAusTaste } = await import('./kuerzel')

const warten = () => new Promise((r) => setTimeout(r, 0))
const befehle = () => gerufen.map((g) => g.befehl)
const aktiv = () => useTabsStore.getState().tabs.find((t) => t.id === useTabsStore.getState().aktivId)!

describe('Seiten des Browsers im Tab', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    const id = 'tab-start'
    useTabsStore.setState({
      tabs: [{ ...useTabsStore.getState().tabs[0], id, url: '', nativDa: false }],
      aktivId: id,
      geschlossen: [],
    })
    gerufen.length = 0
  })

  it('öffnet die Einstellungen in der leeren Startseite, ohne Webview', () => {
    useTabsStore.getState().einstellungen()
    expect(useTabsStore.getState().tabs).toHaveLength(1)
    expect(aktiv().url).toBe('msb://einstellungen')
    expect(befehle()).not.toContain('tab_laden')
  })

  it('nimmt einen schon offenen Einstellungs-Tab und wechselt dort die Kategorie', () => {
    useTabsStore.getState().einstellungen()
    const id = aktiv().id
    useTabsStore.getState().neuerTab('https://example.com/')
    useTabsStore.getState().einstellungen('suche')
    expect(useTabsStore.getState().tabs).toHaveLength(2)
    expect(aktiv().id).toBe(id)
    expect(aktiv().url).toBe('msb://einstellungen/suche')
  })

  it('schließt die Webview, wenn ein Tab mit Seite zu den Einstellungen wechselt', async () => {
    useTabsStore.getState().oeffnen('https://example.com/')
    expect(aktiv().nativDa).toBe(true)
    await warten()
    gerufen.length = 0
    useTabsStore.getState().eingeben('msb://einstellungen/schutz')
    await warten()
    expect(aktiv()).toMatchObject({ url: 'msb://einstellungen/schutz', nativDa: false, laedt: false })
    expect(befehle()).toContain('tab_schliessen')
    expect(befehle()).not.toContain('tab_laden')
  })

  it('lädt beim Aktivieren keine Webview für eine eigene Seite', async () => {
    useTabsStore.getState().einstellungen()
    const id = aktiv().id
    useTabsStore.getState().neuerTab()
    gerufen.length = 0
    useTabsStore.getState().aktivieren(id)
    await warten()
    expect(gerufen).toContainEqual({ befehl: 'tab_aktivieren', args: { id: null } })
    expect(befehle()).not.toContain('tab_laden')
  })

  it('lässt eine Webseite keine eigene Seite öffnen', () => {
    useTabsStore.getState().ereignis({ art: 'neuer_tab', id: aktiv().id, url: 'msb://einstellungen' })
    expect(useTabsStore.getState().tabs).toHaveLength(1)
    expect(aktiv().url).toBe('')
  })

  it('Strg+Komma öffnet die Einstellungen', () => {
    expect(kuerzelAusTaste(new KeyboardEvent('keydown', { key: ',', ctrlKey: true }))).toBe('einstellungen')
  })
})

describe('Beim Start', () => {
  beforeEach(() => {
    sessionStorage.clear()
    useTabsStore.setState({
      tabs: [
        { ...useTabsStore.getState().tabs[0], id: 'tab-a', url: 'https://a.example/' },
        { ...useTabsStore.getState().tabs[0], id: 'tab-b', url: 'https://b.example/' },
      ],
      aktivId: 'tab-b',
      geschlossen: [],
    })
  })

  it('beginnt mit einer leeren Startseite und behält die alten Tabs zum Wiederherstellen', async () => {
    useEinstellungenStore.setState({ beimStart: 'startseite' })
    await useTabsStore.getState().hochfahren()
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual([''])
    expect(useTabsStore.getState().geschlossen.map((g) => g.url)).toEqual(['https://b.example/', 'https://a.example/'])
  })

  it('verwirft beim Neuladen der Oberfläche keine Tabs', async () => {
    useEinstellungenStore.setState({ beimStart: 'startseite' })
    sessionStorage.setItem('msb:gestartet', '1')
    await useTabsStore.getState().hochfahren()
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual(['https://a.example/', 'https://b.example/'])
  })
})

describe('Verlauf und Lesezeichen', () => {
  const TAG = 24 * 60 * 60 * 1000

  beforeEach(() => {
    const jetzt = Date.now()
    useVerlaufStore.setState({
      verlauf: [
        { url: 'https://neu.example/', titel: 'Neu', zeit: jetzt - TAG },
        { url: 'https://alt.example/', titel: 'Alt', zeit: jetzt - 10 * TAG },
      ],
      lesezeichen: [
        { url: 'https://1.example/', titel: 'Eins', zeit: 1 },
        { url: 'https://2.example/', titel: 'Zwei', zeit: 2 },
        { url: 'https://3.example/', titel: 'Drei', zeit: 3 },
      ],
    })
  })

  it('löscht mit der Frist „eine Woche“ nur Älteres', () => {
    useEinstellungenStore.setState({ verlaufBehalten: 'woche' })
    const { unmount } = renderHook(() => useVerlaufFrist())
    expect(useVerlaufStore.getState().verlauf.map((e) => e.titel)).toEqual(['Neu'])
    unmount()
  })

  it('lässt den Verlauf mit „nie“ stehen und löscht, sobald eine Frist gewählt wird', () => {
    useEinstellungenStore.setState({ verlaufBehalten: 'immer' })
    const { unmount } = renderHook(() => useVerlaufFrist())
    expect(useVerlaufStore.getState().verlauf).toHaveLength(2)
    act(() => useEinstellungenStore.setState({ verlaufBehalten: 'woche' }))
    expect(useVerlaufStore.getState().verlauf).toHaveLength(1)
    unmount()
  })

  it('benennt Lesezeichen um und ordnet sie neu', () => {
    const { lesezeichenUmbenennen, lesezeichenVerschieben } = useVerlaufStore.getState()
    lesezeichenUmbenennen('https://2.example/', '  Zweites  ')
    lesezeichenVerschieben(2, 0)
    expect(useVerlaufStore.getState().lesezeichen.map((e) => e.titel)).toEqual(['Drei', 'Eins', 'Zweites'])
    lesezeichenUmbenennen('https://3.example/', '   ')
    expect(useVerlaufStore.getState().lesezeichen[0].titel).toBe('https://3.example/')
  })
})
