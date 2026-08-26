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
    vi.mocked(client.api).mockResolvedValueOnce([
      {
        id: 1,
        event_id: 'evt-test-1',
        title: 'Team Meeting Test',
        start: new Date().toISOString(),
        end: new Date(Date.now() + 3600000).toISOString(),
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
})
