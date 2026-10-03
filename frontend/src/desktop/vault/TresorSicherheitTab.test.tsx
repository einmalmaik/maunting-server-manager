/**
 * Die Leck-Prüfung schickt einen Teil des Passwort-Prüfwerts an Have I Been
 * Pwned. Bis 02.10.2026 ließ sie sich nirgends abschalten, und die
 * Datenschutzerklärung nannte GitHub als einzige Ausnahme.
 */

import { fireEvent, render, screen } from '@testing-library/react'
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
})
