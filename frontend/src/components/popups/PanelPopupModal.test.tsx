import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PanelPopupModal } from './PanelPopupModal'
import * as popupsApi from '@/api/popups'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/popups', () => ({
  getActivePopup: vi.fn(),
  dismissPopup: vi.fn(),
}))

describe('PanelPopupModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ isAuthenticated: true })
  })

  it('fetches and renders active popup', async () => {
    vi.mocked(popupsApi.getActivePopup).mockResolvedValue({
      id: 1,
      title: 'Wartungsarbeiten angekündigt',
      content_markdown: 'Am Samstag finden **Wartungsarbeiten** statt.',
      is_active: true,
      start_at: null,
      end_at: null,
      button_text: 'Statusseite',
      button_url: 'https://status.example.com',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    })

    render(<PanelPopupModal />)

    expect(await screen.findByText('Wartungsarbeiten angekündigt')).toBeInTheDocument()
    expect(screen.getByText('Wartungsarbeiten')).toBeInTheDocument()
    expect(screen.getByText('Statusseite')).toBeInTheDocument()
  })

  it('handles snooze dismissal', async () => {
    vi.mocked(popupsApi.dismissPopup).mockResolvedValue({ ok: true, mode: 'snooze' })

    render(
      <PanelPopupModal
        popup={{
          id: 42,
          title: 'Wichtiger Hinweis',
          content_markdown: 'Bitte beachten.',
          is_active: true,
          start_at: null,
          end_at: null,
          button_text: null,
          button_url: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }}
      />
    )

    const understandBtn = screen.getByRole('button', { name: /Verstanden|Understood/i })
    fireEvent.click(understandBtn)

    await waitFor(() => {
      expect(popupsApi.dismissPopup).toHaveBeenCalledWith(42, 'snooze')
    })
  })

  it('handles permanent dismissal', async () => {
    vi.mocked(popupsApi.dismissPopup).mockResolvedValue({ ok: true, mode: 'permanent' })

    render(
      <PanelPopupModal
        popup={{
          id: 42,
          title: 'Wichtiger Hinweis',
          content_markdown: 'Bitte beachten.',
          is_active: true,
          start_at: null,
          end_at: null,
          button_text: null,
          button_url: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }}
      />
    )

    const permanentBtn = screen.getByRole('button', { name: /Nicht mehr anzeigen|Don't show again/i })
    fireEvent.click(permanentBtn)

    await waitFor(() => {
      expect(popupsApi.dismissPopup).toHaveBeenCalledWith(42, 'permanent')
    })
  })
})
