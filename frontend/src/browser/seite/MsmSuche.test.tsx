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
const { MsmSuche, gemerktLeeren, sichereSuche, kontextFragen } = await import('./MsmSuche')
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

/**
 * Die Anfragen laufen über Pfade: Suche, Wissen, Autocomplete. Der Test liest
 * deshalb gezielt den Pfad aus, statt sich auf die Reihenfolge der Aufrufe zu
 * verlassen — das Wissens-Panel fragt nach, ohne dass die Suche davon wüsste.
 */
const suchAufrufe: Record<string, unknown>[] = []
/** Was ein Treffer-Aufruf zurückgibt; je Test gesetzt. */
let suchAntwort: (koerper: Record<string, unknown>, aufrufe: number) => Promise<Record<string, unknown>> =
  () => Promise.resolve({ treffer: [], vorschlaege: [] })

function koerper(aufruf = 0) {
  return suchAufrufe[aufruf]
}

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  gemerktLeeren()
  api.mockReset()
  suchAufrufe.length = 0
  suchAntwort = () => Promise.resolve({ treffer: [], vorschlaege: [] })
  schutz.stand = schutzMit(false, [])
  schutz.wirft = false
  useTabsStore.setState({ tabs: [tab()], aktivId: 'tab-s', geschlossen: [] })

  api.mockImplementation((pfad: string, optionen?: { body?: string }) => {
    if (pfad === '/browser/suche/autocomplete') return Promise.resolve({ vorschlaege: [] })
    if (pfad === '/browser/suche/wissen') return Promise.resolve({ wissen: null })
    if (pfad === '/browser/suche') {
      const daten = JSON.parse(optionen?.body ?? '{}') as Record<string, unknown>
      suchAufrufe.push(daten)
      return suchAntwort(daten, suchAufrufe.length - 1)
    }
    return Promise.resolve({})
  })
})

