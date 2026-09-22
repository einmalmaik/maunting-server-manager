import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import i18n from '@/i18n'
import { Notes, type NoteItem } from './Notes'
import { api } from '@/api/client'
import { teamsApi } from '@/api/teams'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
  },
}))

vi.mock('@/stores/toastStore', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn().mockResolvedValue(true),
}))

const mockNotes: NoteItem[] = [
  {
    id: 1,
    note_uid: 'note-1',
    title: 'Einkaufsliste Supermarkt',
    content: '- [ ] 1x Milch\n- [x] 2x Brot\n- [ ] 6x Eier',
    category: 'shopping',
    color: 'emerald',
    is_pinned: true,
    is_archived: false,
    note_type: 'personal',
    user_id: 1,
    created_at: '2026-08-31T10:00:00Z',
    updated_at: '2026-08-31T10:00:00Z',
  },
  {
    id: 2,
    note_uid: 'note-2',
    title: 'Server Wartungsplan',
    content: 'Wöchentliche Updates für Node-1 und Node-2 durchführen.',
    category: 'work',
    color: 'purple',
    is_pinned: false,
    is_archived: false,
    note_type: 'team',
    team_name: 'DevOps',
    user_id: 1,
    created_at: '2026-08-31T11:00:00Z',
    updated_at: '2026-08-31T11:00:00Z',
  },
]

describe('Notes Component', () => {
  // Der Platzhalter stand hier als fester Satz („Notizen oder Inhalte
  // durchsuchen...") und stammte aus der Zeit vor der Übersetzung. Die Seite
  // holt ihn heute aus `notes.searchPlaceholder`; gesucht wird deshalb über
  // denselben Schlüssel, damit eine Textänderung nicht als Fehlschlag zählt.
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    vi.mocked(api).mockImplementation(async (url) => {
      if (typeof url === 'string' && url.includes('/notes')) {
        return [...mockNotes]
      }
      return []
    })
    vi.mocked(teamsApi.list).mockResolvedValue([])
  })

  it('renders single Neue Notiz button and search bar without redundant headers', async () => {
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>
    )

    const searchInput = await screen.findByPlaceholderText(i18n.t('notes.searchPlaceholder'))
    expect(searchInput).toBeInTheDocument()

    // Der Knopf trägt beide Beschriftungen im Markup — die lange für breite
    // Fenster, die kurze fürs Telefon. Sichtbar ist immer nur eine; im Test
    // ohne Stylesheet stehen beide im Namen. Deshalb Teiltreffer statt
    // Gleichheit. Die Zusage bleibt: **genau einer**, kein zweiter in der
    // Kopfzeile.
    const newNoteButtons = screen.getAllByRole('button', {
      name: new RegExp(i18n.t('notes.newNote')),
    })
    expect(newNoteButtons.length).toBe(1)
  })

  it('renders multiple notes with proper formatting and checklist items', async () => {
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Einkaufsliste Supermarkt')).toBeInTheDocument()
    })
    expect(screen.getByText('Server Wartungsplan')).toBeInTheDocument()
    expect(screen.getByText('Einkauf')).toBeInTheDocument()
    expect(screen.getByText('Server')).toBeInTheDocument()
    expect(screen.getByText('DevOps')).toBeInTheDocument()
    expect(screen.getByText('1x Milch')).toBeInTheDocument()
    expect(screen.getByText('2x Brot')).toBeInTheDocument()
    expect(screen.getByText('6x Eier')).toBeInTheDocument()
  })

  it('filters notes when searching in the search bar', async () => {
    render(
      <MemoryRouter>
        <Notes />
      </MemoryRouter>
    )

    const searchInput = await screen.findByPlaceholderText(i18n.t('notes.searchPlaceholder'))
    await waitFor(() => {
      expect(screen.getByText('Einkaufsliste Supermarkt')).toBeInTheDocument()
    })

    fireEvent.change(searchInput, { target: { value: 'Wartung' } })

    await waitFor(() => {
      expect(screen.queryByText('Einkaufsliste Supermarkt')).not.toBeInTheDocument()
      expect(screen.getByText('Server Wartungsplan')).toBeInTheDocument()
    })
  })
})
