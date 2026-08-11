import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiMemoryManager } from './AiMemoryManager'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listMemory: vi.fn(),
    listPersonalMemory: vi.fn(),
    getMemoryPreference: vi.fn(),
    setMemoryPreference: vi.fn(),
    saveMemory: vi.fn(),
    deleteMemory: vi.fn(),
    clearMemory: vi.fn(),
  },
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn().mockResolvedValue([]) }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))

const entry: AiMemoryEntry = {
  id: '00000000-0000-0000-0000-000000000101',
  scope: 'user',
  server_id: null,
  team_id: null,
  key: 'response.language',
  value: 'Synthetic test preference',
  origin: 'user',
  use_count: 0,
  last_used_at: null,
  created_at: '2026-08-01T12:00:00Z',
  updated_at: '2026-08-01T12:00:00Z',
}

/** Ein von der KI selbst gemerkter Eintrag — muss sichtbar anders aussehen. */
const learned: AiMemoryEntry = {
  ...entry,
  id: '00000000-0000-0000-0000-000000000102',
  key: 'ram.bevorzugt',
  value: '8 GB',
  origin: 'ai',
  use_count: 4,
  last_used_at: '2026-08-05T09:00:00Z',
}

/** Genug Einträge, damit Suche, Filter und Zähler überhaupt erscheinen. */
const viele: AiMemoryEntry[] = [
  entry,
  learned,
  { ...entry, id: '...-103', key: 'zeitzone', value: 'Europe/Berlin' },
  { ...entry, id: '...-104', key: 'anrede', value: 'Du', origin: 'ai' },
]