describe('MSM-Suche', () => {
  it('zeigt Host, Titel und Text und schickt den Begriff in seiner Schreibweise', async () => {
    suchAntwort = () => Promise.resolve({ treffer: treffer(2), vorschlaege: [] })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
    expect(screen.getByText('seite0.example')).toBeInTheDocument()
    expect(screen.getByText('Text 1')).toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/browser/suche', expect.objectContaining({ method: 'POST' }))
    expect(koerper()).toEqual({ q: 'Wetter Köln', seite: 1, sicher: false, sprache: 'de' })
  })

  it('öffnet einen Treffer im selben Tab, mit Strg in einem neuen', async () => {
    suchAntwort = () => Promise.resolve({ treffer: treffer(2), vorschlaege: [] })
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByText('Titel 1'), { ctrlKey: true })
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual([ADRESSE, 'https://seite1.example/pfad'])
    expect(useTabsStore.getState().aktivId).toBe('tab-s')
    fireEvent.click(screen.getByText('Titel 0'))
    expect(useTabsStore.getState().tabs[0]).toMatchObject({ url: 'https://seite0.example/pfad', vorher: ADRESSE })
  })

  it('hängt weitere Ergebnisse an, ohne doppelte', async () => {
    let aufrufe = 0
    suchAntwort = () => {
      aufrufe += 1
      if (aufrufe === 1) return Promise.resolve({ treffer: treffer(10), vorschlaege: [] })
      return Promise.resolve({ treffer: [...treffer(1, 9), ...treffer(3, 10)], vorschlaege: [] })
    }
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Weitere Ergebnisse' }))
    expect(await screen.findByText('Titel 12')).toBeInTheDocument()
    expect(koerper(1).seite).toBe(2)
    expect(screen.getAllByText('Titel 9')).toHaveLength(1)
    expect(screen.getAllByRole('listitem')).toHaveLength(13)
  })

  it('blättert über die Seitennavigation zu einer bestimmten Seite', async () => {
    let aufrufe = 0
    suchAntwort = () => {
      aufrufe += 1
      if (aufrufe === 1) return Promise.resolve({ treffer: treffer(10), vorschlaege: [] })
      return Promise.resolve({ treffer: treffer(4, 20), vorschlaege: [] })
    }
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByRole('button', { name: '3' }))
    expect(await screen.findByText('Titel 22')).toBeInTheDocument()
    expect(koerper(1)).toMatchObject({ q: 'Wetter Köln', seite: 3 })
  })

  it('sucht nach Zurück nicht noch einmal', async () => {
    suchAntwort = () => Promise.resolve({ treffer: treffer(1), vorschlaege: [] })
    const { unmount } = render(<MsmSuche tab={tab()} />)
    await screen.findByText('Titel 0')
    unmount()
    render(<MsmSuche tab={tab()} />)
    expect(screen.getByText('Titel 0')).toBeInTheDocument()
    expect(suchAufrufe).toHaveLength(1)
  })

  it.each([
    ['Erwachsenen-Inhalte gesperrt', () => (schutz.stand = schutzMit(true, ['erwachsene'])), true],
    ['Schutzdatei unlesbar', () => (schutz.stand = { ...schutzMit(false, []), beschaedigt: true }), true],
    ['Stand nicht zu lesen', () => (schutz.wirft = true), true],
    ['nur Glücksspiel gesperrt', () => (schutz.stand = schutzMit(true, ['gluecksspiel'])), false],
    ['Schutz aus', () => (schutz.stand = schutzMit(false, ['erwachsene'])), false],
  ])('verlangt die sichere Suche: %s', async (_name, vorbereiten, sicher) => {
    vorbereiten()
    suchAntwort = () => Promise.resolve({ treffer: [], vorschlaege: [] })
    render(<MsmSuche tab={tab()} />)
    await waitFor(() => expect(suchAufrufe.length).toBe(1))
    expect(koerper().sicher).toBe(sicher)
  })

  it('nimmt ohne Schutzstand die sichere Suche', () => {
    expect(sichereSuche(null)).toBe(true)
  })

  it('bietet ohne eingerichtete Suche DuckDuckGo an', async () => {
    api.mockImplementation((pfad: string) => {
      if (pfad === '/browser/suche') {
        return Promise.reject(new SanitizedApiError('x', { status: 503, code: 'BROWSER_SUCHE_NICHT_EINGERICHTET' }))
      }
      return Promise.resolve({ wissen: null, vorschlaege: [] })
    })
    render(<MsmSuche tab={tab()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Mit DuckDuckGo suchen' }))
    expect(useTabsStore.getState().tabs[0].url).toBe('https://duckduckgo.com/?q=Wetter%20K%C3%B6ln')
  })

  it('hält eine Instanz, die nicht antwortet, nicht für fehlendes Netz', async () => {
    let aufrufe = 0
    api.mockImplementation((pfad: string) => {
      if (pfad === '/browser/suche') {
        aufrufe += 1
        if (aufrufe === 1) {
          return Promise.reject(new SanitizedApiError('x', { status: 502, code: 'BROWSER_SUCHE_NICHT_ERREICHBAR' }))
        }
        suchAufrufe.push({ q: 'Wetter Köln', seite: 1 })
        return Promise.resolve({ treffer: treffer(1), vorschlaege: [] })
      }
      return Promise.resolve({ wissen: null, vorschlaege: [] })
    })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Suche fehlgeschlagen')).toBeInTheDocument()
    expect(screen.queryByText('Keine Verbindung')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }))
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
  })

  it('sagt, wenn zu viel gesucht wurde', async () => {
    api.mockImplementation((pfad: string) => {
      if (pfad === '/browser/suche') {
        return Promise.reject(new SanitizedApiError('x', { status: 429, code: 'BROWSER_SUCHE_ZU_VIELE' }))
      }
      return Promise.resolve({ wissen: null, vorschlaege: [] })
    })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText(/In einer Minute geht es weiter/)).toBeInTheDocument()
  })

  it('sucht ohne Netz weiter, sobald es zurück ist', async () => {
    let aufrufe = 0
    api.mockImplementation((pfad: string) => {
      if (pfad === '/browser/suche') {
        aufrufe += 1
        if (aufrufe === 1) return Promise.reject(new TypeError('Failed to fetch'))
        suchAufrufe.push({ q: 'Wetter Köln', seite: 1 })
        return Promise.resolve({ treffer: treffer(1), vorschlaege: [] })
      }
      return Promise.resolve({ wissen: null, vorschlaege: [] })
    })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Keine Verbindung')).toBeInTheDocument()
    window.dispatchEvent(new Event('online'))
    expect(await screen.findByText('Titel 0')).toBeInTheDocument()
  })

  it('sagt, wenn es nichts gibt', async () => {
    suchAntwort = () => Promise.resolve({ treffer: [], vorschlaege: [] })
    render(<MsmSuche tab={tab()} />)
    expect(await screen.findByText('Keine Treffer für „Wetter Köln“')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Weitere Ergebnisse' })).not.toBeInTheDocument()
  })
})

