import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { GeneralTab } from './GeneralTab'
import { api } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { EMPTY_PANEL_SETTINGS } from './types'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('GeneralTab', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    usePermissionsStore.setState({
      isLoading: false,
      me: {
        is_owner: true,
        global_keys: ['panel.settings.write'],
        server_keys: {},
      } as any,
    })
    vi.mocked(api).mockReset()
    vi.mocked(api).mockResolvedValue({
      ...EMPTY_PANEL_SETTINGS,
      panel_url: 'https://panel.example',
      updates_automatic: false,
    })
  })

  it('speichert die automatischen Updates, nachdem der Schalter umgelegt wurde', async () => {
    render(<GeneralTab />)

    const schalter = await screen.findByRole('switch', { name: 'Automatische Updates' })
    expect(schalter).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(schalter)
    expect(schalter).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const aufrufe = vi.mocked(api).mock.calls
      const letzterAufruf = aufrufe[aufrufe.length - 1]
      expect(letzterAufruf[0]).toBe('/settings')
      expect(letzterAufruf[1]?.method).toBe('POST')
      expect(JSON.parse(String(letzterAufruf[1]?.body)).updates_automatic).toBe(true)
    })
  })

  /**
   * Der Schalter fuer den Social-Hub war da, schaltete, meldete "Gespeichert" -- und sein Wert
   * stand nie im POST-Body. Auffallen konnte das nicht: Der einzige Test prueffte genau einen
   * Schluessel. Diese Liste waechst mit jedem neuen Schalter mit, und ein vergessener Eintrag
   * faellt sofort auf, statt erst dem Nutzer.
   */
  it('schickt jeden Schalter dieses Tabs auch wirklich mit', async () => {
    const erwarteteSchluessel = [
      'updates_automatic',
      'desktop_app_download_enabled',
      'story_fable_download_enabled',
      'calendar_enabled',
      'notes_enabled',
      'vault_enabled',
      'social_enabled',
      'cloudflare_enabled',
    ]

    render(<GeneralTab />)
    await screen.findByRole('switch', { name: 'Automatische Updates' })

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const aufrufe = vi.mocked(api).mock.calls
      const letzterAufruf = aufrufe[aufrufe.length - 1]
      expect(letzterAufruf[0]).toBe('/settings')
      const body = JSON.parse(String(letzterAufruf[1]?.body))
      for (const schluessel of erwarteteSchluessel) {
        expect(body, `${schluessel} fehlt im POST-Body`).toHaveProperty(schluessel)
      }
    })
  })

  it('speichert den umgelegten Social-Schalter', async () => {
    render(<GeneralTab />)

    const schalter = await screen.findByRole('switch', { name: 'Social-, Freundes- & E2EE-Chat-Hub' })
    const vorher = schalter.getAttribute('aria-checked')

    fireEvent.click(schalter)
    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const aufrufe = vi.mocked(api).mock.calls
      const body = JSON.parse(String(aufrufe[aufrufe.length - 1][1]?.body))
      expect(body.social_enabled).toBe(vorher !== 'true')
    })
  })

  it('sperrt den Schalter ohne das Recht panel.settings.write', async () => {
    usePermissionsStore.setState({
      isLoading: false,
      me: { is_owner: false, global_keys: [], server_keys: {} } as any,
    })

    render(<GeneralTab />)

    const schalter = await screen.findByRole('switch', { name: 'Automatische Updates' })
    expect(schalter).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Speichern/i })).toBeNull()
  })
})
