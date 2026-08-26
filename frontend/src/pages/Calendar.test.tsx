import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { Calendar } from './Calendar'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('Calendar Page Component', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    await i18n.changeLanguage('de')
  })

  it('renders calendar header, month view and action buttons', async () => {
    const now = new Date()
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12, 0, 0).toISOString()
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 13, 0, 0).toISOString()
    vi.mocked(client.api).mockResolvedValue([
      {
        id: 1,
        event_id: 'evt-test-1',
        title: 'Team Meeting Test',
        start,
        end,
        description: 'Test agenda',
        location: 'Office',
        color: 'primary',
      },
    ])

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    // Check title and action buttons
    expect(screen.getByText('Kalender & Termine')).toBeInTheDocument()
    expect(screen.getByText('Neuer Termin')).toBeInTheDocument()
    expect(screen.getByText('Abonnieren')).toBeInTheDocument()
    expect(screen.getByText('Aktualisieren')).toBeInTheDocument()

    // Wait for event chip to appear
    await waitFor(() => {
      expect(screen.getByText('Team Meeting Test')).toBeInTheDocument()
    })
  })

  it('switches between Month, Week, and Day views', async () => {
    vi.mocked(client.api).mockResolvedValue([])

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    // Switch to Week view
    const weekBtn = screen.getByRole('button', { name: 'Woche' })
    fireEvent.click(weekBtn)

    // Switch to Day view
    const dayBtn = screen.getByRole('button', { name: 'Tag' })
    fireEvent.click(dayBtn)

    expect(screen.getByText(/Termine für diesen Tag eingetragen/i)).toBeInTheDocument()
  })

  it('opens create event modal when clicking Neuer Termin', async () => {
    vi.mocked(client.api).mockResolvedValue([])

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    const createBtn = screen.getByRole('button', { name: /Neuer Termin/i })
    fireEvent.click(createBtn)

    expect(screen.getByPlaceholderText('z. B. Team-Meeting, Wartung Server 1')).toBeInTheDocument()
    expect(screen.getByText('Speichern')).toBeInTheDocument()
  })

  it('triggers test-reminder API when clicking Push testen button', async () => {
    vi.mocked(client.api).mockResolvedValueOnce([]) // fetchEvents
    vi.mocked(client.api).mockResolvedValueOnce({
      status: 'success',
      email_sent: true,
      device_notifications_enabled: true,
      title: 'Test-Termin: Server-Wartung & Backup-Check',
      start: '27.08.2026 um 14:00 Uhr',
      time_hint: 'in 1 Tag',
    })

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    const testBtn = screen.getByRole('button', { name: /Push testen/i })
    fireEvent.click(testBtn)

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/calendar/test-reminder', { method: 'POST' })
    })
  })
})

