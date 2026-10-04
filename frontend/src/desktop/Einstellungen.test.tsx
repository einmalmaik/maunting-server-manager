import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

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
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
}))

// Wake-Word und Messenger-PIN stehen jetzt in Reitern, die diese Tests öffnen.
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn().mockResolvedValue(() => {}) }))

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

// Was die Karte exportieren würde; den Export selbst prüft `datenexport.test.ts`.
let exportTresor: { eintraege: () => { category?: string }[] } | undefined
vi.mock('@/pages/profile/DatenexportKarte', () => ({
  DatenexportKarte: (props: { tresor?: typeof exportTresor }) => {
    exportTresor = props.tresor
    return null
  },
}))

import i18n from '@/i18n'
import { api } from '@/api/client'
import { usePublicSettingsStore, DEFAULT_PUBLIC_SETTINGS } from '@/stores/publicSettingsStore'
import { Einstellungen } from './Einstellungen'
import { useVaultStore, type VaultItem } from './vault/vaultStore'

/**
 * Der sichtbare Text zu einem Schlüssel.
 *
 * Diese Datei suchte früher nach den Schlüsseln selbst (`profile.tabs.account`).
 * Das ging nur, solange die Übersetzungen im Test gar nicht geladen waren —
 * die Oberfläche zeigte dann ihre eigenen Schlüssel, und der Test hielt genau
 * diesen Zustand fest. Seit `@/i18n` mit im Bündel liegt, steht dort ein Satz,
 * und die Suche lief ins Leere. Über `t()` bleibt die Zusage dieselbe („dieser
 * Reiter ist da"), ohne einen Wortlaut festzunageln.
 */
const txt = (schluessel: string) => i18n.t(schluessel)

