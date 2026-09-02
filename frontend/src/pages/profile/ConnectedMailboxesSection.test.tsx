import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { userIntegrationsApi, type MailboxItem } from '@/api/userIntegrations'
import i18n from '@/i18n'
import { ConnectedMailboxesSection } from './ConnectedMailboxesSection'

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

const mockMailbox: MailboxItem = {
  id: 1,
  name: 'Arbeitskonto',
  email: 'work@example.com',
  provider_type: 'custom',
  is_default: true,
  imap_host: 'imap.example.com',
  imap_port: 993,
  imap_use_ssl: true,
  smtp_host: 'smtp.example.com',
  smtp_port: 587,
  smtp_use_tls: true,
  imap_username: 'work@example.com',
  smtp_username: 'work@example.com',
  has_credentials: true,
  sync_enabled: true,
  notify_filter_rules: [],
  created_at: '2026-08-25T10:00:00Z',
  updated_at: '2026-08-25T10:00:00Z',
}

describe('ConnectedMailboxesSection', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(userIntegrationsApi.getMailboxes).mockReset().mockResolvedValue([mockMailbox])
    vi.mocked(userIntegrationsApi.createMailbox).mockReset().mockResolvedValue(mockMailbox)
    vi.mocked(userIntegrationsApi.deleteMailbox).mockReset().mockResolvedValue(undefined)
    vi.mocked(userIntegrationsApi.testMailbox).mockReset().mockResolvedValue({ ok: true, details: 'OK' })
  })

  it('renders existing mailboxes with badges and action buttons', async () => {
    render(<ConnectedMailboxesSection />)

    expect(await screen.findByText('Arbeitskonto')).toBeInTheDocument()
    expect(screen.getByText('work@example.com')).toBeInTheDocument()
    expect(screen.getByText('Standard')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Verbindung testen/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Entfernen/i })).toBeInTheDocument()
  })

  it('opens add modal via portal with explanation and DNA Checkboxes', async () => {
    render(<ConnectedMailboxesSection />)
    await screen.findByText('Arbeitskonto')

    fireEvent.click(screen.getByRole('button', { name: /Postfach hinzufügen/i }))

    expect(screen.getByRole('heading', { name: 'Postfach hinzufügen' })).toBeInTheDocument()
    expect(screen.getByText(/Passwörter werden mit DIS AES-256-GCM verschlüsselt gespeichert/i)).toBeInTheDocument()
    expect(screen.getByText(/Du kannst nur IMAP \(nur Lesen\), nur SMTP \(nur Senden\) oder beides zusammen eintragen/i)).toBeInTheDocument()

    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBeGreaterThanOrEqual(2)
  })

  it('tests mailbox connection on test button click', async () => {
    render(<ConnectedMailboxesSection />)
    await screen.findByText('Arbeitskonto')

    fireEvent.click(screen.getByRole('button', { name: /Verbindung testen/i }))

    await waitFor(() => {
      expect(userIntegrationsApi.testMailbox).toHaveBeenCalledWith(1)
    })
  })
})
