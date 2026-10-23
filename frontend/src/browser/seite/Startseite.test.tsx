import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({ lage: 'anheftbar', gerufen: [] as string[] }))
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string) => {
    nativ.gerufen.push(befehl)
    if (befehl === 'widget_lage') return Promise.resolve(nativ.lage)
    if (befehl === 'widget_anheften') return Promise.resolve(true)
    return Promise.resolve(null)
  },
}))
vi.mock('@/lib/offlineSync', () => ({
  kalenderVorkommenLokal: () => [],
  loadCalendarEventsOfflineFirst: () => Promise.resolve({ events: [], isOffline: false }),
  getOfflineNotes: () => [],
  loadNotesOfflineFirst: () => Promise.resolve({ notes: [], isOffline: false }),
}))
vi.mock('@/services/notesCalendarCrypto', () => ({ NOTE_CIPHERTEXT_PREFIX: 'sv-note-v1:', CALENDAR_CIPHERTEXT_PREFIX: 'sv-cal-v1:' }))
vi.mock('../services/lokalesWhisper', () => ({
  vorwaermenWhisperModell: vi.fn(),
  istLokalesWhisperMoeglich: () => false,
  ladeWhisperModell: vi.fn(),
  starteAudioAufnahme: vi.fn(),
  transkribiereAudioMitWhisper: vi.fn(),
}))

const { Startseite } = await import('./Startseite')
const { useEinstellungenStore } = await import('../services/einstellungenStore')
const { vorwaermenWhisperModell } = await import('../services/lokalesWhisper')

const UA = navigator.userAgent
const alsAndroid = (an: boolean) =>
  Object.defineProperty(navigator, 'userAgent', {
    value: an ? 'Mozilla/5.0 (Linux; Android 15; Pixel) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36' : UA,
    configurable: true,
  })
const zeigen = (privat = false) => render(<MemoryRouter><Startseite privat={privat} /></MemoryRouter>)

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  useEinstellungenStore.setState({ widgetAngeboten: false })
  nativ.lage = 'anheftbar'
  nativ.gerufen.length = 0
  vi.mocked(vorwaermenWhisperModell).mockClear()
})
afterEach(() => alsAndroid(false))

describe('Suche auf der Startseite', () => {
  it('am Handy integrierte Suchleiste mit Suchmaschinen-Logo und Mikrofon', () => {
    alsAndroid(true)
    zeigen()
    expect(screen.getByRole('textbox', { name: 'Suchen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Suchmaschine' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sprachsuche/i })).toBeInTheDocument()
  })

  it('am Rechner integrierte Suchleiste mit Suchmaschinen-Logo und Mikrofon', () => {
    zeigen()
    expect(screen.getByRole('textbox', { name: 'Suchen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Suchmaschine' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sprachsuche/i })).toBeInTheDocument()
  })

  it('lädt am Handy das lokale Whisper-Modell nicht vor', async () => {
    alsAndroid(true)
    zeigen()
    await vi.waitFor(() => expect(vorwaermenWhisperModell).not.toHaveBeenCalled())
    alsAndroid(false)
    zeigen()
    await vi.waitFor(() => expect(vorwaermenWhisperModell).toHaveBeenCalled())
  })
})

describe('Such-Widget anbieten', () => {
  it('bietet das Widget einmal an und bittet den Startbildschirm darum', async () => {
    alsAndroid(true)
    const { unmount } = zeigen()
    fireEvent.click(await screen.findByRole('button', { name: 'Widget hinzufügen' }))
    expect(nativ.gerufen).toContain('widget_anheften')
    expect(screen.queryByRole('button', { name: 'Widget hinzufügen' })).not.toBeInTheDocument()
    unmount()
    zeigen()
    await Promise.resolve()
    expect(screen.queryByRole('button', { name: 'Widget hinzufügen' })).not.toBeInTheDocument()
  })

  it('fragt nicht, wenn das Widget schon liegt, und merkt sich das', async () => {
    alsAndroid(true)
    nativ.lage = 'liegt'
    zeigen()
    await vi.waitFor(() => expect(useEinstellungenStore.getState().widgetAngeboten).toBe(true))
    expect(screen.queryByRole('button', { name: 'Widget hinzufügen' })).not.toBeInTheDocument()
  })

  it('nicht im privaten Tab und nicht am Rechner', async () => {
    alsAndroid(true)
    const { unmount } = zeigen(true)
    unmount()
    alsAndroid(false)
    zeigen()
    await Promise.resolve()
    expect(nativ.gerufen).not.toContain('widget_lage')
  })
})
