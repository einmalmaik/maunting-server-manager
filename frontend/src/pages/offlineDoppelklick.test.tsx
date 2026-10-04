/**
 * Ungeduldige Klicks ohne Server.
 *
 * Befund 04.10.2026 (Tauri und Android, Server stumm): dreimal schnell auf
 * „Notiz erstellen" legte drei Notizen an. Das Gerät zeigte nur eine, weil die
 * drei Speichervorgänge denselben lokalen Stand lasen und sich gegenseitig
 * überschrieben; die Warteschlange hielt aber alle drei, und nach dem Abgleich
 * standen drei Kopien da. Eine davon zu löschen half nicht, die anderen kamen
 * wieder.
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { Notes } from './Notes'
import { Calendar } from './Calendar'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { leereOfflineAblage } from '@/lib/offlineAblage'
import {
  getOfflineCalendarEvents,
  getOfflineNotes,
  getOutbox,
  grundbestandZuruecksetzen,
  saveCalendarEventOffline,
  saveNoteOffline,
} from '@/lib/offlineSync'
import { clearNotesKeyCache, setUserNotesKey } from '@/services/notesCalendarCrypto'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: { list: vi.fn(() => new Promise(() => {})) },
}))

const creates = (entity: string) => getOutbox().filter((m) => m.entity === entity && m.action === 'create')

describe('Offline: ungeduldige Klicks', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(async () => {
    // Das Gerät hat seinen Notizschlüssel schon. Ohne ihn wartet das
    // Speichern absichtlich auf den Server (eigener Fall, nicht dieser).
    clearNotesKeyCache()
    await setUserNotesKey(1, btoa(String.fromCharCode(...new Uint8Array(32).fill(7))))
    leereOfflineAblage()
    grundbestandZuruecksetzen()
    setzeAngemeldetesKonto(1)
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(() => new Promise(() => {}))
  })

  afterEach(() => {
    clearNotesKeyCache()
    leereOfflineAblage()
    setzeAngemeldetesKonto(null)
  })

  it('zwei Notizen zugleich gespeichert: beide bleiben auf dem Gerät', async () => {
    await Promise.all([saveNoteOffline({ title: 'erste' }), saveNoteOffline({ title: 'zweite' })])
    expect(getOfflineNotes().map((n) => n.title).sort()).toEqual(['erste', 'zweite'])
    expect(creates('note')).toHaveLength(2)
  })

  it('zwei Termine zugleich gespeichert: beide bleiben auf dem Gerät', async () => {
    const start = new Date().toISOString()
    await Promise.all([
      saveCalendarEventOffline({ title: 'eins', start_time: start, end_time: start }),
      saveCalendarEventOffline({ title: 'zwei', start_time: start, end_time: start }),
    ])
    expect(getOfflineCalendarEvents().map((e) => e.title).sort()).toEqual(['eins', 'zwei'])
    expect(creates('calendar')).toHaveLength(2)
  })

  it('dreimal schnell auf „Notiz erstellen": eine Notiz', async () => {
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('notes.newNote')) }))
    fireEvent.change(screen.getByPlaceholderText(i18n.t('notes.formTitlePlaceholder')), {
      target: { value: 'Ungeduld' },
    })
    const knopf = screen.getByRole('button', { name: i18n.t('notes.createAction') })
    await act(async () => {
      fireEvent.click(knopf)
      fireEvent.click(knopf)
      fireEvent.click(knopf)
    })
    await screen.findByText('Ungeduld')
    expect(getOfflineNotes().filter((n) => n.title === 'Ungeduld')).toHaveLength(1)
    expect(creates('note')).toHaveLength(1)
  })

  it('dreimal schnell auf „Speichern" im Kalender: ein Termin', async () => {
    const { container } = render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>,
    )
    const heute = container.querySelector('div.cursor-pointer.ring-1') as HTMLElement
    fireEvent.click(heute)
    fireEvent.change(screen.getByPlaceholderText(i18n.t('calendar.eventTitlePlaceholder')), {
      target: { value: 'Ungeduldstermin' },
    })
    const knopf = screen.getByRole('button', { name: i18n.t('common.save') })
    await act(async () => {
      fireEvent.click(knopf)
      fireEvent.click(knopf)
      fireEvent.click(knopf)
    })
    await screen.findByText('Ungeduldstermin')
    expect(getOfflineCalendarEvents().filter((e) => e.title === 'Ungeduldstermin')).toHaveLength(1)
    expect(creates('calendar')).toHaveLength(1)
  })
})
