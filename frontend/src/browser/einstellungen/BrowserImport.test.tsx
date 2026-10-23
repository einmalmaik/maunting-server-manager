import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen, antworten } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: unknown }[],
  antworten: {
    profile: [] as unknown,
    import: null as unknown,
    profilFehler: null as string | null,
    importFehler: null as string | null,
  },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args?: unknown) => {
    gerufen.push({ befehl, args })
    if (befehl === 'detect_installed_browsers') {
      return antworten.profilFehler ? Promise.reject(antworten.profilFehler) : Promise.resolve(antworten.profile)
    }
    if (befehl === 'import_browser_data') {
      return antworten.importFehler ? Promise.reject(antworten.importFehler) : Promise.resolve(antworten.import)
    }
    return Promise.resolve(null)
  },
}))

const { BrowserImport } = await import('./BrowserImport')
const { useVerlaufStore } = await import('../services/verlaufStore')
const { useBildStore } = await import('../services/bildStore')
const { useEinstellungenStore, VORGABE_SCHNELLZUGRIFFE } = await import('../services/einstellungenStore')

const PNG = 'data:image/png;base64,iVBORw0KGgo='

const PROFILE = [
  { id: 'chrome:Default', browser: 'chrome', name: 'Person 1', verlauf: true, lesezeichen: true },
  { id: 'firefox:default-release', browser: 'firefox', name: 'Standard', verlauf: true, lesezeichen: true },
]

const ERGEBNIS = {
  browser: 'firefox',
  profil: 'Standard',
  dauer_ms: 1234,
  lesezeichen: [
    { url: 'https://neu.example/seite', titel: 'Neu', ordner: ['Lesezeichenleiste', 'Dev'], zeit: 1000 },
  ],
  verlauf: [{ url: 'https://neu.example/seite', titel: 'Neu', zeit: 2000, anzahl: 4 }],
  top_sites: [{ url: 'https://top.example/', titel: 'Top' }],
  bilder: [{ herkunft: 'https://neu.example', daten: PNG }],
  zahlen: { lesezeichen: 1, verlauf: 1, top_sites: 1, bilder: 1 },
  warnungen: [{ code: 'verlauf_gekuerzt' }],
}

const zeigen = () => render(<BrowserImport />)

describe('Browser-Daten importieren', () => {
  beforeEach(() => {
    gerufen.length = 0
    antworten.profile = PROFILE
    antworten.import = ERGEBNIS
    antworten.profilFehler = null
    antworten.importFehler = null
    localStorage.clear()
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useVerlaufStore.setState({ verlauf: [], lesezeichen: [] })
    useBildStore.setState({ bilder: {} })
    useEinstellungenStore.setState({ schnellzugriffe: VORGABE_SCHNELLZUGRIFFE })
  })

  it('lädt Profile, importiert das gewählte und zeigt die übernommenen Zahlen', async () => {
    zeigen()
    const auswahl = await screen.findByRole('button', { name: 'Browser und Profil' })
    fireEvent.click(auswahl)
    fireEvent.click(screen.getByRole('option', { name: 'Mozilla Firefox — Standard' }))
    fireEvent.click(screen.getByRole('button', { name: 'Alles importieren' }))

    expect(await screen.findByText('Übernommen aus Mozilla Firefox (Standard)')).toBeInTheDocument()
    expect(gerufen.filter((g) => g.befehl === 'import_browser_data')).toEqual([
      { befehl: 'import_browser_data', args: { profilId: 'firefox:default-release' } },
    ])
    expect(screen.getByText('Der Verlauf war länger als 5000 Einträge. Ältere wurden nicht übernommen.')).toBeInTheDocument()

    expect(useVerlaufStore.getState().lesezeichen).toEqual([
      expect.objectContaining({
        url: 'https://neu.example/seite',
        ordner: ['Lesezeichenleiste', 'Dev'],
        favicon: 'https://neu.example',
      }),
    ])
    expect(useVerlaufStore.getState().verlauf[0]).toMatchObject({ anzahl: 4, zeit: 2000 })
    expect(useEinstellungenStore.getState().schnellzugriffe).toEqual([{ url: 'https://top.example/', titel: 'Top' }])
    expect(useBildStore.getState().bilder).toEqual({ 'https://neu.example': PNG })
    expect(gerufen).toContainEqual({
      befehl: 'kachel_symbole_behalten',
      args: { adressen: ['https://top.example/'] },
    })
  })

  it('zeigt während des Imports einen Spinner und sperrt den Knopf', async () => {
    let fertig: (wert: unknown) => void = () => {}
    antworten.import = new Promise((aufloesen) => {
      fertig = aufloesen
    })
    zeigen()
    await screen.findByRole('button', { name: 'Browser und Profil' })
    fireEvent.click(screen.getByRole('button', { name: 'Alles importieren' }))

    const knopf = screen.getByRole('button', { name: 'Wird importiert …' })
    expect(knopf).toBeDisabled()
    fertig(ERGEBNIS)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Wird importiert …' })).not.toBeInTheDocument())
  })

  it('meldet einen gesperrten Quell-Browser verständlich', async () => {
    antworten.importFehler = 'gesperrt'
    zeigen()
    await screen.findByRole('button', { name: 'Browser und Profil' })
    fireEvent.click(screen.getByRole('button', { name: 'Alles importieren' }))

    expect(
      await screen.findByText('Der Quell-Browser hält seine Daten gerade fest. Schließe ihn kurz und versuche es erneut.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Alles importieren' })).toBeEnabled()
  })

  it('sagt, wenn kein unterstützter Browser da ist', async () => {
    antworten.profile = []
    zeigen()
    expect(await screen.findByText('Kein unterstützter Browser mit einem benutzbaren Profil gefunden.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Alles importieren' })).not.toBeInTheDocument()
  })

  it('bietet nach einem Ladefehler einen neuen Versuch an', async () => {
    antworten.profilFehler = 'kaputt'
    zeigen()
    expect(await screen.findByText('Die installierten Browser ließen sich nicht ermitteln.')).toBeInTheDocument()
    antworten.profilFehler = null
    fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }))
    expect(await screen.findByRole('button', { name: 'Browser und Profil' })).toBeInTheDocument()
  })
})
