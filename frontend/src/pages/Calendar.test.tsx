import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { Calendar } from './Calendar'
import { grundbestandZuruecksetzen } from '@/lib/offlineSync'

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
    expect(screen.getByText('Kalender')).toBeInTheDocument()
    expect(screen.getByText('Push testen')).toBeInTheDocument()
    expect(screen.getByText('Abonnieren')).toBeInTheDocument()
    expect(screen.queryByText('Aktualisieren')).not.toBeInTheDocument()

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

  it('opens create event modal when clicking on a calendar day', async () => {
    vi.mocked(client.api).mockResolvedValue([])

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    // In month view, clicking the 15th day number cell opens the creation modal
    const dayCells = screen.getAllByText('15')
    fireEvent.click(dayCells[0])

    expect(screen.getByPlaceholderText('z. B. Team-Meeting, Wartung Server 1')).toBeInTheDocument()
    expect(screen.getByText('Speichern')).toBeInTheDocument()
  })

  it('triggers test-reminder API when clicking Push testen button', async () => {
    vi.mocked(client.api).mockImplementation(async (url: string) => {
      if (url === '/calendar/test-reminder') {
        return {
          status: 'success',
          email_sent: true,
          device_notifications_enabled: true,
          title: 'Test-Termin: Server-Wartung & Backup-Check',
          start: '27.08.2026 um 14:00 Uhr',
          time_hint: 'in 1 Tag',
        }
      }
      return []
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

  it('renders category filter buttons and filters events', async () => {
    vi.mocked(client.api).mockImplementation(async (url: string) => {
      if (url.includes('/calendar/events')) {
        return [
          {
            id: 1,
            event_id: 'evt-1',
            title: 'Team Meeting',
            start: '2026-08-28T10:00:00Z',
            end: '2026-08-28T11:00:00Z',
            event_type: 'team',
            team_name: 'DevOps',
            color: 'emerald',
          },
          {
            id: 2,
            event_id: 'evt-2',
            title: 'Server Reboot',
            start: '2026-08-28T14:00:00Z',
            end: '2026-08-28T15:00:00Z',
            event_type: 'server',
            server_name: 'Node-1',
            color: 'purple',
          },
        ]
      }
      return []
    })

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    expect(screen.getByText('Alle')).toBeInTheDocument()
    expect(screen.getByText('Persönlich')).toBeInTheDocument()
    expect(screen.getByText('Team')).toBeInTheDocument()
    expect(screen.getByText('Server-Wartung')).toBeInTheDocument()
    expect(screen.getByText('Node')).toBeInTheDocument()

    // Click on Server filter button
    const serverFilterBtn = screen.getByRole('button', { name: /Server-Wartung/i })
    fireEvent.click(serverFilterBtn)

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith(expect.stringContaining('event_type=server'))
    })
  })

  describe('Tageszuordnung: das Ende ist exklusiv', () => {
    /** Ortszeit-Mitternacht des Tages `tag` im aktuellen Monat, als ISO. */
    const mitternacht = (tag: number) => {
      const jetzt = new Date()
      return new Date(jetzt.getFullYear(), jetzt.getMonth(), tag, 0, 0, 0).toISOString()
    }
    const uhrzeit = (tag: number, stunde: number) => {
      const jetzt = new Date()
      return new Date(jetzt.getFullYear(), jetzt.getMonth(), tag, stunde, 0, 0).toISOString()
    }

    async function zeige(termin: Record<string, unknown>) {
      grundbestandZuruecksetzen()
      localStorage.clear()
      vi.mocked(client.api).mockResolvedValue([
        { id: 1, event_id: 'evt', recurrence: '{"rrule":null}', ...termin },
      ] as any)
      render(
        <MemoryRouter>
          <Calendar />
        </MemoryRouter>,
      )
      await waitFor(() => {
        expect(screen.getAllByText(String(termin.title)).length).toBeGreaterThan(0)
      })
      return screen.getAllByText(String(termin.title)).length
    }

    it('zeigt einen ganztaegigen Termin auf genau einem Tag', async () => {
      // So speichert MSM einen eintaegigen ganztaegigen Termin, und so schreibt
      // `export_ical` ihn als `DTEND;VALUE=DATE:` heraus: das Ende ist
      // Mitternacht des Folgetags. Mit `evEnd >= dayStart` sass jeder
      // Geburtstag auf zwei Tagen.
      expect(
        await zeige({
          title: 'Geburtstag Ganztag',
          start: mitternacht(14),
          end: mitternacht(15),
          all_day: true,
        }),
      ).toBe(1)
    })

    it('zaehlt einen Termin, der um Mitternacht endet, nicht zum Folgetag', async () => {
      expect(
        await zeige({ title: 'Spaete Wartung', start: uhrzeit(20, 23), end: mitternacht(21) }),
      ).toBe(1)
    })

    it('zeigt einen mehrtaegigen Termin auf allen Tagen, die er wirklich belegt', async () => {
      // 14. bis 17. Mitternacht sind drei Tage, nicht vier.
      expect(
        await zeige({
          title: 'Mehrtaegige Wartung',
          start: mitternacht(14),
          end: mitternacht(17),
          all_day: true,
        }),
      ).toBe(3)
    })

    it('zeigt einen punktuellen Termin ohne Dauer trotzdem', async () => {
      // `end === start`: es gibt kein Ende, das nach dem Tagesbeginn liegen
      // koennte. Ohne die Ausnahme verschwaende so ein Meilenstein ganz.
      expect(
        await zeige({ title: 'Meilenstein', start: uhrzeit(9, 14), end: uhrzeit(9, 14) }),
      ).toBe(1)
    })
  })

  it('behaelt beim Bearbeiten eine unlesbare Wiederholungsregel', async () => {
    // Auf einem Geraet ohne Schluessel ist die Regel ein Umschlag. Das
    // Formular zeigt dann "keine Wiederholung"; wer nur den Titel aendert,
    // darf die Serie damit nicht loeschen oder doppelt verschluesseln.
    grundbestandZuruecksetzen()
    localStorage.clear()
    const umschlag = 'sv-cal-v1:unlesbar-auf-diesem-geraet'
    const heute = new Date()
    const start = new Date(heute.getFullYear(), heute.getMonth(), heute.getDate(), 12).toISOString()
    const ende = new Date(heute.getFullYear(), heute.getMonth(), heute.getDate(), 13).toISOString()
    vi.mocked(client.api).mockImplementation(async (pfad: string) => {
      if (pfad.startsWith('/calendar/events')) {
        return [
          {
            id: 1, event_id: 'evt-serie', title: 'Jour fixe', start, end: ende,
            recurrence: umschlag, event_type: 'personal', can_edit: true,
          },
        ] as any
      }
      return {} as any
    })

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getAllByText('Jour fixe').length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByText('Jour fixe')[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Speichern' }))

    await waitFor(() => {
      const geschrieben = vi
        .mocked(client.api)
        .mock.calls.filter(([p, o]) => p === '/calendar/events/evt-serie' && (o as any)?.method === 'PUT')
      expect(geschrieben).toHaveLength(1)
      expect(JSON.parse(String((geschrieben[0][1] as any).body)).recurrence).toBe(umschlag)
    })
  })
})

