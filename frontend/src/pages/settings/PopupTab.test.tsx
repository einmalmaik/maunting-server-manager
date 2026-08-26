import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PopupTab } from './PopupTab'
import * as popupsApi from '@/api/popups'
import { useToastStore } from '@/stores/toastStore'

vi.mock('@/api/popups', () => ({
  listAdminPopups: vi.fn(),
  createAdminPopup: vi.fn(),
  updateAdminPopup: vi.fn(),
  deleteAdminPopup: vi.fn(),
  getActivePopup: vi.fn(),
  dismissPopup: vi.fn(),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))

describe('PopupTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useToastStore.setState({ toasts: [] })
  })

  it('renders list of popups and allows starting creation', async () => {
    vi.mocked(popupsApi.listAdminPopups).mockResolvedValue([
      {
        id: 10,
        title: 'Geplante Wartung',
        content_markdown: 'Server-Neustart um 04:00 Uhr.',
        is_active: true,
        start_at: null,
        end_at: null,
        button_text: null,
        button_url: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ])

    render(<PopupTab />)

    expect(await screen.findByText('Geplante Wartung')).toBeInTheDocument()
    expect(screen.getByText('Server-Neustart um 04:00 Uhr.')).toBeInTheDocument()

    const newBtn = screen.getByRole('button', { name: /Neues Pop-up|Create New/i })
    fireEvent.click(newBtn)

    expect(screen.getByLabelText(/Titel|Title/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Verfasse den Text/i)).toBeInTheDocument()
  })

  it('submits a new popup creation', async () => {
    vi.mocked(popupsApi.listAdminPopups).mockResolvedValue([])
    vi.mocked(popupsApi.createAdminPopup).mockResolvedValue({
      id: 11,
      title: 'Neues Feature',
      content_markdown: 'Markdown Text.',
      is_active: true,
      start_at: null,
      end_at: null,
      button_text: null,
      button_url: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    })

    render(<PopupTab />)

    const newBtn = await screen.findByRole('button', { name: /Neues Pop-up|Create New/i })
    fireEvent.click(newBtn)

    fireEvent.change(screen.getByPlaceholderText(/z. B. Geplante Wartungsarbeiten/i), {
      target: { value: 'Neues Feature' },
    })
    fireEvent.change(screen.getByPlaceholderText(/Verfasse den Text/i), {
      target: { value: 'Markdown Text.' },
    })

    const saveBtn = screen.getByRole('button', { name: /Speichern|Save/i })
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(popupsApi.createAdminPopup).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Neues Feature',
          content_markdown: 'Markdown Text.',
          is_active: true,
        })
      )
    })
  })
})
