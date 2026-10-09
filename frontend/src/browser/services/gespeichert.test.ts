/**
 * Was aus localStorage zurückkommt (`msb:einstellungen`, `msb:tabs`), wurde
 * ungeprüft übernommen; ein Stand einer anderen Fassung oder ein halb
 * geschriebener Wert legte danach die Oberfläche lahm (Bugjagd 08.10.2026,
 * beide Tests am Stand 229b690d rot).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { useEinstellungenStore } = await import('./einstellungenStore')
const { useTabsStore } = await import('./tabsStore')

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  localStorage.clear()
  sessionStorage.clear()
})

describe('Gespeicherter Stand mit falschen Typen', () => {
  it('msb:einstellungen mit ausgeblendet = null lässt Kopfleiste und Schalter nicht abstürzen', async () => {
    localStorage.setItem(
      'msb:einstellungen',
      JSON.stringify({ state: { ausgeblendet: null, anordnung: 'links', widgetsAus: 5, schnellzugriffe: 'kaputt' }, version: 1 }),
    )
    await useEinstellungenStore.persist.rehydrate()
    const s = useEinstellungenStore.getState()
    const fehler: string[] = []
    const probe = (name: string, f: () => unknown) => {
      try {
        f()
      } catch (e) {
        fehler.push(`${name}: ${(e as Error).message}`)
      }
    }
    // Genau die Selektoren aus `Kopfleiste.tsx:134`, `Navigationsleiste.tsx:120`, `Schnellzugriffe.tsx`.
    probe('Kopfleiste', () => s.ausgeblendet.includes('privaterTab'))
    probe('umschalten', () => s.umschalten('schild'))
    probe('Schnellzugriffe', () => (useEinstellungenStore.getState().schnellzugriffe as unknown as unknown[]).map((k) => k))
    expect(fehler).toEqual([])
  })

  it('msb:tabs mit einer Adresse, die kein Text ist, lässt das Hochfahren nicht abstürzen', async () => {
    localStorage.setItem(
      'msb:tabs',
      JSON.stringify({ state: { tabs: [{ id: 'tab-a', url: 42, titel: null, favicon: null }], aktivId: 'tab-a' }, version: 1 }),
    )
    await useTabsStore.persist.rehydrate()
    await expect(useTabsStore.getState().hochfahren()).resolves.toBeUndefined()
  })

  it('übernimmt gültige Tabs und lässt die übrigen Werte stehen', async () => {
    localStorage.setItem(
      'msb:tabs',
      JSON.stringify({
        state: { tabs: [{ id: 'tab-a', url: 'https://a.example/', titel: 'A', favicon: null, privat: true }, { id: 'main', url: 'x' }], aktivId: 'tab-a' },
        version: 1,
      }),
    )
    await useTabsStore.persist.rehydrate()
    expect(useTabsStore.getState().tabs).toMatchObject([{ id: 'tab-a', url: 'https://a.example/', titel: 'A', privat: false }])
    localStorage.setItem('msb:einstellungen', JSON.stringify({ state: { panelBreite: 500, ausgeblendet: ['schild'], unbekannt: 1 }, version: 1 }))
    await useEinstellungenStore.persist.rehydrate()
    expect(useEinstellungenStore.getState()).toMatchObject({ panelBreite: 500, ausgeblendet: ['schild'] })
    expect('unbekannt' in useEinstellungenStore.getState()).toBe(false)
  })

  it('macht aus der alten Vorgabe DuckDuckGo die Werkseinstellung und behält eine andere Wahl', async () => {
    localStorage.setItem('msb:einstellungen', JSON.stringify({ state: { suchmaschine: 'duckduckgo' }, version: 1 }))
    await useEinstellungenStore.persist.rehydrate()
    expect(useEinstellungenStore.getState().suchmaschine).toBeNull()

    localStorage.setItem('msb:einstellungen', JSON.stringify({ state: { suchmaschine: 'brave' }, version: 1 }))
    await useEinstellungenStore.persist.rehydrate()
    expect(useEinstellungenStore.getState().suchmaschine).toBe('brave')

    // Ab Version 2 ist DuckDuckGo eine eigene Wahl.
    localStorage.setItem('msb:einstellungen', JSON.stringify({ state: { suchmaschine: 'duckduckgo' }, version: 2 }))
    await useEinstellungenStore.persist.rehydrate()
    expect(useEinstellungenStore.getState().suchmaschine).toBe('duckduckgo')
  })
})
