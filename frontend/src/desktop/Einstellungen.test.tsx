import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockKonfig = {
  backend_url: 'http://localhost:8000',
  sandbox_pfad: 'C:\\Sandbox',
  eingerichtet: true,
  hotkey_fenster: 'Alt+Space',
  hotkey_sprache: 'Alt+Shift+Space',
  wakeword_aktiv: false,
  wakeword_wort: 'Singra',
  audio_eingabe: null,
  audio_ausgabe: null,
  wakeword_schwelle: 0.45,
  audio_echo: true,
  audio_rauschen: true,
  audio_autogain: true,
  audio_verstaerkung: 1,
  computer_use_aktiv: false,
}

const konfigLadenMock = vi.fn().mockResolvedValue(mockKonfig)
const konfigSpeichernMock = vi.fn().mockResolvedValue(undefined)
const oeffneBrowserMock = vi.fn().mockResolvedValue(undefined)

vi.mock('./tauri', () => ({
  konfigLaden: () => konfigLadenMock(),
  konfigSpeichern: (k: unknown) => konfigSpeichernMock(k),
  oeffneBrowser: (url: string) => oeffneBrowserMock(url),
  audioGeraete: vi.fn().mockResolvedValue({ eingaenge: [], ausgaenge: [], standard_eingang: null, standard_ausgang: null }),
  duckingSetzen: vi.fn().mockResolvedValue(undefined),
  hotkeysSetzen: vi.fn().mockResolvedValue(undefined),
  overlayTesten: vi.fn().mockResolvedValue(undefined),
  setzeStatus: vi.fn().mockResolvedValue(undefined),
  sandboxVerfuegbar: vi.fn().mockResolvedValue(true),
  wakewordLauschen: vi.fn().mockResolvedValue(undefined),
  wakewordStand: vi.fn().mockResolvedValue({ aufnahmen: 0, gesamt: 3, schwelle: 0.45, trainiert: false, mikrofon: null, erkannt: null }),
}))

vi.mock('@tauri-apps/plugin-autostart', () => ({
  isEnabled: vi.fn().mockResolvedValue(false),
  enable: vi.fn().mockResolvedValue(undefined),
  disable: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/client', () => ({
  api: vi.fn().mockResolvedValue({ systembereich: 'aus' }),
}))

const mockLegalSettings = {
  imprint_enabled: true,
  imprint_url: 'https://example.com/impressum',
}

vi.mock('@/hooks/usePublicLegalSettings', () => ({
  usePublicLegalSettings: () => mockLegalSettings,
}))

import { Einstellungen } from './Einstellungen'

describe('Einstellungen Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    konfigLadenMock.mockResolvedValue({ ...mockKonfig })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('rendert Reiterleiste inklusive Rechtliches', async () => {
    render(
      <MemoryRouter>
        <Einstellungen />
      </MemoryRouter>,
    )

    expect(await screen.findByText(/mss\.einstellungen\.tab\.desktop/i)).toBeInTheDocument()
    expect(screen.getByText(/mss\.einstellungen\.tab\.wakeword/i)).toBeInTheDocument()
    expect(screen.getByText(/mss\.einstellungen\.tab\.audio/i)).toBeInTheDocument()
    expect(screen.getByText(/mss\.einstellungen\.tab\.rechtliches/i)).toBeInTheDocument()
    expect(screen.getByText(/mss\.einstellungen\.tab\.gefahr/i)).toBeInTheDocument()
  })

  it('wechselt zum Rechtliches-Tab und zeigt Datenschutz und Impressum', async () => {
    render(
      <MemoryRouter initialEntries={['/einstellungen?tab=rechtliches']}>
        <Einstellungen />
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Maunting Studios — Sicherheit braucht Vertrauen/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Datenschutzerklärung/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Betreiber-Impressum/i })).toBeInTheDocument()
    expect(screen.getByText('https://example.com/impressum')).toBeInTheDocument()

    const impressumBtn = screen.getByRole('button', { name: /Impressum im Browser öffnen/i })
    fireEvent.click(impressumBtn)
    expect(oeffneBrowserMock).toHaveBeenCalledWith('https://example.com/impressum')
  })

  it('öffnet Bestätigungsdialog bei Aktivierung von Computer-Use', async () => {
    const onKonfigChange = vi.fn()
    render(
      <MemoryRouter>
        <Einstellungen onKonfigAenderung={onKonfigChange} />
      </MemoryRouter>,
    )

    await waitFor(() => expect(konfigLadenMock).toHaveBeenCalled())

    const switchBtn = await screen.findByRole('switch', { name: /mss\.einstellungen\.computerUse\.titel/i })
    expect(switchBtn).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(switchBtn)

    // Bestätigungsdialog erscheint
    expect(await screen.findByText(/mss\.einstellungen\.computerUse\.aktivierenTitel/i)).toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: /mss\.einstellungen\.computerUse\.aktivierenBestaetigen/i })
    fireEvent.click(confirmBtn)

    await waitFor(() => expect(konfigSpeichernMock).toHaveBeenCalledWith(
      expect.objectContaining({ computer_use_aktiv: true })
    ))
    expect(onKonfigChange).toHaveBeenCalled()
  })
})
