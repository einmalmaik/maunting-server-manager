import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { useTabsStore } = await import('./tabsStore')
const { useVerlaufStore } = await import('./verlaufStore')
const { useToastStore } = await import('@/stores/toastStore')

const aktiv = () => useTabsStore.getState().tabs.find((t) => t.id === useTabsStore.getState().aktivId)!

describe('Lokale Ablage der Browser-Stores', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    localStorage.clear()
    const id = 'tab-start'
    useTabsStore.setState({ tabs: [{ ...useTabsStore.getState().tabs[0], id, url: '', nativDa: false }], aktivId: id, geschlossen: [] })
    useVerlaufStore.setState({ verlauf: [], lesezeichen: [] })
  })

  // Bis 09.10.2026 warf danach jedes `set` QuotaExceededError (Bugjagd #4).
  it('eine Seite mit riesigen Adressen legt das Speichern nicht lahm', () => {
    const id = aktiv().id
    const geworfen: string[] = []
    for (let i = 0; i < 6; i++) {
      const url = `https://seite.example/?${i}=${'x'.repeat(1_000_000)}`
      for (const art of ['laedt', 'geladen'] as const) {
        try {
          useTabsStore.getState().ereignis({ art, id, url })
        } catch (e) {
          geworfen.push(`${art} #${i}: ${(e as Error).name}`)
        }
      }
    }
    useVerlaufStore.getState().lesezeichenUmschalten('https://bank.example/', 'Bank')
    useTabsStore.getState().neuerTab('https://example.com/')
    expect(geworfen).toEqual([])
    expect(useVerlaufStore.getState().verlauf).toEqual([])
    expect(JSON.parse(localStorage.getItem('msb:verlauf')!).state.lesezeichen).toHaveLength(1)
    const gespeichert = JSON.parse(localStorage.getItem('msb:tabs')!).state.tabs.map((t: { url: string }) => t.url)
    expect(gespeichert).toEqual(['https://seite.example/', 'https://example.com/'])
  })

  it('kürzt Titel, die eine Seite vorgibt', () => {
    useVerlaufStore.getState().besucht('https://seite.example/', 't'.repeat(10_000))
    expect(useVerlaufStore.getState().verlauf[0].titel).toHaveLength(300)
  })

  it('sagt, wenn der Speicher voll ist, statt zu werfen', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('voll', 'QuotaExceededError')
    })
    try {
      expect(() => useVerlaufStore.getState().lesezeichenUmschalten('https://bank.example/', 'Bank')).not.toThrow()
      expect(() => useVerlaufStore.getState().lesezeichenUmschalten('https://bank2.example/', 'Bank')).not.toThrow()
      const meldungen = useToastStore.getState().toasts.filter((t) => t.message.includes('lokale Speicher ist voll'))
      expect(meldungen).toHaveLength(1)
    } finally {
      setItem.mockRestore()
    }
  })
})
