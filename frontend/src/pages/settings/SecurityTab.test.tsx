import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SecurityTab } from './SecurityTab'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'

vi.mock('@/api/client', () => ({ api: vi.fn() }))

const canRotate = vi.fn(() => true)
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (key === 'system.secrets.rotate' ? canRotate() : false),
}))

describe('SecurityTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
    canRotate.mockReturnValue(true)
    vi.mocked(client.api).mockReset()
  })

  it('refuses rotation without permission', () => {
    canRotate.mockReturnValue(false)
    render(<SecurityTab />)
    expect(screen.getByText(/system\.secrets\.rotate/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /rotat/i })).not.toBeInTheDocument()
  })

  it('posts rotate after confirm and never stores a password in the success summary', async () => {
    vi.mocked(client.api).mockResolvedValue({
      ok: true,
      admin_user: 'msm_admin',
      nodes_updated: [1],
      nodes_skipped: [],
    })

    render(<SecurityTab />)
    fireEvent.click(screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))

    // Confirm dialog is async via store — resolve true
    await waitFor(() => {
      expect(useConfirmStore.getState().pending).not.toBeNull()
    })
    useConfirmStore.getState().resolve(true)

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/admin/managed-postgres/rotate-admin', {
        method: 'POST',
      })
    })

    await waitFor(() => {
      expect(screen.getByText(/Nodes aktualisiert: 1/i)).toBeInTheDocument()
    })
    expect(screen.queryByDisplayValue(/./)).not.toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/password\s*[:=]\s*\S+/i)
    expect(document.body.textContent || '').toMatch(/nicht angezeigt|never shown/i)
  })

  it('surfaces API errors without silent failure', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('Node offline'))
    render(<SecurityTab />)
    fireEvent.click(screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))
    await waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
    useConfirmStore.getState().resolve(true)

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => /offline|fehlgeschlagen|failed/i.test(t.message))).toBe(true)
    })
  })
})
