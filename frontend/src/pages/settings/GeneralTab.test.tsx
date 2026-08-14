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