describe('Einstellungen Component', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    vi.clearAllMocks()
    konfigLadenMock.mockResolvedValue({ ...mockKonfig })
    usePublicSettingsStore.setState({ ...DEFAULT_PUBLIC_SETTINGS, isLoading: false, error: null })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('gibt die Schlüssel der Kamera-Sicherung nicht in den Datenexport', async () => {
    usePublicSettingsStore.setState({ ...DEFAULT_PUBLIC_SETTINGS, vault_enabled: true, isLoading: false, error: null })
    useVaultStore.setState({
      isUnlocked: true,
      items: [
        { id: 'a', service: 'Bank', category: 'login' } as VaultItem,
        { id: 'b', service: 'Posteingang', category: 'sicherung' } as VaultItem,
      ],
    })
    render(
      <MemoryRouter initialEntries={['/einstellungen?tab=konto']}>
        <Einstellungen />
      </MemoryRouter>,
    )
    await waitFor(() => expect(exportTresor).toBeDefined())
    expect(exportTresor!.eintraege().map((e) => e.category)).toEqual(['login'])
    useVaultStore.setState({ isUnlocked: false, items: [] })
  })

  it('rendert Reiterleiste inklusive Rechtliches', async () => {
    render(
      <MemoryRouter>
        <Einstellungen />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('tab', { name: txt('profile.tabs.account') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('profile.tabs.social') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.desktop') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('profile.tabs.security') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.audio') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.rechtliches') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.gefahr') })).toBeInTheDocument()
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

    const switchBtn = await screen.findByRole('switch', { name: txt('mss.einstellungen.computerUse.titel') })
    expect(switchBtn).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(switchBtn)

    // Bestätigungsdialog erscheint
    expect(await screen.findByText(txt('mss.einstellungen.computerUse.aktivierenTitel'))).toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: txt('mss.einstellungen.computerUse.aktivierenBestaetigen') })
    fireEvent.click(confirmBtn)

    await waitFor(() => expect(konfigSpeichernMock).toHaveBeenCalledWith(
      expect.objectContaining({ computer_use_aktiv: true })
    ))
    expect(onKonfigChange).toHaveBeenCalled()
  })

  it('deaktiviert Computer-Use Schalter auf Android und zeigt Hinweis', async () => {
    const originalUserAgent = navigator.userAgent
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36',
      configurable: true,
    })

    render(
      <MemoryRouter>
        <Einstellungen />
      </MemoryRouter>,
    )

    await waitFor(() => expect(konfigLadenMock).toHaveBeenCalled())

    const switchBtn = await screen.findByRole('switch', { name: txt('mss.einstellungen.computerUse.titel') })
    expect(switchBtn).toBeDisabled()
    expect(switchBtn).toHaveAttribute('aria-checked', 'false')

    expect(screen.getByText(txt('mss.einstellungen.computerUse.statusNichtVerfuegbar'))).toBeInTheDocument()
    expect(screen.getByText(txt('mss.einstellungen.computerUse.androidHinweis'))).toBeInTheDocument()

    Object.defineProperty(navigator, 'userAgent', {
      value: originalUserAgent,
      configurable: true,
    })
  })

  it('blendet Social- und Sicherheits-Reiter aus, wenn Social und Tresor deaktiviert sind', async () => {
    usePublicSettingsStore.setState({
      ...DEFAULT_PUBLIC_SETTINGS,
      social_enabled: false,
      vault_enabled: false,
    })

    render(
      <MemoryRouter>
        <Einstellungen />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('tab', { name: txt('profile.tabs.account') })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: txt('profile.tabs.social') })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: txt('profile.tabs.security') })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.desktop') })).toBeInTheDocument()
  })

  it('leitet bei direktem Aufruf eines deaktivierten Reiters auf desktop um', async () => {
    usePublicSettingsStore.setState({
      ...DEFAULT_PUBLIC_SETTINGS,
      social_enabled: false,
      vault_enabled: false,
    })

    render(
      <MemoryRouter initialEntries={['/einstellungen?tab=social']}>
        <Einstellungen />
      </MemoryRouter>,
    )

    await waitFor(() => expect(konfigLadenMock).toHaveBeenCalled())
    expect(screen.getByRole('tab', { name: txt('mss.einstellungen.tab.desktop'), selected: true })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: txt('profile.tabs.social') })).not.toBeInTheDocument()
  })

  it.each([
    ['wakeword', 'mss.einstellungen.tab.audio'],
    ['messenger', 'profile.tabs.security'],
    ['tresor', 'profile.tabs.security'],
    ['profil', 'profile.tabs.account'],
  ])('ein alter Link ?tab=%s führt zum neuen Reiter', async (alt, reiter) => {
    render(
      <MemoryRouter initialEntries={[`/einstellungen?tab=${alt}`]}>
        <Einstellungen />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('tab', { name: txt(reiter), selected: true })).toBeInTheDocument()
  })

  describe('ohne Server (offline)', () => {
    const zeichne = (ziel: string, offline = true) =>
      render(
        <MemoryRouter initialEntries={[ziel]}>
          <Einstellungen offline={offline} />
        </MemoryRouter>,
      )

    beforeEach(() => {
      exportTresor = undefined
    })

    it.each(['konto', 'social'])('?tab=%s zeigt den Serverhinweis statt der Karten und fragt nichts an', async (reiter) => {
      zeichne(`/einstellungen?tab=${reiter}`)

      expect(await screen.findByText(txt('mss.einstellungen.offline.titel'))).toBeInTheDocument()
      expect(screen.getByRole('status')).toHaveTextContent(txt('mss.einstellungen.offline.text'))
      // Der Reiter selbst bleibt gewählt: niemand wird stumm woandershin geschoben.
      const name = reiter === 'konto' ? 'profile.tabs.account' : 'profile.tabs.social'
      expect(screen.getByRole('tab', { name: txt(name), selected: true })).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: txt('profile.timezoneTitle') })).not.toBeInTheDocument()
      expect(exportTresor).toBeUndefined()
      expect(api).not.toHaveBeenCalled()
    })

    it('alle Reiter bleiben offline sichtbar', async () => {
      zeichne('/einstellungen')
      for (const name of [
        'profile.tabs.account', 'profile.tabs.security', 'profile.tabs.social', 'mss.einstellungen.tab.desktop',
        'mss.einstellungen.tab.audio', 'mss.einstellungen.tab.rechtliches', 'mss.einstellungen.tab.gefahr',
      ]) {
        expect(await screen.findByRole('tab', { name: txt(name) })).toBeInTheDocument()
      }
    })

    it('Desktop: lokale Schalter speichern, der Systembereich des Panels wird nicht angefragt', async () => {
      zeichne('/einstellungen?tab=desktop')

      const schalter = await screen.findByRole('switch', { name: txt('mss.einstellungen.computerUse.titel') })
      fireEvent.click(schalter)
      fireEvent.click(await screen.findByRole('button', { name: txt('mss.einstellungen.computerUse.aktivierenBestaetigen') }))

      await waitFor(() => expect(konfigSpeichernMock).toHaveBeenCalledWith(
        expect.objectContaining({ computer_use_aktiv: true }),
      ))
      expect(api).not.toHaveBeenCalledWith('/ai/settings/desktop')
      expect(screen.queryByText(txt('mss.systembereich.titel'))).not.toBeInTheDocument()
      expect(screen.queryByText(txt('mss.einstellungen.offline.titel'))).not.toBeInTheDocument()
    })

    it('Sicherheit ist offline kein Serverreiter: Messenger-PIN und Tresor stehen da', async () => {
      usePublicSettingsStore.setState({ ...DEFAULT_PUBLIC_SETTINGS, social_enabled: true, vault_enabled: true, isLoading: false, error: null })
      zeichne('/einstellungen?tab=sicherheit')

      expect(await screen.findByRole('heading', { name: txt('mss.einstellungen.sicherheit.messenger') })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: txt('mss.einstellungen.sicherheit.tresor') })).toBeInTheDocument()
      expect(screen.queryByText(txt('mss.einstellungen.offline.titel'))).not.toBeInTheDocument()
    })

    it('Rechtliches nennt ein unbekanntes Impressum nicht „inaktiv“', async () => {
      const vorher = { ...mockLegalSettings }
      mockLegalSettings.imprint_enabled = false
      mockLegalSettings.imprint_url = ''
      try {
        zeichne('/einstellungen?tab=rechtliches')
        expect(await screen.findByRole('heading', { name: /Betreiber-Impressum/i })).toBeInTheDocument()
        expect(screen.queryByText(txt('mss.einstellungen.rechtliches.impressumInaktiv'))).not.toBeInTheDocument()
        expect(screen.getByText(txt('common.offline'))).toBeInTheDocument()
      } finally {
        Object.assign(mockLegalSettings, vorher)
      }
    })

    it('kommt der Server zurück, erscheint das Konto ohne Neuladen — und verschwindet beim nächsten Ausfall wieder', async () => {
      const { rerender } = zeichne('/einstellungen?tab=konto')
      expect(await screen.findByText(txt('mss.einstellungen.offline.titel'))).toBeInTheDocument()

      rerender(
        <MemoryRouter initialEntries={['/einstellungen?tab=konto']}>
          <Einstellungen offline={false} />
        </MemoryRouter>,
      )
      expect(await screen.findByRole('heading', { name: txt('profile.timezoneTitle') })).toBeInTheDocument()
      expect(screen.queryByText(txt('mss.einstellungen.offline.titel'))).not.toBeInTheDocument()

      rerender(
        <MemoryRouter initialEntries={['/einstellungen?tab=konto']}>
          <Einstellungen offline />
        </MemoryRouter>,
      )
      expect(await screen.findByText(txt('mss.einstellungen.offline.titel'))).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: txt('profile.timezoneTitle') })).not.toBeInTheDocument()
    })

    it('online zeigt kein Reiter den Serverhinweis', async () => {
      zeichne('/einstellungen?tab=konto', false)
      expect(await screen.findByRole('heading', { name: txt('profile.timezoneTitle') })).toBeInTheDocument()
      expect(screen.queryByText(txt('mss.einstellungen.offline.titel'))).not.toBeInTheDocument()
    })
  })
})
