/**
 * Wettläufe im Tab-Store, aus der Bugjagd vom 08.10.2026. Beide Tests waren
 * am Stand 229b690d rot.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen, beimLaden } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: Record<string, unknown> }[],
  beimLaden: { tun: null as null | ((args: Record<string, unknown>) => void) },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    // Unter Android meldet `Tab.laden` die Sperre synchron (`TabsBruecke.weg`),
    // bevor `tab_laden` zurückkommt.
    if (befehl === 'tab_laden' && beimLaden.tun) beimLaden.tun(args)
    return Promise.resolve(null)
  },
}))

const { useTabsStore } = await import('./tabsStore')

const warten = () => new Promise((r) => setTimeout(r, 0))
const aktiv = () => useTabsStore.getState().tabs.find((t) => t.id === useTabsStore.getState().aktivId)!

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  localStorage.clear()
  const id = 'tab-start'
  useTabsStore.setState({ tabs: [{ ...useTabsStore.getState().tabs[0], id, url: '', nativDa: false }], aktivId: id, geschlossen: [] })
  gerufen.length = 0
  beimLaden.tun = null
})

describe('Tabs: Sperre und private Sitzung', () => {
  it('eine gesperrte Seite bleibt gesperrt, auch wenn Rust die Sperre vor dem Ende von tab_laden meldet', async () => {
    beimLaden.tun = (args) =>
      useTabsStore.getState().ereignis({ art: 'gesperrt', id: args.id as string, url: args.url as string, grund: 'gluecksspiel' })
    useTabsStore.getState().oeffnen('https://casino.example/')
    await warten()
    await warten()
    expect(aktiv()).toMatchObject({ fehler: 'gesperrt', gesperrt: 'gluecksspiel' })
    // Die Oberfläche zeichnet die Sperrseite; die (leere) Webview darf nicht darüber liegen.
    const aktivieren = gerufen.filter((g) => g.befehl === 'tab_aktivieren')
    expect(aktivieren.at(-1)?.args).toEqual({ id: null })
  })

  it('nach dem letzten privaten Tab holt Strg+Umschalt+T keine private Seite zurück', () => {
    const privat = useTabsStore.getState().neuerTab('https://geheim.example/akte', { privat: true })
    useTabsStore.getState().schliessen(privat)
    expect(useTabsStore.getState().tabs.some((t) => t.privat)).toBe(false)
    // Die private Sitzung ist zu (Android leert ihr Profil). Jemand anderes am Gerät:
    useTabsStore.getState().wiederherstellen()
    const urls = useTabsStore.getState().tabs.map((t) => t.url)
    expect(urls).not.toContain('https://geheim.example/akte')
  })
})
