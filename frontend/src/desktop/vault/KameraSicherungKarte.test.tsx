/**
 * Die Karte der Kamera-Sicherung: einschalten nur bei offenem Tresor und mit
 * vollem Zugriff, „Speicher freigeben“ nur nach Rückfrage.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { formatBytes } from '@/lib/format'
import { useConfirmStore } from '@/stores/confirmStore'
import { useToastStore } from '@/stores/toastStore'
import * as tauri from '@/desktop/tauri'
import * as kamera from './kameraSicherung'
import { KameraSicherungKarte } from './KameraSicherungKarte'
import { useVaultStore } from './vaultStore'

vi.mock('@/desktop/tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  medienZugriff: vi.fn(),
}))

vi.mock('./kameraSicherung', async () => {
  const { create } = await import('zustand')
  return {
    useKameraSicherung: create(() => ({ stand: null, laeuft: false, warten: null })),
    kameraStandLaden: vi.fn(async () => null),
    kameraEinschalten: vi.fn(),
    kameraAusschalten: vi.fn(),
    kameraNurWlan: vi.fn(),
    kameraAnstossen: vi.fn(),
    kameraVorhandeneSichern: vi.fn(),
    freigebbar: vi.fn(),
    speicherFreigeben: vi.fn(async () => true),
  }
})

const BUCKET = 'k'.repeat(64)
const STAND = { bucket: BUCKET, an: true, nurWlan: false, geraet: 'g', bis: 3, fassung: 'gen:1', gesichert: 0 }

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

beforeEach(() => {
  vi.clearAllMocks()
  useToastStore.setState({ toasts: [] })
  useConfirmStore.setState({ pending: null })
  vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
  kamera.useKameraSicherung.setState({ stand: null, laeuft: false, warten: null })
  useVaultStore.setState({ isUnlocked: true, bucketId: BUCKET })
})

describe('KameraSicherungKarte', () => {
  it('lässt sich bei gesperrtem Tresor nicht einschalten und sagt, warum', () => {
    useVaultStore.setState({ isUnlocked: false })
    zeigen()
    expect(schalter()).toBeDisabled()
    expect(screen.getByText(i18n.t('mss.vault.kamera.gesperrt'))).toBeInTheDocument()
  })

  it('sagt, wenn nur ausgewählte Fotos freigegeben sind, und bleibt aus', async () => {
    vi.mocked(kamera.kameraEinschalten).mockResolvedValue({ stand: 'teilweise', papierkorb: true })
    zeigen()
    fireEvent.click(schalter())
    await waitFor(() => expect(toasts()).toContain(i18n.t('mss.vault.kamera.zugriff.teilweise')))
    expect(schalter()).not.toBeChecked()
  })

  it('zeigt eingeschaltet WLAN-Schalter, Stand und den fehlenden Zugriff mit Abhilfe', async () => {
    kamera.useKameraSicherung.setState({ stand: { ...STAND, gesichert: 2, zuletzt: Date.UTC(2026, 9, 3, 12) }, warten: 'zugriff' })
    zeigen()
    expect(schalter()).toBeChecked()
    expect(screen.getByRole('switch', { name: i18n.t('mss.vault.kamera.nurWlan') })).toBeInTheDocument()
    expect(screen.getByText(/^2 Aufnahmen gesichert/)).toBeInTheDocument()
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.kamera.zugriffErteilen') }))
    await waitFor(() => expect(kamera.kameraAnstossen).toHaveBeenCalledWith(BUCKET))
    expect(tauri.medienZugriff).toHaveBeenLastCalledWith(true)
  })

  it('sagt, solange bei gesperrtem Tresor nicht gesichert wird, und schweigt danach', () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    const { unmount } = zeigen()
    expect(screen.getByText(i18n.t('mss.vault.kamera.eingangFehlt'))).toBeInTheDocument()
    unmount()

    kamera.useKameraSicherung.setState({ stand: { ...STAND, eingang: { id: 'e', pqPublicKey: 'p', rsaPublicKey: 'r' } } })
    zeigen()
    expect(screen.queryByText(i18n.t('mss.vault.kamera.eingangFehlt'))).not.toBeInTheDocument()
  })

  it('gibt Speicher erst nach der Rückfrage frei, mit Zahl und Größe', async () => {
    kamera.useKameraSicherung.setState({ stand: STAND })
    vi.mocked(kamera.freigebbar).mockResolvedValue({ bilder: [1, 2], videos: [3], bytes: 3 * 1024 * 1024 })
    zeigen()
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') }))
    const dialog = await screen.findByRole('dialog')
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

  it('bietet unter Android 10 kein Freigeben an, sagt aber warum', async () => {
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: false })
    kamera.useKameraSicherung.setState({ stand: STAND })
    zeigen()
    expect(await screen.findByText(i18n.t('mss.vault.kamera.freigebenAlt'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('mss.vault.kamera.freigebenKnopf') })).toBeNull()
  })
})
