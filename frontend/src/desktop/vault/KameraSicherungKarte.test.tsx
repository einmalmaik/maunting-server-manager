/**
 * Die Karte der Kamera-Sicherung: einschalten nur bei offenem Tresor und mit
 * frischem Nachweis in der Karte, „Speicher freigeben“ nur nach Rückfrage.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { formatBytes } from '@/lib/format'
import { useAuthStore } from '@/stores/authStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { useToastStore } from '@/stores/toastStore'
import * as tauri from '@/desktop/tauri'
import type { KameraStand } from '@/desktop/tauri'
import * as kamera from './kameraSicherung'
import { KameraSicherungKarte } from './KameraSicherungKarte'
import { useVaultStore } from './vaultStore'

vi.mock('@/desktop/tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  medienZugriff: vi.fn(),
  medienEinstellungen: vi.fn(async () => {}),
}))

vi.mock('./kameraSicherung', async () => {
  const { create } = await import('zustand')
  return {
    useKameraSicherung: create(() => ({ stand: null })),
    kameraStandLaden: vi.fn(async () => null),
    kameraEinschalten: vi.fn(),
    kameraAusschalten: vi.fn(),
    kameraNurWlan: vi.fn(),
    kameraScreenshots: vi.fn(async () => {}),
    kameraVorhandeneSichern: vi.fn(),
    freigebbar: vi.fn(),
    speicherFreigeben: vi.fn(async () => true),
  }
})

const BUCKET = 'k'.repeat(64)
const STAND: KameraStand = {
  eingerichtet: true,
  konto: 1,
  bucket: BUCKET,
  geraet: 'g',
  nurWlan: false,
  screenshots: false,
  gesichert: 0,
  zuletzt: 0,
  offen: 0,
}
// Zusammengesetzt, damit im öffentlichen Repo kein Passwort-Literal steht.
const PASSWORT = 'Konto-' + 'Passwort'

function zeigen() {
  return render(
    <MemoryRouter>
      <KameraSicherungKarte />
      <ConfirmDialog />
    </MemoryRouter>,
  )
}

const schalter = () => screen.getByRole('switch', { name: i18n.t('mss.vault.kamera.schalter') })
const toasts = () => useToastStore.getState().toasts.map((t) => t.message)

function anmelden(mehr: Record<string, unknown> = {}) {
  useAuthStore.setState({ user: { id: 1, has_password: true, two_factor_enabled: false, ...mehr } as never })
}

beforeEach(() => {
  vi.clearAllMocks()
  useToastStore.setState({ toasts: [] })
  useConfirmStore.setState({ pending: null })
  vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
  kamera.useKameraSicherung.setState({ stand: null })
  useVaultStore.setState({ isUnlocked: true, bucketId: BUCKET })
  anmelden()
})

describe('Einschalten', () => {
  it('lässt sich bei gesperrtem Tresor nicht einschalten und sagt, warum', () => {
    useVaultStore.setState({ isUnlocked: false })
    zeigen()
    expect(schalter()).toBeDisabled()
    expect(screen.getByText(i18n.t('mss.vault.kamera.gesperrt'))).toBeInTheDocument()
  })

  it('fragt in der Karte nach dem Passwort und schaltet erst mit ihm ein', async () => {
    vi.mocked(kamera.kameraEinschalten).mockResolvedValue('ok')
    zeigen()
    fireEvent.click(schalter())
    expect(kamera.kameraEinschalten).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.kamera.passwortLabel')), { target: { value: PASSWORT } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.einschalten') }))
    await waitFor(() => expect(kamera.kameraEinschalten).toHaveBeenCalledWith(BUCKET, { password: PASSWORT }))
    await waitFor(() => expect(screen.queryByLabelText(i18n.t('mss.vault.kamera.passwortLabel'))).toBeNull())
  })

  it('schickt mit eingerichteter App den Code mit', async () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['totp'] })
    vi.mocked(kamera.kameraEinschalten).mockResolvedValue('ok')
    zeigen()
    fireEvent.click(schalter())
    const knopf = screen.getByRole('button', { name: i18n.t('mss.vault.kamera.einschalten') })
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.kamera.passwortLabel')), { target: { value: PASSWORT } })
    expect(knopf).toBeDisabled()
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.kamera.otpLabel')), { target: { value: '123456' } })
    fireEvent.click(knopf)
    await waitFor(() =>
      expect(kamera.kameraEinschalten).toHaveBeenCalledWith(BUCKET, { password: PASSWORT, otp_code: '123456' }),
    )
  })

  it('sagt, wenn nur ausgewählte Fotos freigegeben sind', async () => {
    vi.mocked(kamera.kameraEinschalten).mockResolvedValue('teilweise')
    zeigen()
    fireEvent.click(schalter())
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.kamera.passwortLabel')), { target: { value: PASSWORT } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.einschalten') }))
    await waitFor(() => expect(toasts()).toContain(i18n.t('mss.vault.kamera.zugriff.teilweise')))
  })

  it('fragt an derselben Stelle neu, wenn der Zugang weggefallen ist', () => {
    kamera.useKameraSicherung.setState({ stand: { ...STAND, warten: 'zugang' } })
    zeigen()
    expect(screen.getByText(i18n.t('mss.vault.kamera.warten.zugang'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.neuBestaetigen') })).toBeInTheDocument()
  })

  it('zählt einen Job für einen anderen Tresor nicht als eingeschaltet', () => {
    kamera.useKameraSicherung.setState({ stand: { ...STAND, bucket: 'x'.repeat(64) } })
    zeigen()
    expect(schalter()).not.toBeChecked()
  })

  it('schaltet aus, ohne zu fragen', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    zeigen()
    fireEvent.click(schalter())
    await waitFor(() => expect(kamera.kameraAusschalten).toHaveBeenCalled())
  })
})

describe('Eingeschaltet', () => {
  it('zeigt WLAN-Schalter, Stand, offene Aufnahmen und den fehlenden Zugriff mit Abhilfe', async () => {
    kamera.useKameraSicherung.setState({
      stand: { ...STAND, gesichert: 2, zuletzt: Date.UTC(2026, 9, 3, 12), offen: 3, warten: 'zugriff' },
    })
    zeigen()
    expect(schalter()).toBeChecked()
    expect(screen.getByRole('switch', { name: i18n.t('mss.vault.kamera.nurWlan') })).toBeInTheDocument()
    expect(screen.getByText(/^2 Aufnahmen gesichert/)).toHaveTextContent(i18n.t('mss.vault.kamera.offen', { count: 3 }))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.zugriffErteilen') }))
    await waitFor(() => expect(tauri.medienZugriff).toHaveBeenLastCalledWith(true))
    await waitFor(() => expect(kamera.kameraStandLaden).toHaveBeenCalledTimes(2))
  })

  it('sagt, wenn das Telefon die App im Hintergrund anhält, und führt in die Einstellungen', async () => {
    // Samsung „Eingeschränkt“, Xiaomi-Energiesparen: der Job läuft erst mit offener App.
    kamera.useKameraSicherung.setState({ stand: { ...STAND, warten: 'akku' } })
    zeigen()
    expect(screen.getByText(i18n.t('mss.vault.kamera.warten.akku'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.einstellungenOeffnen') }))
    await waitFor(() => expect(tauri.medienEinstellungen).toHaveBeenCalled())
  })

  it('schaltet Screenshots mit eigenem Schalter dazu, standardmäßig aus', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    zeigen()
    const knopf = screen.getByRole('switch', { name: i18n.t('mss.vault.kamera.screenshots') })
    expect(knopf).not.toBeChecked()
    fireEvent.click(knopf)
    await waitFor(() => expect(kamera.kameraScreenshots).toHaveBeenCalledWith(true))
  })

  it('gibt Speicher erst nach der Rückfrage frei, mit Zahl und Größe', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    vi.mocked(kamera.freigebbar).mockResolvedValue({ bilder: [1, 2], videos: [3], bytes: 3 * 1024 * 1024 })
    zeigen()
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    const dialog = await screen.findByRole('dialog')
    expect(kamera.freigebbar).toHaveBeenCalledWith(BUCKET)
    expect(dialog).toHaveTextContent(`3 gesicherte Aufnahmen (${formatBytes(3 * 1024 * 1024)})`)
    expect(kamera.speicherFreigeben).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    await waitFor(() => expect(kamera.speicherFreigeben).toHaveBeenCalledWith({ bilder: [1, 2], videos: [3], bytes: 3 * 1024 * 1024 }))
    await waitFor(() => expect(toasts()).toContain(i18n.t('mss.vault.kamera.freigegeben', { count: 3 })))
  })

  it('löscht nichts, wenn die Rückfrage abgebrochen wird', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    vi.mocked(kamera.freigebbar).mockResolvedValue({ bilder: [1], videos: [], bytes: 10 })
    zeigen()
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))
    await act(async () => {})
    expect(kamera.speicherFreigeben).not.toHaveBeenCalled()
  })

  it('sagt, wenn noch nichts vollständig gesichert ist, statt zu fragen', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    vi.mocked(kamera.freigebbar).mockResolvedValue({ bilder: [], videos: [], bytes: 0 })
    zeigen()
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    await waitFor(() => expect(toasts()).toContain(i18n.t('mss.vault.kamera.nichtsFreizugeben')))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('sagt, wenn der Server nicht erreichbar ist, statt „nichts gesichert“', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    vi.mocked(kamera.freigebbar).mockResolvedValue({ bilder: [], videos: [], bytes: 0, unerreichbar: true })
    zeigen()
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    await waitFor(() => expect(toasts()).toContain(i18n.t('mss.vault.kamera.freigebenUnerreichbar')))
    expect(toasts()).not.toContain(i18n.t('mss.vault.kamera.nichtsFreizugeben'))
  })


  it('bietet unter Android 10 kein Freigeben an, sagt aber warum', async () => {
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: false })
    kamera.useKameraSicherung.setState({ stand: STAND })
    zeigen()
    expect(await screen.findByText(i18n.t('mss.vault.kamera.freigebenAlt'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') })).toBeNull()
  })
})
