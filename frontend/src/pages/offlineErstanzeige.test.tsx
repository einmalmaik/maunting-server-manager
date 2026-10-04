/**
 * Server nicht erreichbar: Notizen und Kalender zeigen den lokalen Spiegel
 * sofort, nicht erst nach der Zeitgrenze des Netzes.
 *
 * Befund 04.10.2026 in MSS (Tauri) mit abgeschaltetem Backend: jede Anfrage
 * lief erst nach rund 21 s ab, und vor dem eigentlichen Abruf stehen zwei
 * weitere (Schlüsselabgleich). Die Notizen standen 42 s als Ladegerüst da, die
 * Termine fehlten 63 s im sonst sichtbaren Kalender. Hier hängt jede Anfrage
 * für immer; vorher erschien der Eintrag in keinem der beiden Fälle.
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { Notes, type NoteItem } from './Notes'
import { Calendar, type CalendarEventItem } from './Calendar'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { leereOfflineAblage } from '@/lib/offlineAblage'
import { grundbestandZuruecksetzen, setOfflineCalendarEvents, setOfflineNotes } from '@/lib/offlineSync'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: { list: vi.fn(() => new Promise(() => {})) },
}))

const NOTIZ: NoteItem = {
  id: 7,
  note_uid: 'notiz-offline-1',
  title: 'Notiz aus dem Spiegel',
  content: 'liegt lokal',
  category: 'personal',
  color: 'primary',
  is_pinned: false,
  is_archived: false,
  note_type: 'personal',
  user_id: 1,
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-01T10:00:00Z',
}

function termin(titel: string, tag: Date): CalendarEventItem {
  const start = new Date(tag.getFullYear(), tag.getMonth(), tag.getDate(), 12, 0, 0)
  const ende = new Date(start.getTime() + 3600_000)
  return {
    id: 9,
    event_id: 'termin-' + titel,
    title: titel,
    start: start.toISOString(),
    end: ende.toISOString(),
    description: '',
    location: '',
    color: 'blue',
    event_type: 'personal',
    user_id: 1,
  } as CalendarEventItem
}

describe('Offline: lokaler Spiegel ohne Warten auf das Netz', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    leereOfflineAblage()
    grundbestandZuruecksetzen()
    setzeAngemeldetesKonto(1)
    // Wie ein Server, der nicht antwortet: keine Antwort, kein Fehler.
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(() => new Promise(() => {}))
  })

  afterEach(() => {
    leereOfflineAblage()
    setzeAngemeldetesKonto(null)
  })

  it('Notizen stehen da, obwohl der Server nicht antwortet', async () => {
    setOfflineNotes([NOTIZ])

    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Notiz aus dem Spiegel', {}, { timeout: 1000 })).toBeInTheDocument()
  })

  it('Termine stehen im Kalender, obwohl der Server nicht antwortet', async () => {
    setOfflineCalendarEvents([termin('Termin aus dem Spiegel', new Date())])

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Termin aus dem Spiegel', {}, { timeout: 1000 })).toBeInTheDocument()
  })
})
