import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({ api: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))
vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))

const { SanitizedApiError } = await import('@/api/client')
const { Widgets, alter } = await import('./Widgets')
const { SPIEGEL, auswahl, nachrichtenVergessen, standLesen } = await import('./nachrichtenDaten')
const { useEinstellungenStore, NACHRICHTEN_THEMEN } = await import('../../services/einstellungenStore')
const { useSitzung } = await import('../../services/sitzung')

const meldung = (url: string, titel: string, zeit: string | null = null, inhalt = '') => ({ url, titel, zeit, inhalt })

const STAND = {
  stand: '2026-10-09T10:00:00+00:00',
  themen: {
    technik: [meldung('https://heise.example/linux', 'Neuer Linux-Kernel', '2026-10-09T09:00:00+00:00'), meldung('https://a.example/x', 'Chip-Mangel', '2026-10-09T06:00:00+00:00')],
    sport: [meldung('https://kicker.example/1', 'Pokalspiel', '2026-10-09T09:30:00+00:00'), meldung('https://heise.example/linux', 'Neuer Linux-Kernel', '2026-10-09T09:00:00+00:00')],
    politik: [meldung('https://b.example/p', 'Haushalt', null, 'Linux im Bundestag')],
  },
}

const zeigen = () =>
  render(
    <MemoryRouter>
      <Widgets privat={false} />
    </MemoryRouter>,
  )

beforeEach(() => {
  localStorage.clear()
  nachrichtenVergessen()
  api.mockReset()
  useEinstellungenStore.setState({ widgetOrdnung: [], widgetsAus: ['uhr', 'termine', 'notizen', 'zuletzt'], nachrichtenThemen: NACHRICHTEN_THEMEN, nachrichtenWorte: '' })
  useSitzung.setState({ stand: 'an' })
})

describe('Auswahl der Nachrichten', () => {
  const stand = standLesen(STAND, 'de')!

  it('nimmt die neuesten aus den gewählten Themen, ohne doppelte', () => {
    expect(auswahl(stand, ['technik', 'sport'], '').map((m) => m.titel)).toEqual(['Pokalspiel', 'Neuer Linux-Kernel', 'Chip-Mangel'])
    expect(auswahl(stand, ['politik'], '').map((m) => m.titel)).toEqual(['Haushalt'])
    expect(auswahl(stand, [], '')).toEqual([])
  })

  it('filtert nach Schlagworten in Titel und Text, ohne Groß- und Kleinschreibung', () => {
    expect(auswahl(stand, NACHRICHTEN_THEMEN, ' LINUX , ').map((m) => m.titel)).toEqual(['Neuer Linux-Kernel', 'Haushalt'])
    expect(auswahl(stand, NACHRICHTEN_THEMEN, 'pokal, chip').map((m) => m.titel)).toEqual(['Pokalspiel', 'Chip-Mangel'])
  })

  it('übernimmt aus Antwort und Spiegel nur Meldungen mit https-Adresse und passenden Typen', () => {
    const gelesen = standLesen(
      {
        themen: {
          technik: [meldung('http://a.example/', 'A'), { url: 'https://b.example/', titel: 5 }, meldung('https://c.example/', 'C'), null],
          sport: 'kaputt',
          unbekannt: [meldung('https://d.example/', 'D')],
        },
      },
      'de',
    )
    expect(gelesen?.themen).toEqual({ technik: [meldung('https://c.example/', 'C')] })
    expect(standLesen(null, 'de')).toBeNull()
    expect(standLesen({ themen: 'x' }, 'de')).toBeNull()
  })

  it('nennt das Alter in der App-Sprache', () => {
    const jetzt = Date.parse('2026-10-09T12:00:00Z')
    expect(alter('2026-10-09T09:00:00Z', jetzt, 'de')).toBe('vor 3 Stunden')
    expect(alter('2026-10-09T11:55:00Z', jetzt, 'en')).toBe('5 minutes ago')
    expect(alter(null, jetzt, 'de')).toBeNull()
    expect(alter('gestern', jetzt, 'de')).toBeNull()
  })
})

describe('Nachrichten-Widget', () => {
  it('zeigt gekoppelt die Schlagzeilen mit Host und legt sie als Spiegel ab', async () => {
    api.mockResolvedValueOnce(STAND)
    zeigen()
    expect(await screen.findByText('Pokalspiel')).toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/browser/nachrichten?sprache=de')
    expect(screen.getByText(/^kicker\.example · /)).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(SPIEGEL)!).themen.sport).toHaveLength(2)
  })

  it('zeigt zuerst den Spiegel und fragt höchstens alle 30 Minuten nach', async () => {
    localStorage.setItem(SPIEGEL, JSON.stringify({ sprache: 'de', themen: STAND.themen }))
    api.mockResolvedValue(STAND)
    const { unmount } = zeigen()
    expect(screen.getByText('Haushalt')).toBeInTheDocument()
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    unmount()
    zeigen()
    expect(screen.getByText('Haushalt')).toBeInTheDocument()
    expect(api).toHaveBeenCalledTimes(1)
  })

  it('fragt ungekoppelt nicht und zeigt sich nicht', () => {
    useSitzung.setState({ stand: 'aus' })
    zeigen()
    expect(screen.queryByText('Nachrichten')).not.toBeInTheDocument()
    expect(api).not.toHaveBeenCalled()
  })

  it('zeigt sich in privaten Tabs nicht', () => {
    render(
      <MemoryRouter>
        <Widgets privat />
      </MemoryRouter>,
    )
    expect(screen.queryByText('Nachrichten')).not.toBeInTheDocument()
    expect(api).not.toHaveBeenCalled()
  })

  it('sagt, wenn nichts zu den Schlagworten passt', async () => {
    useEinstellungenStore.setState({ nachrichtenWorte: 'Raumfahrt' })
    api.mockResolvedValueOnce(STAND)
    zeigen()
    expect(await screen.findByText('Heute nichts zu deinen Schlagworten')).toBeInTheDocument()
  })

  it('sagt ohne Stand, dass keine Suche eingerichtet ist', async () => {
    api.mockRejectedValueOnce(new SanitizedApiError('x', { status: 503, code: 'BROWSER_NACHRICHTEN_NICHT_EINGERICHTET' }))
    zeigen()
    expect(await screen.findByText('Auf dem MSM-Server läuft keine SearXNG-Instanz.')).toBeInTheDocument()
  })

  it('behält den Spiegel, wenn der Server nicht antwortet', async () => {
    localStorage.setItem(SPIEGEL, JSON.stringify({ sprache: 'de', themen: STAND.themen }))
    api.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    zeigen()
    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(screen.getByText('Pokalspiel')).toBeInTheDocument()
    expect(screen.queryByText('Nachrichten gerade nicht erreichbar')).not.toBeInTheDocument()
  })
})
