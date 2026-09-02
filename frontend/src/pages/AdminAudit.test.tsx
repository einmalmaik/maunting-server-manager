import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminAudit } from './AdminAudit'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'

vi.mock('@/api/client', () => ({ api: vi.fn() }))

describe('AdminAudit', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
    vi.mocked(client.api).mockReset()
  })

  it('loads audit rows from the real API path and does not show secrets', async () => {
    vi.mocked(client.api).mockResolvedValue([
      {
        id: 9,
        user_id: 1,
        action: 'postgres.admin.rotate',
        target_type: 'managed_postgres',
        target_id: null,
        origin: 'system',
        correlation_id: '0a613465-487d-44a0-af1c-5aa031a873c9',
        details: '{"nodes_updated":[1]}',
        created_at: '2026-07-30T10:00:00.000Z',
      },
    ])

    render(
      <MemoryRouter>
        <AdminAudit />
      </MemoryRouter>,
    )

    expect(await screen.findByText('postgres.admin.rotate')).toBeInTheDocument()
    expect(client.api).toHaveBeenCalledWith(expect.stringMatching(/^\/admin\/audit-logs\?/))
    expect(screen.getByText('System')).toBeInTheDocument()
    expect(screen.getByText('0a613465')).toBeInTheDocument()
    expect(screen.queryByText(/password\s*=/i)).not.toBeInTheDocument()
  })

  it('shows an explicit error when the API fails', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('Keine Berechtigung'))

    render(
      <MemoryRouter>
        <AdminAudit />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Keine Berechtigung/i)).toBeInTheDocument()
    })
  })
})
