import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { AiTab, type AiRoleLimits } from './AiTab'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
vi.mock('./AiProvidersSettings', () => ({ AiProvidersSettings: () => null }))

const permissions = vi.fn((_key: string): boolean => true)
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => permissions(key),
}))

const row: AiRoleLimits = {
  role_id: 9,
  role_name: 'ai-vip',
  configured: true,
  daily_token_limit: 10_000,
  weekly_token_limit: 50_000,
  monthly_token_limit: 200_000,
  requests_per_minute: 20,
  concurrent_operations: 2,
  monthly_cost_limit_cents: 5_000,
  updated_at: '2026-08-01T12:00:00Z',
}

describe('AiTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    permissions.mockReturnValue(true)
    useToastStore.setState({ toasts: [] })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockResolvedValue([row])
  })

  it('loads role limits and saves a complete set including unlimited', async () => {
    render(<AiTab />)
    await screen.findByRole('heading', { name: 'ai-vip' })

    fireEvent.click(screen.getByRole('checkbox', {
      name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i,
    }))
    vi.mocked(client.api).mockResolvedValue({ ...row, monthly_token_limit: null })
    fireEvent.click(screen.getByRole('button', { name: /Speichern: ai-vip/i }))

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/ai/settings/role-limits/9', {
        method: 'PUT',
        body: JSON.stringify({
          daily_token_limit: 10_000,
          weekly_token_limit: 50_000,
          monthly_token_limit: null,
          requests_per_minute: 20,
          concurrent_operations: 2,
          monthly_cost_limit_cents: 5_000,
        }),
      })
    })
  })

  it('shows API failures and does not silently discard them', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('AI-Limits nicht erreichbar'))
    render(<AiTab />)

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((toast) => toast.message === 'AI-Limits nicht erreichbar')).toBe(true)
    })
  })

  it('does not request settings without read permission', () => {
    permissions.mockReturnValue(false)
    render(<AiTab />)

    expect(client.api).not.toHaveBeenCalled()
    expect(screen.getByText(/keine Berechtigung/i)).toBeInTheDocument()
  })
})
