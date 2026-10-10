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
      { finden: () => undefined, aendern, zeigen: vi.fn(), neuerTab: vi.fn(() => '') },
    )
    expect(useDownloadsStore.getState().downloads.map((d) => [d.nr, d.stand])).toEqual([[7, 'start']])
    expect(aendern).not.toHaveBeenCalled()
  })
})
