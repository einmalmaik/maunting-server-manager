import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiTomTomSettings } from './AiTomTomSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)

describe('AiTomTomSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  it('zeigt den serverseitigen Konfigurationsstatus', async () => {
    api.mockResolvedValue({ configured: true })

    render(<AiTomTomSettings canWrite />)

    expect(await screen.findByText(i18n.t('ai.tomtom.title'))).toBeInTheDocument()
    expect(screen.getByPlaceholderText(i18n.t('ai.tomtom.configured'))).toHaveValue('')
  })

  it('sendet den Schlüssel nur beim Speichern und leert das Eingabefeld', async () => {
    api.mockImplementation(((path: string, init?: RequestInit) => {
      if (path === '/ai/settings/tomtom' && init?.method === 'PUT') {
        return Promise.resolve({ configured: true })
      }
      return Promise.resolve({ configured: false })
    }) as unknown as typeof client.api)

    render(<AiTomTomSettings canWrite />)

    const input = await screen.findByPlaceholderText(i18n.t('ai.tomtom.placeholder'))
    fireEvent.change(input, { target: { value: 'tomtom-test-key' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.save') }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/ai/settings/tomtom',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ api_key: 'tomtom-test-key' }),
      }),
    ))
    await waitFor(() => expect(input).toHaveValue(''))
  })
})
