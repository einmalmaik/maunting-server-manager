/**
 * „Master-Passwort vergessen?": bis 09/2026 ging das nur über die Datenbank.
 *
 * Wie beim Löschen des Kontos: das Konto-Passwort, wenn eins hinterlegt ist,
 * bei 2FA zusätzlich ein Faktor, und das Wort „delete", das sich nicht
 * einfügen lässt. Ein Social-Konto ohne 2FA braucht nur das Wort.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { TresorZuruecksetzen } from './TresorZuruecksetzen'
import { useVaultStore } from './vaultStore'

const speicher = vi.hoisted(() => ({ wert: { belegt: 0, quote: 1, in_loeschung: 0, blobs: 0 } }))
vi.mock('./tresorBlobApi', () => ({ speicherAbfragen: vi.fn(async () => speicher.wert), bucketMelderSetzen: vi.fn(), zurueckgesetztFrage: vi.fn() }))

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
}))

describe('TresorZuruecksetzen', () => {
  const resetVault = vi.fn(async () => undefined)

  beforeEach(() => {
    resetVault.mockClear()
    speicher.wert = { belegt: 0, quote: 1, in_loeschung: 0, blobs: 0 }
    useVaultStore.setState({ resetVault })
    useAuthStore.setState({
      user: { id: 1, username: 'ich', has_password: true, two_factor_enabled: false } as never,
    })
  })

  const knopf = () => screen.getByRole('button', { name: i18n.t('mss.vault.zuruecksetzen.knopf') })

  it('gibt den Knopf erst frei, wenn „delete" getippt ist, und schickt das Konto-Passwort mit', async () => {
    const fertig = vi.fn()
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={fertig} />)

    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.passwortLabel')), {
      target: { value: 'konto-pw' },
    })
    expect(knopf()).toBeDisabled()

    const wort = screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.wortLabel'))
    fireEvent.change(wort, { target: { value: 'delete' } })
    expect(knopf()).toBeEnabled()

    fireEvent.click(knopf())
    await waitFor(() => expect(fertig).toHaveBeenCalled())
    expect(resetVault).toHaveBeenCalledWith({ password: 'konto-pw' }, 'delete')
  })

  it('lässt das Bestätigungswort nicht einfügen', () => {
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={() => {}} />)
    const wort = screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.wortLabel'))
    const ereignis = new Event('paste', { bubbles: true, cancelable: true })
    wort.dispatchEvent(ereignis)
    expect(ereignis.defaultPrevented).toBe(true)
  })

  it('zeigt die Ablehnung des Servers und bleibt im Formular', async () => {
    resetVault.mockRejectedValueOnce(new Error('Bitte dein Passwort bestätigen.'))
    const fertig = vi.fn()
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={fertig} />)
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.passwortLabel')), {
      target: { value: 'falsch' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.wortLabel')), {
      target: { value: 'delete' },
    })
    fireEvent.click(knopf())
    expect(await screen.findByRole('alert')).toHaveTextContent('Bitte dein Passwort bestätigen.')
    expect(fertig).not.toHaveBeenCalled()
  })

  it('verlangt bei 2FA Passwort und Code zugleich und schickt beides', async () => {
    useAuthStore.setState({
      user: { id: 1, username: 'ich', has_password: true, two_factor_enabled: true, two_factor_methods: ['totp'] } as never,
    })
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={() => {}} />)
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.passwortLabel')), {
      target: { value: 'konto-pw' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.wortLabel')), {
      target: { value: 'delete' },
    })
    // Ohne vollständigen Code bleibt der Knopf gesperrt.
    expect(knopf()).toBeDisabled()
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.otpLabel')), {
      target: { value: '123456' },
    })
    fireEvent.click(knopf())
    await waitFor(() => expect(resetVault).toHaveBeenCalledWith({ password: 'konto-pw', otp_code: '123456' }, 'delete'))
  })

  it('fragt ein Social-Konto ohne 2FA nur nach dem Wort', async () => {
    useAuthStore.setState({
      user: { id: 1, username: 'ich', has_password: false, two_factor_enabled: false } as never,
    })
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={() => {}} />)
    expect(screen.queryByLabelText(i18n.t('mss.vault.zuruecksetzen.passwortLabel'))).toBeNull()
    fireEvent.change(screen.getByLabelText(i18n.t('mss.vault.zuruecksetzen.wortLabel')), {
      target: { value: 'delete' },
    })
    fireEvent.click(knopf())
    await waitFor(() => expect(resetVault).toHaveBeenCalledWith({ password: null }, 'delete'))
  })

  it('nennt vorher, wie viele Dateien mitgehen und wie groß sie sind', async () => {
    speicher.wert = { belegt: 3 * 1024 * 1024 + 500, quote: 10, in_loeschung: 500, blobs: 7 }
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={() => {}} />)
    expect(
      await screen.findByText(i18n.t('mss.vault.zuruecksetzen.dateien', { groesse: '3.0 MB' })),
    ).toBeInTheDocument()
  })

  it('schweigt über Dateien, wenn keine da sind', async () => {
    render(<TresorZuruecksetzen onAbbrechen={() => {}} onFertig={() => {}} />)
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText(/7 Tagen/)).toBeNull()
  })
})