describe('AiMemoryManager', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: ['ai.memory.use'], server_keys: {} },
      isLoading: false,
      error: null,
    })
    vi.mocked(aiApi.listMemory).mockReset().mockResolvedValue([entry])
    vi.mocked(aiApi.listPersonalMemory).mockReset().mockResolvedValue([entry])
    vi.mocked(aiApi.getMemoryPreference).mockReset().mockResolvedValue({ enabled: true, notice_due: false, notice_hidden: false })
    vi.mocked(aiApi.setMemoryPreference).mockReset().mockResolvedValue({ enabled: false, notice_due: false, notice_hidden: false })
    vi.mocked(aiApi.saveMemory).mockReset().mockResolvedValue(entry)
    vi.mocked(aiApi.clearMemory).mockReset().mockResolvedValue({ removed: 4 })
  })

  it('loads explicit entries and persists the opt-out without exposing hidden values', async () => {
    render(<AiMemoryManager />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Memory im KI-Kontext verwenden' }))
    await waitFor(() => expect(aiApi.setMemoryPreference).toHaveBeenCalledWith(false))

    fireEvent.change(screen.getByLabelText('Schlüssel, z. B. response.language'), { target: { value: 'answer.format' } })
    fireEvent.change(screen.getByLabelText('Präferenz'), { target: { value: 'Use concise synthetic output' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hinzufügen' }))
    await waitFor(() => expect(aiApi.saveMemory).toHaveBeenCalledWith({
      scope: 'user', key: 'answer.format', value: 'Use concise synthetic output',
    }))
  })

  it('marks what the AI remembered on its own and how often it was used', async () => {
    // Ohne diese Kennzeichnung waere nicht erkennbar, ob ein Eintrag eine
    // eigene Ansage ist oder eine Ableitung der KI — und genau daran haengt,
    // wie sehr man ihm trauen sollte.
    vi.mocked(aiApi.listMemory).mockResolvedValue([entry, learned])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue([entry, learned])
    render(<AiMemoryManager />)

    expect(await screen.findByText('von der KI gemerkt')).toBeInTheDocument()
    expect(screen.getByText('4× verwendet')).toBeInTheDocument()
    // Der selbst hinterlegte Eintrag traegt die Kennzeichnung nicht.
    expect(screen.getAllByText('von der KI gemerkt')).toHaveLength(1)
  })

  it('filters by text and by origin', async () => {
    // Der Herkunftsfilter beantwortet die Frage, die sonst niemand beantwortet:
    // "was hat sich die KI ueber mich gemerkt?" — etwas anderes als "was habe
    // ich ihr gesagt?".
    vi.mocked(aiApi.listMemory).mockResolvedValue(viele)
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(viele)
    render(<AiMemoryManager />)

    expect(await screen.findByText('Europe/Berlin')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Erinnerungen durchsuchen'), { target: { value: 'berlin' } })
    expect(screen.getByText('Europe/Berlin')).toBeInTheDocument()
    expect(screen.queryByText('8 GB')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Erinnerungen durchsuchen'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Von der KI' }))
    expect(screen.getByText('8 GB')).toBeInTheDocument()
    expect(screen.queryByText('Europe/Berlin')).not.toBeInTheDocument()
  })

  it('clears the whole scope in one step', async () => {
    // Ein gewachsenes Gedaechtnis Zeile fuer Zeile abzuraeumen hiess vorher:
    // eine Bestaetigung je Eintrag.
    vi.mocked(aiApi.listMemory).mockResolvedValue(viele)
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(viele)
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alle löschen' }))
    await waitFor(() => expect(aiApi.clearMemory).toHaveBeenCalledWith('user', undefined))
  })

  it('edits an entry in place instead of demanding the key again', async () => {
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung bearbeiten: response.language' }))
    const schluessel = screen.getByLabelText('Schlüssel, z. B. response.language') as HTMLInputElement
    expect(schluessel.value).toBe('response.language')
    // Der Schluessel ist die Identitaet des Fakts — beim Bearbeiten gesperrt,
    // sonst legt ein Tippfehler stillschweigend einen zweiten Eintrag an.
    expect(schluessel).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Präferenz'), { target: { value: 'Deutsch' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.saveMemory).toHaveBeenCalledWith({
      scope: 'user', key: 'response.language', value: 'Deutsch',
    }))
  })

  it('shows team knowledge read-only without the manage switch', async () => {
    // Lesen darf jedes Mitglied — `scope_identity` verlangt fuer Team nur
    // Mitgliedschaft. Aendern verlangt den Schalter, und was man nicht darf,
    // soll gar nicht erst als Knopf dastehen.
    vi.mocked(aiApi.listMemory).mockResolvedValue([entry])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue([entry])
    render(<AiMemoryManager scope={{ kind: 'team', teamId: 7, canManage: false }} />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()
    expect(aiApi.listMemory).toHaveBeenCalledWith('team', undefined, 7)
    expect(screen.queryByRole('button', { name: 'Hinzufügen' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Memory-Eintrag löschen/ })).not.toBeInTheDocument()
    // Der Gedaechtnis-Schalter ist eine persoenliche Einstellung und hat in
    // einer Teamansicht nichts verloren.
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('renders nothing without the memory permission', () => {
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: [], server_keys: {} },
      isLoading: false,
      error: null,
    })
    const { container } = render(<AiMemoryManager />)
    expect(container).toBeEmptyDOMElement()
    expect(aiApi.listMemory).not.toHaveBeenCalled()
  })
  it('zeigt serverbezogene Notizen im persoenlichen Bereich, mit Servernamen', async () => {
    // Sie sind persoenlich (`server:{id}:user:{uid}`), die KI schreibt sie, und
    // sie liefen bis eben in jedem Gespraech mit, ohne dass sie irgendwo
    // sichtbar oder loeschbar gewesen waeren: `listMemory('server', ...)` will
    // je Aufruf einen konkreten Server, den niemand raten kann.
    vi.mocked(client.api).mockResolvedValue([{ id: 62, name: 'DayZ-1' }])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue([
      entry,
      { ...entry, id: '...-105', scope: 'server', server_id: 62, key: 'startzeit', value: 'Braucht laengeren Timeout' },
    ])
    render(<AiMemoryManager />)

    expect(await screen.findByText('Braucht laengeren Timeout')).toBeInTheDocument()
    expect(screen.getByText('Server: DayZ-1')).toBeInTheDocument()
    // Der allgemeine Eintrag traegt kein Serverschild.
    expect(screen.queryByText(/^Server: #/)).not.toBeInTheDocument()
  })

  it('faellt auf die Nummer zurueck, wenn der Servername nicht zu holen ist', async () => {
    // Etwa nach einem Rechteentzug. Die eigene Notiz bleibt sichtbar und
    // loeschbar — eigene Daten, die man nicht mehr loeschen kann, waeren das
    // schlechtere Ergebnis.
    vi.mocked(client.api).mockRejectedValue(new Error('kein Zugriff'))
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue([
      { ...entry, id: '...-106', scope: 'server', server_id: 84, key: 'startzeit', value: 'Notiz zu einem entzogenen Server' },
    ])
    render(<AiMemoryManager />)

    expect(await screen.findByText('Notiz zu einem entzogenen Server')).toBeInTheDocument()
    expect(screen.getByText('Server: #84')).toBeInTheDocument()
  })

  it('ändert eine Servernotiz an Ort und Stelle statt eine persönliche Kopie anzulegen', async () => {
    // Ohne den Bereich des Eintrags geht die Korrektur als `scope: 'user'`
    // hinaus. Das Backend sucht dann unter `user:{id}`, findet die Notiz dort
    // nicht und legt eine zweite Zeile mit demselben Schlüssel an: die alte
    // wirkt mit dem alten Wert weiter, und ab da gehen beide Werte gemeinsam in
    // jedes Gespräch über diesen Server.
    vi.mocked(client.api).mockResolvedValue([{ id: 62, name: 'DayZ-1' }])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue([
      { ...entry, id: '...-107', scope: 'server', server_id: 62, key: 'start-timeout', value: 'braucht 120s' },
    ])
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung bearbeiten: start-timeout' }))
    fireEvent.change(screen.getByLabelText('Präferenz'), { target: { value: 'braucht 300s' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.saveMemory).toHaveBeenCalledWith({
      scope: 'server', server_id: 62, key: 'start-timeout', value: 'braucht 300s',
    }))
  })

  it('behält Suchfeld und Herkunftsfilter, solange sie noch etwas bewirken', async () => {
    // Vier Einträge, Suche auf den einzigen Treffer, Treffer gelöscht: die
    // Schranke „erst ab vier" hängte das Suchfeld ab, `sichtbar` filterte aber
    // weiter. Die drei verbliebenen Einträge waren damit unsichtbar, und es gab
    // kein Bedienelement mehr, um den Filter zu leeren.
    const ohneTreffer = viele.filter((row) => row.id !== learned.id)
    vi.mocked(aiApi.listPersonalMemory)
      .mockReset()
      .mockResolvedValueOnce(viele)
      .mockResolvedValue(ohneTreffer)
    render(<AiMemoryManager />)

    fireEvent.change(await screen.findByLabelText('Erinnerungen durchsuchen'), { target: { value: 'ram' } })
    fireEvent.click(screen.getByRole('button', { name: 'Memory-Eintrag löschen: ram.bevorzugt' }))
    await waitFor(() => expect(aiApi.deleteMemory).toHaveBeenCalledWith(learned.id))

    const feld = await screen.findByLabelText('Erinnerungen durchsuchen')
    expect(feld).toHaveValue('ram')
    expect(screen.getByRole('group', { name: 'Nach Herkunft filtern' })).toBeInTheDocument()
    // Und man kommt auch wieder heraus.
    fireEvent.change(feld, { target: { value: '' } })
    expect(screen.getByText('Europe/Berlin')).toBeInTheDocument()
  })
})
