import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const holeLivekitStatus = vi.fn()
const speichereLivekitKonfiguration = vi.fn()
const testeLivekitVerbindung = vi.fn()

vi.mock('@/api/calls', () => ({
  holeLivekitStatus: () => holeLivekitStatus(),
  speichereLivekitKonfiguration: (p: unknown) => speichereLivekitKonfiguration(p),
  testeLivekitVerbindung: (p: unknown) => testeLivekitVerbindung(p),
}))

const darfSchreiben = vi.fn(() => true)
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => darfSchreiben(),
}))

const toastFehler = vi.fn()
vi.mock('@/stores/toastStore', () => ({
  toast: { error: (t: string) => toastFehler(t), success: vi.fn(), info: vi.fn() },
}))

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  // Der Tab liefert zu jedem Schlüssel einen deutschen Standardtext mit; im Test
  // wird genau der geprüft, damit die Zusagen an den Betreiber sichtbar bleiben.
  // Dass dieselben Texte auch in `de.json` stehen, prüft der letzte Block.
  useTranslation: () => ({
    t: (_key: string, fallback?: string, werte?: Record<string, unknown>) => {
      const text = fallback ?? _key
      return werte
        ? text.replace(/\{\{(\w+)\}\}/g, (_treffer, name) => String(werte[name] ?? ''))
        : text
    },
  }),
}))

const { MessengerTab } = await import('./MessengerTab')
const de = (await import('@/locales/de.json')).default as Record<string, any>
const en = (await import('@/locales/en.json')).default as Record<string, any>

const LOKAL_OK = {
  modus: 'lokal' as const,
  url: 'wss://panel.test/livekit',
  konfiguriert: true,
  erreichbar: true,
  fehler: null,
  api_key_maskiert: '*******abcd',
  raeume_aktiv: 2,
}

const EXTERN_OK = {
  ...LOKAL_OK,
  modus: 'extern' as const,
  url: 'wss://projekt.livekit.cloud',
}

function waehleModus(label: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Medienserver' }))
  fireEvent.click(screen.getByRole('option', { name: new RegExp(label, 'i') }))
}

beforeEach(() => {
  vi.clearAllMocks()
  darfSchreiben.mockReturnValue(true)
  holeLivekitStatus.mockResolvedValue(LOKAL_OK)
  speichereLivekitKonfiguration.mockResolvedValue(LOKAL_OK)
  testeLivekitVerbindung.mockResolvedValue({
    erreichbar: true,
    meldung: 'Verbindung steht.',
    raeume_aktiv: 0,
  })
})

describe('Statusstreifen', () => {
  it('meldet einen erreichbaren Anrufserver samt aktiver Räume', async () => {
    render(<MessengerTab />)

    expect(await screen.findByText('Anrufserver erreichbar')).toBeInTheDocument()
    expect(screen.getByText('wss://panel.test/livekit')).toBeInTheDocument()
    expect(screen.getByText(/2 aktive Räume/)).toBeInTheDocument()
  })

  it('nennt den Grund, wenn der Server nicht antwortet', async () => {
    holeLivekitStatus.mockResolvedValue({
      ...LOKAL_OK,
      erreichbar: false,
      fehler: 'LiveKit ist unter dieser Adresse nicht erreichbar.',
    })
    render(<MessengerTab />)

    expect(await screen.findByText('Anrufserver nicht erreichbar')).toBeInTheDocument()
    expect(
      screen.getByText('LiveKit ist unter dieser Adresse nicht erreichbar.'),
    ).toBeInTheDocument()
  })
})

describe('Umschaltung', () => {
  it('zeigt die Zugangsfelder erst im Externmodus', async () => {
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    expect(screen.queryByLabelText('API-Key')).not.toBeInTheDocument()

    waehleModus('Externer Server')
    expect(screen.getByLabelText('Server-URL')).toBeInTheDocument()
    expect(screen.getByLabelText('API-Key')).toBeInTheDocument()
    expect(screen.getByLabelText('API-Secret')).toBeInTheDocument()
  })

  it('speichert den lokalen Modus ohne Zugangsdaten', async () => {
    holeLivekitStatus.mockResolvedValue(EXTERN_OK)
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Integriert')
    fireEvent.click(screen.getByRole('button', { name: /Speichern/ }))

    await waitFor(() =>
      expect(speichereLivekitKonfiguration).toHaveBeenCalledWith({
        modus: 'lokal',
        url: undefined,
        api_key: undefined,
        api_secret: undefined,
      }),
    )
  })

  it('schickt eingetippte Zugangsdaten vollständig hinaus', async () => {
    speichereLivekitKonfiguration.mockResolvedValue(EXTERN_OK)
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Externer Server')
    fireEvent.change(screen.getByLabelText('Server-URL'), {
      target: { value: '  wss://projekt.livekit.cloud  ' },
    })
    fireEvent.change(screen.getByLabelText('API-Key'), { target: { value: 'APIneu' } })
    fireEvent.change(screen.getByLabelText('API-Secret'), { target: { value: 'neu-geheim' } })
    fireEvent.click(screen.getByRole('button', { name: /Speichern/ }))

    await waitFor(() =>
      expect(speichereLivekitKonfiguration).toHaveBeenCalledWith({
        modus: 'extern',
        url: 'wss://projekt.livekit.cloud',
        api_key: 'APIneu',
        api_secret: 'neu-geheim',
      }),
    )
  })

  it('schickt den maskierten Schlüssel niemals zurück', async () => {
    // Sonst landete der Maskentext `*******abcd` als echter Schlüssel in der
    // Datenbank und der Anrufserver wiese jede Anmeldung ab.
    holeLivekitStatus.mockResolvedValue(EXTERN_OK)
    speichereLivekitKonfiguration.mockResolvedValue(EXTERN_OK)
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    expect(screen.getByLabelText('API-Key')).toHaveValue('')
    expect(screen.getByLabelText('API-Key')).toHaveAttribute('placeholder', '*******abcd')

    fireEvent.click(screen.getByRole('button', { name: /Speichern/ }))

    await waitFor(() => expect(speichereLivekitKonfiguration).toHaveBeenCalled())
    const gesendet = speichereLivekitKonfiguration.mock.calls[0][0] as Record<string, unknown>
    expect(gesendet.api_key).toBeUndefined()
    expect(gesendet.api_secret).toBeUndefined()
  })

  it('meldet einen Fehler beim Speichern, statt ihn zu schlucken', async () => {
    speichereLivekitKonfiguration.mockRejectedValue(new Error('Adresse abgelehnt.'))
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Externer Server')
    fireEvent.click(screen.getByRole('button', { name: /Speichern/ }))

    await waitFor(() => expect(toastFehler).toHaveBeenCalledWith('Adresse abgelehnt.'))
  })
})

