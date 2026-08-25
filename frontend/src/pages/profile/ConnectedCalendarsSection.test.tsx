import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { userIntegrationsApi, type CalendarItem } from '@/api/userIntegrations'
import i18n from '@/i18n'
import { ConnectedCalendarsSection } from './ConnectedCalendarsSection'

vi.mock('@/api/userIntegrations', () => ({
  userIntegrationsApi: {
    getMailboxes: vi.fn(),
    createMailbox: vi.fn(),
    updateMailbox: vi.fn(),
    deleteMailbox: vi.fn(),
    testMailbox: vi.fn(),
    getCalendars: vi.fn(),
    createCalendar: vi.fn(),
    deleteCalendar: vi.fn(),
    testCalendar: vi.fn(),
  },
}))

const mockCalendar: CalendarItem = {
  id: 1,
  name: 'Team-Kalender',
  provider_type: 'caldav',
  is_default: true,
  caldav_url: 'https://caldav.example.com/dav/calendars/team',
  caldav_username: 'teamuser',
  has_credentials: true,
  created_at: '2026-08-25T10:00:00Z',
  updated_at: '2026-08-25T10:00:00Z',
}

describe('ConnectedCalendarsSection', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(userIntegrationsApi.getCalendars).mockReset().mockResolvedValue([mockCalendar])
    vi.mocked(userIntegrationsApi.createCalendar).mockReset().mockResolvedValue(mockCalendar)
    vi.mocked(userIntegrationsApi.deleteCalendar).mockReset().mockResolvedValue(undefined)
    vi.mocked(userIntegrationsApi.testCalendar).mockReset().mockResolvedValue({ ok: true, details: 'OK' })
  })

  it('renders existing calendars with badges and action buttons', async () => {
    render(<ConnectedCalendarsSection />)

    expect(await screen.findByText('Team-Kalender')).toBeInTheDocument()
    expect(screen.getByText(/caldav\.example\.com/)).toBeInTheDocument()
    expect(screen.getByText('Standard')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Verbindung testen/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Entfernen/i })).toBeInTheDocument()
  })

  it('opens add modal with explanation and DNA Checkbox', async () => {
    render(<ConnectedCalendarsSection />)
    await screen.findByText('Team-Kalender')

    fireEvent.click(screen.getByRole('button', { name: /Kalender hinzufügen/i }))

    expect(screen.getByRole('heading', { name: 'Kalender hinzufügen' })).toBeInTheDocument()
    expect(screen.getByText(/Wird zum Abfragen von Terminen und Vorbereiten von Termineinträgen durch den KI-Assistenten verwendet/i)).toBeInTheDocument()

    const checkbox = screen.getByRole('checkbox')
    expect(checkbox).toBeInTheDocument()
  })

  it('tests calendar connection on test button click', async () => {
    render(<ConnectedCalendarsSection />)
    await screen.findByText('Team-Kalender')

    fireEvent.click(screen.getByRole('button', { name: /Verbindung testen/i }))

    await waitFor(() => {
      expect(userIntegrationsApi.testCalendar).toHaveBeenCalledWith(1)
    })
  })
})
