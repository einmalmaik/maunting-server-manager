import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { hosterApi, type HosterIntegration } from '@/api/hoster'
import i18n from '@/i18n'
import { HosterTab } from './HosterTab'

vi.mock('@/api/hoster', () => ({
  hosterApi: {
    listIntegrations: vi.fn(),
    createIntegration: vi.fn(),
    updateIntegration: vi.fn(),
    deleteIntegration: vi.fn(),
    rotateApiKey: vi.fn(),
    rotateWebhookSecret: vi.fn(),
    listProducts: vi.fn(),
    saveProduct: vi.fn(),
    deleteProduct: vi.fn(),
    listServices: vi.fn(),
    listDeliveries: vi.fn(),
    retryDelivery: vi.fn(),
  },
}))

const integration: HosterIntegration = {
  id: 3,
  name: 'Testshop',
  slug: 'testshop',
  enabled: true,
  service_user_id: 9,
  webhook_url: 'https://shop.example/hooks/msm',
  terminate_grace_days: 7,
  api_key_hint: '...ab12',
  webhook_secret_configured: true,
  webhook_secret_hint: '...cd34',
  created_at: '2026-08-07T10:00:00Z',
  updated_at: '2026-08-07T10:00:00Z',
}

describe('HosterTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(hosterApi.listIntegrations).mockReset().mockResolvedValue([integration])
    vi.mocked(hosterApi.listProducts).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.listServices).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.listDeliveries).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.rotateApiKey).mockReset().mockResolvedValue({
      value: 'brandneuer-api-key',
      hint: '...9999',
    })
  })

  it('shows only the hint, never a stored secret', async () => {
    render(<HosterTab canWrite />)

    expect(await screen.findByText('...ab12')).toBeInTheDocument()
    expect(screen.getByText('...cd34')).toBeInTheDocument()
    // Es gibt keinen Lesepfad fuer Klartext-Geheimnisse.
    expect(screen.queryByText(/brandneuer-api-key/)).not.toBeInTheDocument()
  })

  it('reveals a rotated key exactly once and hides it again after acknowledgement', async () => {
    render(<HosterTab canWrite />)
    fireEvent.click(await screen.findByRole('button', { name: /API-Key rotieren/ }))

    await waitFor(() => expect(screen.getByText('brandneuer-api-key')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Verstanden' }))

    await waitFor(() =>
      expect(screen.queryByText('brandneuer-api-key')).not.toBeInTheDocument(),
    )
  })

  it('hides every write action for a read-only operator', async () => {
    render(<HosterTab canWrite={false} />)

    await screen.findByText('...ab12')
    expect(screen.queryByRole('button', { name: /API-Key rotieren/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Integration hinzufuegen/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Löschen/ })).not.toBeInTheDocument()
  })
})
