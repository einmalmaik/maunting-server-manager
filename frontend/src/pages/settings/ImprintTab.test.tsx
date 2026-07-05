import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ImprintTab } from './ImprintTab'
import { api } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('ImprintTab', () => {
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
      imprint_enabled: false,
      imprint_url: 'https://example.com/impressum',
    })
  })

  it('saves the switch and URL without dropping the stored URL when disabled', async () => {
    render(<ImprintTab />)

    const url = await screen.findByLabelText('Impressum-URL')
    expect(url).toHaveValue('https://example.com/impressum')

    fireEvent.click(screen.getByRole('switch', { name: 'Impressum anzeigen' }))
    fireEvent.change(url, { target: { value: 'https://maunting.example/legal' } })
    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      expect(api).toHaveBeenLastCalledWith('/settings', {
        method: 'POST',
        body: JSON.stringify({
          imprint_enabled: true,
          imprint_url: 'https://maunting.example/legal',
        }),
      })
    })
  })
})
