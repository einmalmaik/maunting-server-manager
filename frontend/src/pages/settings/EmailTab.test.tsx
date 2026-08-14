import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { EmailTab } from './EmailTab'
import { api } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { EMPTY_PANEL_SETTINGS } from './types'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('EmailTab', () => {
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
      email_provider: 'smtp',
      smtp_host: 'smtp.example.com',
      smtp_tls: 'true',
    })
  })

  it('speichert smtp_tls als false, nachdem der Schalter ausgeschaltet wurde', async () => {
    render(<EmailTab />)

    const schalter = await screen.findByRole('switch', { name: 'TLS verwenden' })
    expect(schalter).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(schalter)
    expect(schalter).toHaveAttribute('aria-checked', 'false')

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const aufrufe = vi.mocked(api).mock.calls
      const letzterAufruf = aufrufe[aufrufe.length - 1]
      expect(letzterAufruf[0]).toBe('/settings')
      expect(letzterAufruf[1]?.method).toBe('POST')
      expect(JSON.parse(String(letzterAufruf[1]?.body)).smtp_tls).toBe('false')
    })
  })
})
