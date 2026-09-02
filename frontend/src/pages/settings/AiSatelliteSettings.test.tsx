import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiSatelliteSettings } from './AiSatelliteSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)

describe('AiSatelliteSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  it('lädt und zeigt den Status unkonfiguriert an', async () => {
    api.mockImplementation(((path: string) => {
      if (path === '/ai/settings/satellite') {
        return Promise.resolve({ configured: false })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiSatelliteSettings canWrite />)

    expect(await screen.findByText(i18n.t('ai.satellite.title'))).toBeInTheDocument()
    expect(screen.getByPlaceholderText(i18n.t('ai.satellite.clientIdPlaceholder'))).toBeInTheDocument()
    expect(screen.getByPlaceholderText(i18n.t('ai.satellite.clientSecretPlaceholder'))).toBeInTheDocument()
  })

  it('speichert Zugangsdaten über das Formular', async () => {
    api.mockImplementation(((path: string, init?: RequestInit) => {
      if (path === '/ai/settings/satellite' && init?.method === 'PUT') {
        return Promise.resolve({ configured: true })
      }
      if (path === '/ai/settings/satellite') {
        return Promise.resolve({ configured: false })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiSatelliteSettings canWrite />)

    const clientInput = await screen.findByPlaceholderText(i18n.t('ai.satellite.clientIdPlaceholder'))
    const secretInput = screen.getByPlaceholderText(i18n.t('ai.satellite.clientSecretPlaceholder'))
    const saveBtn = screen.getByRole('button', { name: i18n.t('settings.save') })

    fireEvent.change(clientInput, { target: { value: 'client_xyz' } })
    fireEvent.change(secretInput, { target: { value: 'secret_123' } })
    fireEvent.click(saveBtn)

    expect(api).toHaveBeenCalledWith(
      '/ai/settings/satellite',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ client_id: 'client_xyz', client_secret: 'secret_123' }),
      }),
    )
  })
})
