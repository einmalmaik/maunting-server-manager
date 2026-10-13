import { describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { ereignisAnwenden } = await import('./tabEreignis')
const { useDownloadsStore } = await import('./downloadsStore')

describe('Tab-Ereignisse', () => {
  it('nimmt einen Download auch ohne Tab an (Popup einer Erweiterung, geschlossener Tab)', () => {
    useDownloadsStore.setState({ downloads: [], neu: 0 })
    const aendern = vi.fn()
    ereignisAnwenden(
      { art: 'download', id: 'erweiterung-popup', nr: 7, url: 'https://a.example/x.zip', datei: 'x.zip', stand: 'start' },
      { finden: () => undefined, aendern, zeigen: vi.fn(), neuerTab: vi.fn(() => ''), schliessen: vi.fn() },
    )
    expect(useDownloadsStore.getState().downloads.map((d) => [d.nr, d.stand])).toEqual([[7, 'start']])
    expect(aendern).not.toHaveBeenCalled()
  })

  it('nimmt Download-Fortschritt ohne Tab an und ohne Geistereintrag', () => {
    useDownloadsStore.setState({ downloads: [], neu: 0 })
    const aendern = vi.fn()
    const zugriff = { finden: () => undefined, aendern, zeigen: vi.fn(), neuerTab: vi.fn(() => ''), schliessen: vi.fn() }
    ereignisAnwenden(
      { art: 'download', id: 'tab-x', nr: 3, url: 'https://a.example/y.zip', datei: 'y.zip', stand: 'start' },
      zugriff,
    )
    ereignisAnwenden(
      { art: 'download_fortschritt', id: 'tab-x', nr: 3, empfangen: 200, gesamt: 1000, bytes_pro_sekunde: 100, rest_sekunden: 8, ohne_daten_sekunden: 0 },
      zugriff,
    )
    // Unbekannte Nummer: kein Geistereintrag, kein Tab angerührt.
    ereignisAnwenden(
      { art: 'download_fortschritt', id: 'tab-x', nr: 9, empfangen: 200, gesamt: 1000, bytes_pro_sekunde: 100, rest_sekunden: 8, ohne_daten_sekunden: 0 },
      zugriff,
    )
    const zustand = useDownloadsStore.getState()
    expect(zustand.downloads.map((d) => [d.nr, d.empfangen, d.gesamt])).toEqual([[3, 200, 1000]])
    expect(aendern).not.toHaveBeenCalled()
  })

  it('schließt einen Tab bei Ereignis schliessen (window.close)', () => {
    const schliessen = vi.fn()
    const tab = { id: 'tab-1', url: 'https://example.com/', privat: false } as any
    ereignisAnwenden(
      { art: 'schliessen', id: 'tab-1' },
      { finden: () => tab, aendern: vi.fn(), zeigen: vi.fn(), neuerTab: vi.fn(() => ''), schliessen },
    )
    expect(schliessen).toHaveBeenCalledWith('tab-1')
  })

  it('öffnet neuen Tab mit nativer Kennung bei Fensteraufruf', () => {
    const neuerTab = vi.fn(() => 'tab-p1')
    const tab = { id: 'tab-1', url: 'https://example.com/', privat: false } as any
    ereignisAnwenden(
      { art: 'neuer_tab', id: 'tab-1', neue_id: 'tab-p1', url: 'https://oauth.example/' },
      { finden: () => tab, aendern: vi.fn(), zeigen: vi.fn(), neuerTab, schliessen: vi.fn() },
    )
    expect(neuerTab).toHaveBeenCalledWith('https://oauth.example/', {
      privat: false,
      nach: 'tab-1',
      id: 'tab-p1',
      nativDa: true,
    })
  })
})
