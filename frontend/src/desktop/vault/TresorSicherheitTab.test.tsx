/**
 * Die Leck-Prüfung schickt einen Teil des Passwort-Prüfwerts an Have I Been
 * Pwned. Bis 02.10.2026 ließ sie sich nirgends abschalten, und die
 * Datenschutzerklärung nannte GitHub als einzige Ausnahme.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { TresorSicherheitTab } from './TresorSicherheitTab'
import { checkPasswordLeak, VAULT_LEAK_CHECK_ENABLED_KEY } from './leakChecker'
import { useVaultStore } from './vaultStore'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
}))

describe('Tresor-Sicherheit: Leck-Prüfung', () => {
  beforeEach(() => {
    localStorage.clear()
    useVaultStore.setState({ checkBiometricsSupport: vi.fn(async () => undefined) })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('ist an und lässt sich abschalten; danach geht nichts mehr hinaus', async () => {
    const anfragen = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 200 }))
    render(
      <MemoryRouter>
        <TresorSicherheitTab />
      </MemoryRouter>,
    )
    const schalter = screen.getByRole('switch', { name: i18n.t('mss.vault.leckPruefung') })
    expect(schalter).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(schalter)
    expect(schalter).toHaveAttribute('aria-checked', 'false')
    expect(localStorage.getItem(VAULT_LEAK_CHECK_ENABLED_KEY)).toBe('false')
    expect(await checkPasswordLeak('ein-langes-passwort')).toEqual({ isLeaked: false, count: 0, checked: false })
    expect(anfragen).not.toHaveBeenCalled()

    fireEvent.click(schalter)
    expect(localStorage.getItem(VAULT_LEAK_CHECK_ENABLED_KEY)).toBe('true')
  })

  it('nennt jeden Schalter beim Namen, auch für Screenreader', () => {
    render(
      <MemoryRouter>
        <TresorSicherheitTab />
      </MemoryRouter>,
    )
    // Bis 03.10.2026 standen Fensterwechsel und Fingerabdruck ohne Namen da.
    for (const schalter of screen.getAllByRole('switch')) expect(schalter).toHaveAccessibleName()
    expect(screen.getByRole('switch', { name: i18n.t('mss.vault.beiFensterwechsel') })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: i18n.t('mss.vault.biometrieSchalter') })).toBeInTheDocument()
  })

  it('öffnet den Biometrie-Aktivierungsdialog mit Dialog und PasswordInput, unterstützt Bestätigung und Abbruch', async () => {
    const enableBiometrics = vi.fn().mockResolvedValue(true)
    useVaultStore.setState({
      isInitialized: true,
      isUnlocked: true,
      isBiometricsSupported: true,
      isBiometricsEnabled: false,
      enableBiometrics,
    })

    render(
      <MemoryRouter>
        <TresorSicherheitTab />
      </MemoryRouter>,
    )

    const bioSchalter = screen.getByRole('switch', { name: i18n.t('mss.vault.biometrieSchalter') })
    await act(async () => {
      fireEvent.click(bioSchalter)
    })

    // Dialog ist offen
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText(i18n.t('mss.vault.biometrieEinrichten'))).toBeInTheDocument()

    // PasswordInput existiert (hat das Auge-Symbol und placeholder)
    const pwInput = screen.getByPlaceholderText(i18n.t('mss.vault.masterPasswort'))
    expect(pwInput).toBeInTheDocument()
    expect(pwInput).toHaveAttribute('type', 'password')

    // Abbrechen schließt den Dialog und leert das Passwort
    await act(async () => {
      fireEvent.change(pwInput, { target: { value: 'geheimes-passwort' } })
    })
    const abbrechenKnopf = screen.getByRole('button', { name: i18n.t('common.cancel') })
    await act(async () => {
      fireEvent.click(abbrechenKnopf)
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(enableBiometrics).not.toHaveBeenCalled()

    // Erneut öffnen: Passwortfeld ist leer
    await act(async () => {
      fireEvent.click(bioSchalter)
    })
    const pwInputNeu = await screen.findByPlaceholderText(i18n.t('mss.vault.masterPasswort'))
    expect(pwInputNeu).toHaveValue('')

    // Absenden ruft enableBiometrics auf
    await act(async () => {
      fireEvent.change(pwInputNeu, { target: { value: 'richtiges-passwort' } })
    })
    const aktivierenKnopf = screen.getByRole('button', { name: i18n.t('mss.vault.biometrieAktivieren') })
    await act(async () => {
      fireEvent.click(aktivierenKnopf)
    })

    expect(enableBiometrics).toHaveBeenCalledWith('richtiges-passwort')
  })

  it('Schließen über Schließen-Symbol (X) schließt den Dialog und leert den Entwurf', async () => {
    useVaultStore.setState({
      isInitialized: true,
      isUnlocked: true,
      isBiometricsSupported: true,
      isBiometricsEnabled: false,
    })

    render(
      <MemoryRouter>
        <TresorSicherheitTab />
      </MemoryRouter>,
    )

    const bioSchalter = screen.getByRole('switch', { name: i18n.t('mss.vault.biometrieSchalter') })
    await act(async () => {
      fireEvent.click(bioSchalter)
    })

    const pwInput = await screen.findByPlaceholderText(i18n.t('mss.vault.masterPasswort'))
    await act(async () => {
      fireEvent.change(pwInput, { target: { value: 'nicht-abgesendet' } })
    })

    const xKnopf = screen.getByRole('button', { name: i18n.t('common.close') })
    await act(async () => {
      fireEvent.click(xKnopf)
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Erneut öffnen: Passwort ist leer
    await act(async () => {
      fireEvent.click(bioSchalter)
    })
    const pwInputNeu = await screen.findByPlaceholderText(i18n.t('mss.vault.masterPasswort'))
    expect(pwInputNeu).toHaveValue('')
  })
})

