import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { CloudflareTab } from './CloudflareTab'
import { api } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { EMPTY_PANEL_SETTINGS } from './types'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('CloudflareTab', () => {
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
    vi.mocked(api).mockImplementation(async (url: string) => {
      if (url === '/settings') {
        return {
          ...EMPTY_PANEL_SETTINGS,
          cloudflare_enabled: true,
          cloudflare_api_configured: true,
          cloudflare_api_source: 'panel',
          cloudflare_api_token: '••••••••1234',
          cloudflare_default_zone: 'zone-1',
        }
      }
      if (url === '/settings/cloudflare-zones') {
        return {
          zones: [
            { id: 'zone-1', name: 'example.com' },
            { id: 'zone-2', name: 'myserver.net' },
          ],
        }
      }
      if (url === '/settings/cloudflare-token/test') {
        return { valid: true, message: 'Cloudflare API-Token ist gültig' }
      }
      return {}
    })
  })

  it('rendert den CloudflareTab mit konfiguriertem Status', async () => {
    render(<CloudflareTab />)

    expect(await screen.findByText('Cloudflare DNS')).toBeInTheDocument()
    expect(await screen.findByText(/Konfiguriert/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Token konfiguriert/i)).toBeInTheDocument()
  })

  it('führt einen Verbindungstest durch', async () => {
    render(<CloudflareTab />)

    const testBtn = await screen.findByRole('button', { name: /Verbindung testen/i })
    fireEvent.click(testBtn)

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/settings/cloudflare-token/test', { method: 'POST' })
    })
  })

  it('markiert den Cloudflare-Token zum Entfernen und löscht beim Speichern', async () => {
    render(<CloudflareTab />)

    const deleteBtn = await screen.findByRole('button', { name: /Token entfernen/i })
    fireEvent.click(deleteBtn)

    expect(await screen.findByText(/Wird beim Speichern entfernt/i)).toBeInTheDocument()

    const saveBtn = screen.getByRole('button', { name: /Einstellungen speichern/i })
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/settings/cloudflare-token', { method: 'DELETE' })
    })
  })
})

