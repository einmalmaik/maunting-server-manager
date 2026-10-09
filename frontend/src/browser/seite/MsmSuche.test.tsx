import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { schutz, api } = vi.hoisted(() => ({
  schutz: { stand: null as unknown, wirft: false },
  api: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string) => {
    if (befehl !== 'schutz_stand') return Promise.resolve(null)
    return schutz.wirft ? Promise.reject(new Error('kaputt')) : Promise.resolve(schutz.stand)
  },
}))

vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))

const { SanitizedApiError } = await import('@/api/client')
const { MsmSuche, gemerktLeeren, sichereSuche } = await import('./MsmSuche')
const { useTabsStore } = await import('../services/tabsStore')
const { msmSucheAdresse } = await import('../services/intern')

const ADRESSE = msmSucheAdresse('Wetter Köln')

function tab() {
  return { ...useTabsStore.getState().tabs[0], id: 'tab-s', url: ADRESSE, nativDa: false }
}

function schutzMit(aktiv: boolean, kategorien: string[], beschaedigt = false) {
  return { regeln: { aktiv, kategorien }, beschaedigt }
}

const treffer = (n: number, von = 0) =>
  Array.from({ length: n }, (_, i) => ({ titel: `Titel ${von + i}`, url: `https://seite${von + i}.example/pfad`, inhalt: `Text ${von + i}` }))

function koerper(aufruf = 0) {
  return JSON.parse(api.mock.calls[aufruf][1].body as string)
}

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  gemerktLeeren()
  api.mockReset()
  schutz.stand = schutzMit(false, [])
  schutz.wirft = false
  useTabsStore.setState({ tabs: [tab()], aktivId: 'tab-s', geschlossen: [] })
})

describe('MSM-Suche', () => {
  it('zeigt Host, Titel und Text und schickt den Begriff in seiner Schreibweise', async () => {
    api.mockResolvedValueOnce({ treffer: treffer(2) })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
    expect(screen.getByText('seite0.example')).toBeInTheDocument()
    expect(screen.getByText('Text 1')).toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/browser/suche', expect.objectContaining({ method: 'POST' }))
    expect(koerper()).toEqual({ q: 'Wetter Köln', seite: 1, sicher: false, sprache: 'de' })
  })

  it('öffnet einen Treffer im selben Tab, mit Strg in einem neuen', async () => {
    api.mockResolvedValueOnce({ treffer: treffer(2) })
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByText('Titel 1'), { ctrlKey: true })
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual([ADRESSE, 'https://seite1.example/pfad'])
    expect(useTabsStore.getState().aktivId).toBe('tab-s')
    fireEvent.click(screen.getByText('Titel 0'))
    expect(useTabsStore.getState().tabs[0]).toMatchObject({ url: 'https://seite0.example/pfad', vorher: ADRESSE })
  })

  it('hängt weitere Ergebnisse an, ohne doppelte', async () => {
    api.mockResolvedValueOnce({ treffer: treffer(10) }).mockResolvedValueOnce({ treffer: [...treffer(1, 9), ...treffer(3, 10)] })
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Weitere Ergebnisse' }))
    expect(await screen.findByText('Titel 12')).toBeInTheDocument()
    expect(koerper(1).seite).toBe(2)
    expect(screen.getAllByText('Titel 9')).toHaveLength(1)
    expect(screen.getAllByRole('listitem')).toHaveLength(13)
  })

  it('sucht nach Zurück nicht noch einmal', async () => {
    api.mockResolvedValueOnce({ treffer: treffer(1) })
    const { unmount } = render(<MsmSuche tab={tab()} />)
    await screen.findByText('Titel 0')
    unmount()
    render(<MsmSuche tab={tab()} />)
    expect(screen.getByText('Titel 0')).toBeInTheDocument()
    expect(api).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['Erwachsenen-Inhalte gesperrt', () => (schutz.stand = schutzMit(true, ['erwachsene'])), true],
    ['Schutzdatei unlesbar', () => (schutz.stand = { ...schutzMit(false, []), beschaedigt: true }), true],
    ['Stand nicht zu lesen', () => (schutz.wirft = true), true],
    ['nur Glücksspiel gesperrt', () => (schutz.stand = schutzMit(true, ['gluecksspiel'])), false],
    ['Schutz aus', () => (schutz.stand = schutzMit(false, ['erwachsene'])), false],
  ])('verlangt die sichere Suche: %s', async (_name, vorbereiten, sicher) => {
    vorbereiten()
    api.mockResolvedValueOnce({ treffer: [] })
    render(<MsmSuche tab={tab()} />)
    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(koerper().sicher).toBe(sicher)
  })

  it('nimmt ohne Schutzstand die sichere Suche', () => {
    expect(sichereSuche(null)).toBe(true)
  })

  it('bietet ohne eingerichtete Suche DuckDuckGo an', async () => {
    api.mockRejectedValueOnce(new SanitizedApiError('x', { status: 503, code: 'BROWSER_SUCHE_NICHT_EINGERICHTET' }))
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Mit DuckDuckGo suchen' }))
    expect(useTabsStore.getState().tabs[0].url).toBe('https://duckduckgo.com/?q=Wetter%20K%C3%B6ln')
  })

  it('hält eine Instanz, die nicht antwortet, nicht für fehlendes Netz', async () => {
    api.mockRejectedValueOnce(new SanitizedApiError('x', { status: 502, code: 'BROWSER_SUCHE_NICHT_ERREICHBAR' }))
    api.mockResolvedValueOnce({ treffer: treffer(1) })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Suche fehlgeschlagen')).toBeInTheDocument()
    expect(screen.queryByText('Keine Verbindung')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }))
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
  })

  it('sagt, wenn zu viel gesucht wurde', async () => {
    api.mockRejectedValueOnce(new SanitizedApiError('x', { status: 429, code: 'BROWSER_SUCHE_ZU_VIELE' }))
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText(/In einer Minute geht es weiter/)).toBeInTheDocument()
  })

  it('sucht ohne Netz weiter, sobald es zurück ist', async () => {
    api.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce({ treffer: treffer(1) })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Keine Verbindung')).toBeInTheDocument()
    window.dispatchEvent(new Event('online'))
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
  })

  it('sagt, wenn es nichts gibt', async () => {
    api.mockResolvedValueOnce({ treffer: [] })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Keine Treffer für „Wetter Köln“')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Weitere Ergebnisse' })).not.toBeInTheDocument()
  })
})
