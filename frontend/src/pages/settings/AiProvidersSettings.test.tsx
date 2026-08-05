import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiProviderAdmin } from '@/api/ai'
import i18n from '@/i18n'
import { AiProvidersSettings } from './AiProvidersSettings'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listProviderSettings: vi.fn(),
    createProvider: vi.fn(),
    updateProvider: vi.fn(),
    deleteProvider: vi.fn(),
  },
}))

const provider: AiProviderAdmin = {
  id: 4,
  name: 'Internal AI',
  base_url: 'https://ai.example.invalid/v1',
  default_model: 'model-a',
  enabled: true,
  requires_api_key: true,
  allow_private_network: false,
  operator_key_configured: true,
  operator_key_hint: '********1234',
  updated_at: '2026-08-01T12:00:00Z',
}

describe('AiProvidersSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.listProviderSettings).mockReset().mockResolvedValue([provider])
    vi.mocked(aiApi.updateProvider).mockReset().mockResolvedValue(provider)
  })

  it('never receives an existing secret and clears a replacement after save', async () => {
    render(<AiProvidersSettings canWrite />)
    const keyInput = await screen.findByLabelText('Operator-API-Key')

    expect(keyInput).toHaveValue('')
    expect(screen.queryByDisplayValue('operator-secret-value')).not.toBeInTheDocument()
    fireEvent.change(keyInput, { target: { value: 'new-secret-value' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      operator_api_key: 'new-secret-value',
    })))
    await waitFor(() => expect(keyInput).toHaveValue(''))
  })
})