describe('Kategorien von Maunting Search', () => {
  it('schickt Bilder, Videos und News als Kategorie an die Suche', async () => {
    suchAntwort = (koerper) => Promise.resolve({ treffer: treffer(1), vorschlaege: [] })
    const { unmount } = render(<MsmSuche tab={tab()} />)
    await screen.findByText('Titel 0')

    fireEvent.click(screen.getByRole('tab', { name: 'Bilder' }))
    await waitFor(() => expect(suchAufrufe.at(-1)).toMatchObject({ kategorie: 'images' }))

    fireEvent.click(screen.getByRole('tab', { name: 'Videos' }))
    await waitFor(() => expect(suchAufrufe.at(-1)).toMatchObject({ kategorie: 'videos' }))

    fireEvent.click(screen.getByRole('tab', { name: 'News' }))
    await waitFor(() => expect(suchAufrufe.at(-1)).toMatchObject({ kategorie: 'news' }))

    unmount()
  })

  it('wechselt die Region über die Sprachauswahl', async () => {
    suchAntwort = () => Promise.resolve({ treffer: treffer(1), vorschlaege: [] })
    const { unmount } = render(<MsmSuche tab={tab()} />)
    await screen.findByText('Titel 0')

    fireEvent.click(screen.getByRole('button', { name: 'Region und Sprache' }))
    fireEvent.click(await screen.findByRole('option', { name: 'International (Englisch)' }))
    await waitFor(() => expect(suchAufrufe.at(-1)).toMatchObject({ sprache: 'en' }))

    unmount()
  })
})

describe('Kontextbezogene Fragen', () => {
  it('zeigt Fragen auch bei kurzen und einzelnen Suchbegriffen', () => {
    // Es gibt keine Sperrliste: welche Vertiefung sinnvoll ist, entscheiden die
    // Vorschläge der Suche. „hallo" liefert ebenso echte wie „spacex".
    expect(kontextFragen('hallo', ['hallo bedeutung duden'], [])[0].frage).toBe('Hallo bedeutung duden')
    expect(kontextFragen('hi', ['hi gadget 2026'], [])[0].frage).toBe('Hi gadget 2026')
    expect(kontextFragen('', ['leere suche hinweis'], [])[0].frage).toBe('Leere suche hinweis')
  })

  it('nimmt echte Suchvorschläge statt Satz-Schablonen', () => {
    const frageliste = kontextFragen('spacex', [
      'spacex starship nächster testflug',
      'spacex aktie kurs',
      'spacex starship nächster testflug',
    ], [])
    expect(frageliste).toHaveLength(2)
    expect(frageliste[0].frage).toBe('Spacex starship nächster testflug')
    expect(frageliste.some((f) => /Was sind die wichtigsten Details/.test(f.frage))).toBe(false)
  })

  it('fällt ohne Vorschläge auf die Titel der Treffer zurück', () => {
    const frageliste = kontextFragen(
      'tauri update fehler',
      [],
      [
        {
          titel: 'Tauri Update schlägt fehl – Windows Signatur prüfen',
          url: 'https://example.com/a',
          inhalt: 'Wenn das Update unter Windows scheitert, prüft der Browser die Signatur des Installern.',
        },
      ],
    )
    expect(frageliste).toHaveLength(1)
    expect(frageliste[0].frage).toBe('Tauri Update schlägt fehl')
    expect(frageliste[0].antwort).toContain('Signatur')
  })
})