describe('Verbindung testen', () => {
  it('prüft die eingetippten Werte und zeigt das Ergebnis', async () => {
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Externer Server')
    fireEvent.change(screen.getByLabelText('Server-URL'), {
      target: { value: 'wss://projekt.livekit.cloud' },
    })
    fireEvent.change(screen.getByLabelText('API-Key'), { target: { value: 'APIprobe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verbindung testen' }))

    await waitFor(() =>
      expect(testeLivekitVerbindung).toHaveBeenCalledWith({
        url: 'wss://projekt.livekit.cloud',
        api_key: 'APIprobe',
        api_secret: undefined,
      }),
    )
    expect(await screen.findByText('Verbindung steht.')).toBeInTheDocument()
    // Der Test allein darf nichts speichern.
    expect(speichereLivekitKonfiguration).not.toHaveBeenCalled()
  })

  it('zeigt die Ablehnung im Klartext', async () => {
    testeLivekitVerbindung.mockResolvedValue({
      erreichbar: false,
      meldung: 'API-Key oder API-Secret wurden abgelehnt.',
      raeume_aktiv: 0,
    })
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Externer Server')
    fireEvent.change(screen.getByLabelText('Server-URL'), { target: { value: 'wss://x.test' } })
    fireEvent.change(screen.getByLabelText('API-Key'), { target: { value: 'APIfalsch' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verbindung testen' }))

    expect(
      await screen.findByText('API-Key oder API-Secret wurden abgelehnt.'),
    ).toBeInTheDocument()
  })

  it('bleibt ohne Adresse gesperrt', async () => {
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    waehleModus('Externer Server')
    expect(screen.getByRole('button', { name: 'Verbindung testen' })).toBeDisabled()
  })

  it('testet mit dem gespeicherten Stand, ohne erneutes Eintippen', async () => {
    holeLivekitStatus.mockResolvedValue(EXTERN_OK)
    render(<MessengerTab />)
    await screen.findByText('Anrufserver erreichbar')

    const knopf = screen.getByRole('button', { name: 'Verbindung testen' })
    expect(knopf).not.toBeDisabled()
    fireEvent.click(knopf)

    await waitFor(() =>
      expect(testeLivekitVerbindung).toHaveBeenCalledWith({
        url: 'wss://projekt.livekit.cloud',
        api_key: undefined,
        api_secret: undefined,
      }),
    )
  })
})

describe('Ohne Schreibrecht', () => {
  it('zeigt den Zustand, aber keinen Speichern-Knopf', async () => {
    darfSchreiben.mockReturnValue(false)
    holeLivekitStatus.mockResolvedValue(EXTERN_OK)
    render(<MessengerTab />)

    expect(await screen.findByText('Anrufserver erreichbar')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Speichern/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('API-Key')).toBeDisabled()
  })
})

describe('Datenschutzhinweis', () => {
  it('sagt, was der Medienserver sieht und was nicht', async () => {
    render(<MessengerTab />)

    expect(
      await screen.findByText(/Ende-zu-Ende verschlüsselt/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/welche Kennungen wann in welchem Raum/i)).toBeInTheDocument()
  })
})

describe('Übersetzungen', () => {
  const SCHLUESSEL = [
    'title',
    'description',
    'mode',
    'modeLocal',
    'modeLocalHint',
    'modeExternal',
    'modeExternalHint',
    'url',
    'apiKey',
    'apiSecret',
    'secretStored',
    'test',
    'reachable',
    'unreachable',
    'activeRooms_one',
    'activeRooms_other',
    'saved',
    'metadataHint',
  ]

  it.each(['de', 'en'])('hat alle Texte in %s.json', (sprache) => {
    const quelle = sprache === 'de' ? de : en
    for (const schluessel of SCHLUESSEL) {
      expect(quelle.settings.messenger?.[schluessel], `${sprache}: ${schluessel}`).toBeTruthy()
    }
    expect(quelle.settings.messenger.errors.load).toBeTruthy()
    expect(quelle.settings.tabs.messenger).toBeTruthy()
  })
})
