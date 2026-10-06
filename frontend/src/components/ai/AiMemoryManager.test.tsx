import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiMemoryEntry, type AiMemoryPage } from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { confirm } from '@/stores/confirmStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { toast } from '@/stores/toastStore'
import { AiMemoryManager } from './AiMemoryManager'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listScopeMemory: vi.fn(),
    listPersonalMemory: vi.fn(),
    listPersonalTopics: vi.fn(),
    listScopeTopics: vi.fn(),
    getMemoryPreference: vi.fn(),
    setMemoryPreference: vi.fn(),
    createMemory: vi.fn(),
    updateMemory: vi.fn(),
    restoreMemory: vi.fn(),
    listMemoryVersions: vi.fn(),
    restoreMemoryVersion: vi.fn(),
    deleteMemory: vi.fn(),
    clearMemory: vi.fn(),
  },
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn().mockResolvedValue([]) }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn() }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** Altbestand: ein Name und ein Wert, wie bis Gedächtnis v2 alles aussah. */
const entry: AiMemoryEntry = {
  id: '00000000-0000-0000-0000-000000000101',
  scope: 'user',
  server_id: null,
  team_id: null,
  key: 'response.language',
  value: 'Synthetic test preference',
  titel: null,
  thema: null,
  art: null,
  quelle: 'eingetragen',
  wichtigkeit: 3,
  origin: 'user',
  status: 'aktiv',
  vergessen_am: null,
  fassung: 1,
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
  quelle: 'gespraech',
  use_count: 4,
  last_used_at: '2026-08-05T09:00:00Z',
}

/** Seit Gedächtnis v2: ein Satz ohne Namen. */
const satz: AiMemoryEntry = {
  ...entry,
  id: '00000000-0000-0000-0000-000000000110',
  key: null,
  value: 'Der Benutzer startet seine Server am liebsten abends.',
}

/** Ein Satz mit Titel und Thema, der schon einmal geändert wurde. */
const mitTitel: AiMemoryEntry = {
  ...satz,
  id: '00000000-0000-0000-0000-000000000111',
  value: 'Backups laufen jede Nacht um drei Uhr.',
  titel: 'Backupzeit',
  thema: { id: 'thema-betrieb', name: 'Betrieb' },
  fassung: 2,
}

/**
 * Die Seitenhülle um eine Liste, wie sie `GET /ai/memory/personal` liefert.
 *
 * `clearable` wird hier ausgerechnet statt gesetzt, weil das Backend genau das
 * tut: „Alle löschen" trifft im Profil nur die allgemeinen Einträge, die
 * Notizen zu einzelnen Servern stehen daneben und bleiben stehen. Ein
 * hartkodierter Wert ließe die Tests grün, die diese Unterscheidung prüfen.
 */
const seite = (rows: AiMemoryEntry[], rest: Partial<AiMemoryPage> = {}): AiMemoryPage => ({
  entries: rows,
  total: rows.length,
  clearable: rows.filter((row) => row.scope === 'user').length,
  limit: 200,
  ...rest,
})

/**
 * Dieselbe Hülle für einen Bereich (`GET /ai/memory/page`) — dort räumt „Alle
 * löschen" die ganze Kennung ab, `clearable` ist also `total`.
 */
const bereichsSeite = (
  rows: AiMemoryEntry[], rest: Partial<AiMemoryPage> = {},
): AiMemoryPage => ({
  entries: rows, total: rows.length, clearable: rows.length, limit: 200, ...rest,
})

/** Der Filter, mit dem jede Liste ohne Auswahl geholt wird. */
const OHNE_FILTER = { status: 'aktiv', thema: [] }

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
    vi.mocked(aiApi.listScopeMemory).mockReset().mockResolvedValue(bereichsSeite([entry]))
    vi.mocked(aiApi.listPersonalMemory).mockReset().mockResolvedValue(seite([entry]))
    vi.mocked(aiApi.listPersonalTopics).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.listScopeTopics).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.deleteMemory).mockClear()
    vi.mocked(aiApi.getMemoryPreference).mockReset().mockResolvedValue({ enabled: true, notice_due: false, notice_hidden: false })
    vi.mocked(aiApi.setMemoryPreference).mockReset().mockResolvedValue({ enabled: false, notice_due: false, notice_hidden: false })
    vi.mocked(aiApi.createMemory).mockReset().mockResolvedValue(satz)
    vi.mocked(aiApi.updateMemory).mockReset().mockResolvedValue(entry)
    vi.mocked(aiApi.restoreMemory).mockReset().mockResolvedValue(entry)
    vi.mocked(aiApi.listMemoryVersions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.restoreMemoryVersion).mockReset().mockResolvedValue(mitTitel)
    vi.mocked(aiApi.clearMemory).mockReset().mockResolvedValue({ removed: 4 })
    vi.mocked(toast.error).mockClear()
    vi.mocked(toast.success).mockClear()
    // Die Antwort auf die Rückfrage setzt der einzelne Test. Stünde sie in der
    // Attrappe, könnte kein Test mehr zeigen, dass überhaupt gefragt wird.
    vi.mocked(confirm).mockReset().mockResolvedValue(true)
  })

  it('löscht nichts, wenn die Rückfrage verneint wird', async () => {
    // Beide Wege sind endgültig — der einzelne Eintrag wie der ganze Bereich —
    // und beide gehen durch dieselbe Rückfrage. Alle übrigen Tests stimmen ihr
    // zu; ohne diesen hier liesse sich das `if (!await confirm(…)) return` aus
    // beiden Funktionen streichen, ohne dass eine Zeile rot wird.
    vi.mocked(confirm).mockResolvedValue(false)
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(viele))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung löschen: RAM: Bevorzugt' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      // Die Erinnerung steht in der Frage: „wirklich löschen?" allein sagt
      // nicht, was gleich verschwindet.
      message: 'Die Erinnerung „RAM: Bevorzugt“ wirklich löschen?',
      danger: true,
    })))
    expect(aiApi.deleteMemory).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Alle löschen' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2))
    expect(aiApi.clearMemory).not.toHaveBeenCalled()
    // Und der Eintrag steht danach noch da.
    expect(screen.getByText('8 GB')).toBeInTheDocument()
  })

  it('legt einen Satz an und speichert die Abschaltung', async () => {
    render(<AiMemoryManager />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Memory im KI-Kontext verwenden' }))
    await waitFor(() => expect(aiApi.setMemoryPreference).toHaveBeenCalledWith(false))

    fireEvent.change(screen.getByLabelText('Erinnerung'), {
      target: { value: '  Der Benutzer mag kurze Antworten.  ' },
    })
    fireEvent.change(screen.getByLabelText('Thema (optional)'), { target: { value: 'Stil' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hinzufügen' }))
    await waitFor(() => expect(aiApi.createMemory).toHaveBeenCalledWith({
      scope: 'user', text: 'Der Benutzer mag kurze Antworten.', titel: null, thema: 'Stil',
    }))
    // Danach zählen die Themen neu, denn eines kann gerade entstanden sein.
    await waitFor(() => expect(aiApi.listPersonalTopics).toHaveBeenCalledTimes(2))
  })

  it('zeigt einen Satz ohne Namen als Satz und einen mit Titel unter dem Titel', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([satz, mitTitel]))
    render(<AiMemoryManager />)

    expect(await screen.findByText(satz.value)).toBeInTheDocument()
    expect(screen.getByText('Backupzeit')).toBeInTheDocument()
    expect(screen.getByText('Betrieb')).toBeInTheDocument()
    // Kein Rohschlüssel und kein leerer Name davor.
    fireEvent.click(screen.getByRole('button', { name: /Backupzeit/, expanded: false }))
    expect(screen.getByText('Von Hand eingetragen')).toBeInTheDocument()
    expect(screen.queryByText(/^Schlüssel:/)).toBeNull()
  })

  it('marks what the AI remembered on its own and how often it was used', async () => {
    // Ohne diese Kennzeichnung waere nicht erkennbar, ob ein Eintrag eine
    // eigene Ansage ist oder eine Ableitung der KI — und genau daran haengt,
    // wie sehr man ihm trauen sollte.
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([entry, learned]))
    render(<AiMemoryManager />)

    expect(await screen.findByText('von der KI gemerkt')).toBeInTheDocument()
    expect(screen.getByText('4× verwendet')).toBeInTheDocument()
    // Der selbst hinterlegte Eintrag traegt die Kennzeichnung nicht.
    expect(screen.getAllByText('von der KI gemerkt')).toHaveLength(1)
    // Zähler und Aufklappknopf tragen kein natives title.
    expect(document.body.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('filters by text and by origin', async () => {
    // Der Herkunftsfilter beantwortet die Frage, die sonst niemand beantwortet:
    // "was hat sich die KI ueber mich gemerkt?" — etwas anderes als "was habe
    // ich ihr gesagt?".
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(viele))
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
    //
    // Die Servernotiz steht in derselben Liste, wird von „Alle löschen" aber
    // nicht mitgenommen: gelöscht wird über `user:{id}`, sie liegt unter
    // `server:{sid}:user:{uid}`. Die Frage darf deshalb nicht nach fünf
    // Einträgen fragen, um danach vier zu löschen.
    const mitServernotiz: AiMemoryEntry[] = [
      ...viele,
      { ...entry, id: '...-108', scope: 'server', server_id: 62, key: 'startzeit', value: 'Braucht längeren Timeout' },
    ]
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(mitServernotiz))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alle löschen' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Wirklich alle 4 Einträge löschen? Das lässt sich nicht rückgängig machen.',
    })))
    // Weder Team noch Server: der persönliche Bereich hat beides nicht.
    await waitFor(() => expect(aiApi.clearMemory).toHaveBeenCalledWith('user', undefined, undefined))
  })

  it('ändert eine Erinnerung über ihre Kennung und mit der gesehenen Fassung', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([mitTitel]))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung bearbeiten: Backupzeit' }))
    expect(screen.getByLabelText('Erinnerung')).toHaveValue('Backups laufen jede Nacht um drei Uhr.')
    expect(screen.getByLabelText('Titel (optional)')).toHaveValue('Backupzeit')
    expect(screen.getByLabelText('Thema (optional)')).toHaveValue('Betrieb')

    fireEvent.change(screen.getByLabelText('Erinnerung'), { target: { value: 'Backups laufen um vier Uhr.' } })
    fireEvent.change(screen.getByLabelText('Titel (optional)'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    // Ein geleerter Titel geht als `null` hinaus: entfernen, nicht „unverändert".
    await waitFor(() => expect(aiApi.updateMemory).toHaveBeenCalledWith(mitTitel.id, {
      text: 'Backups laufen um vier Uhr.', titel: null, thema: 'Betrieb', fassung: 2,
    }))
    expect(aiApi.createMemory).not.toHaveBeenCalled()
  })

  it('zeigt bei einem Konflikt den neuen Stand und speichert danach gegen ihn', async () => {
    // Ein zweites Fenster oder die Pflege im Hintergrund war schneller. Der
    // eigene Text bleibt stehen; der zweite Versuch schickt die Fassung, die
    // jetzt in der Liste steht — und nicht wieder die alte.
    const neuerStand = { ...mitTitel, value: 'Backups laufen um fünf Uhr.', fassung: 3 }
    vi.mocked(aiApi.listPersonalMemory)
      .mockResolvedValueOnce(seite([mitTitel]))
      .mockResolvedValue(seite([neuerStand]))
    vi.mocked(aiApi.updateMemory)
      .mockRejectedValueOnce(new client.SanitizedApiError(
        'Die Erinnerung wurde inzwischen geändert. Lade neu und versuch es noch einmal.', { status: 409 },
      ))
      .mockResolvedValue(neuerStand)
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung bearbeiten: Backupzeit' }))
    fireEvent.change(screen.getByLabelText('Erinnerung'), { target: { value: 'Backups laufen um vier Uhr.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Die Erinnerung wurde inzwischen geändert. Lade neu und versuch es noch einmal.',
    ))
    expect(await screen.findAllByText('Backups laufen um fünf Uhr.')).not.toHaveLength(0)
    expect(screen.getByLabelText('Erinnerung')).toHaveValue('Backups laufen um vier Uhr.')

    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.updateMemory).toHaveBeenLastCalledWith(mitTitel.id, expect.objectContaining({
      text: 'Backups laufen um vier Uhr.', fassung: 3,
    })))
  })

  it('shows team knowledge read-only without the manage switch', async () => {
    // Lesen darf jedes Mitglied — `scope_identity` verlangt fuer Team nur
    // Mitgliedschaft. Aendern verlangt den Schalter, und was man nicht darf,
    // soll gar nicht erst als Knopf dastehen.
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite([entry]))
    render(<AiMemoryManager scope={{ kind: 'team', teamId: 7, canManage: false }} />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()
    expect(aiApi.listScopeMemory).toHaveBeenCalledWith('team', undefined, 7, 0, OHNE_FILTER)
    expect(aiApi.listScopeTopics).toHaveBeenCalledWith('team', undefined, 7)
    expect(screen.queryByRole('button', { name: 'Hinzufügen' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Erinnerung löschen/ })).not.toBeInTheDocument()
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
    expect(aiApi.listPersonalMemory).not.toHaveBeenCalled()
    expect(aiApi.listScopeMemory).not.toHaveBeenCalled()
  })
  it('zeigt serverbezogene Notizen im persoenlichen Bereich, mit Servernamen', async () => {
    // Sie sind persoenlich (`server:{id}:user:{uid}`), die KI schreibt sie, und
    // sie liefen bis eben in jedem Gespraech mit, ohne dass sie irgendwo
    // sichtbar oder loeschbar gewesen waeren: `listMemory('server', ...)` will
    // je Aufruf einen konkreten Server, den niemand raten kann.
    vi.mocked(client.api).mockResolvedValue([{ id: 62, name: 'DayZ-1' }])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([
      entry,
      { ...entry, id: '...-105', scope: 'server', server_id: 62, key: 'startzeit', value: 'Braucht längeren Timeout' },
    ]))
    render(<AiMemoryManager />)

    expect(await screen.findByText('Braucht längeren Timeout')).toBeInTheDocument()
    expect(screen.getByText('Server: DayZ-1')).toBeInTheDocument()
    // Der allgemeine Eintrag traegt kein Serverschild.
    expect(screen.queryByText(/^Server: #/)).not.toBeInTheDocument()
  })

  it('faellt auf die Nummer zurueck, wenn der Servername nicht zu holen ist', async () => {
    // Etwa nach einem Rechteentzug. Die eigene Notiz bleibt sichtbar und
    // loeschbar — eigene Daten, die man nicht mehr loeschen kann, waeren das
    // schlechtere Ergebnis.
    vi.mocked(client.api).mockRejectedValue(new Error('kein Zugriff'))
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([
      { ...entry, id: '...-106', scope: 'server', server_id: 84, key: 'startzeit', value: 'Notiz zu einem entzogenen Server' },
    ]))
    render(<AiMemoryManager />)

    expect(await screen.findByText('Notiz zu einem entzogenen Server')).toBeInTheDocument()
    expect(screen.getByText('Server: #84')).toBeInTheDocument()
  })

  it('ändert eine Servernotiz an Ort und Stelle statt eine persönliche Kopie anzulegen', async () => {
    // Bis Gedächtnis v2 ging eine Korrektur über Bereich und Schlüssel hinaus,
    // und ohne den Bereich des Eintrags landete sie unter `user:{id}` — eine
    // zweite Zeile, und beide Werte gingen in jedes Gespräch über diesen
    // Server. Seitdem geht sie über die Kennung; einen Bereich gibt es dabei
    // gar nicht mehr zu verwechseln.
    vi.mocked(client.api).mockResolvedValue([{ id: 62, name: 'DayZ-1' }])
    const notiz = { ...entry, id: '...-107', scope: 'server' as const, server_id: 62, key: 'start-timeout', value: 'braucht 120s' }
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([notiz]))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Erinnerung bearbeiten: Start Timeout' }))
    fireEvent.change(screen.getByLabelText('Erinnerung'), { target: { value: 'braucht 300s' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateMemory).toHaveBeenCalledWith('...-107', {
      text: 'braucht 300s', titel: null, thema: null, fassung: 1,
    }))
    expect(aiApi.createMemory).not.toHaveBeenCalled()
  })

  /** Das Wissen einer Anlage, wie es vom Serverreiter kommt. */
  const wissen: AiMemoryEntry = {
    ...entry, id: '...-201', scope: 'server_shared', server_id: 62,
    key: 'whitelist', value: 'Nach dem Start neu laden', origin: 'ai',
  }
  const serverBereich = { kind: 'server_shared', serverId: 62, canManage: true } as const

  it('lädt und speichert das Wissen genau eines Servers', async () => {
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite([wissen]))
    render(<AiMemoryManager scope={serverBereich} />)

    expect(await screen.findByText('Nach dem Start neu laden')).toBeInTheDocument()
    expect(aiApi.listScopeMemory).toHaveBeenCalledWith('server_shared', 62, undefined, 0, OHNE_FILTER)

    fireEvent.change(screen.getByLabelText('Erinnerung'), { target: { value: 'Der Port außen ist 30015.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hinzufügen' }))

    await waitFor(() => expect(aiApi.createMemory).toHaveBeenCalledWith({
      scope: 'server_shared', server_id: 62, text: 'Der Port außen ist 30015.', titel: null, thema: null,
    }))
  })

  it('nimmt die Servernummer auch beim Leeren des Bereichs mit', async () => {
    // Eigener Fall statt einer vierten Zeile im Test darüber: nach dem
    // Speichern steht die Oberfläche noch auf `busy`, und ein Klick auf den
    // dann deaktivierten Knopf bewiese gar nichts.
    //
    // `clearMemory` fehlte die Nummer als einzigem der Memory-Aufrufe. Ohne
    // sie löst das Backend `server_shared` gar nicht erst auf und antwortet
    // 404 — der Knopf „alles löschen" wäre auf diesem Reiter tot gewesen.
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite([wissen]))
    render(<AiMemoryManager scope={serverBereich} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alle löschen' }))

    await waitFor(() => expect(aiApi.clearMemory).toHaveBeenCalledWith('server_shared', undefined, 62))
  })

  it('lädt neu, wenn der Benutzer ohne Umweg zum nächsten Server wechselt', async () => {
    // Die Komponente bleibt dabei stehen — nur die Nummer im Bereich wechselt.
    // Hinge das Laden allein an `scope.kind`, zeigte der zweite Server
    // schweigend die Betriebsanleitung des ersten. Das ist genau die
    // Verwechslung, gegen die auch das Etikett im Kontext steht.
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite([wissen]))
    const { rerender } = render(<AiMemoryManager scope={serverBereich} />)
    await waitFor(() => expect(aiApi.listScopeMemory)
      .toHaveBeenCalledWith('server_shared', 62, undefined, 0, OHNE_FILTER))

    rerender(<AiMemoryManager scope={{ kind: 'server_shared', serverId: 84, canManage: true }} />)

    await waitFor(() => expect(aiApi.listScopeMemory)
      .toHaveBeenCalledWith('server_shared', 84, undefined, 0, OHNE_FILTER))
  })

  it('sagt auf dem Serverreiter, dass die Kollegen mitlesen', async () => {
    // Wer hier etwas hinschreibt, soll das vorher wissen und nicht erst, wenn
    // es jemand zitiert. Der Schalter für das eigene Gedächtnis gehört
    // umgekehrt nicht hierher — er regiert diesen Bereich gar nicht.
    render(<AiMemoryManager scope={{ kind: 'server_shared', serverId: 62, canManage: true }} />)

    expect(await screen.findByText(/alle Kollegen/i)).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'Memory im KI-Kontext verwenden' })).toBeNull()
  })

  it('zeigt fremdes Serverwissen ohne Änderungsknöpfe, wenn man nur lesen darf', async () => {
    // `server.view` reicht zum Lesen, geändert wird mit `server.config.write`.
    // Ein Knopf, der zuverlässig 403 liefert, ist schlechter als keiner.
    const wissen: AiMemoryEntry = {
      ...entry, id: '...-202', scope: 'server_shared', server_id: 62,
      key: 'whitelist', value: 'Nach dem Start neu laden',
    }
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite([wissen]))
    render(<AiMemoryManager scope={{ kind: 'server_shared', serverId: 62, canManage: false }} />)

    expect(await screen.findByText('Nach dem Start neu laden')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /löschen/i })).toBeNull()
    expect(screen.queryByLabelText('Erinnerung')).toBeNull()
  })

  it('behält Suchfeld und Herkunftsfilter, solange sie noch etwas bewirken', async () => {
    // Vier Einträge, Suche auf den einzigen Treffer, Treffer gelöscht: die
    // Schranke „erst ab vier" hängte das Suchfeld ab, `sichtbar` filterte aber
    // weiter. Die drei verbliebenen Einträge waren damit unsichtbar, und es gab
    // kein Bedienelement mehr, um den Filter zu leeren.
    const ohneTreffer = viele.filter((row) => row.id !== learned.id)
    vi.mocked(aiApi.listPersonalMemory)
      .mockReset()
      .mockResolvedValueOnce(seite(viele))
      .mockResolvedValue(seite(ohneTreffer))
    render(<AiMemoryManager />)

    fireEvent.change(await screen.findByLabelText('Erinnerungen durchsuchen'), { target: { value: 'ram' } })
    fireEvent.click(screen.getByRole('button', { name: 'Erinnerung löschen: RAM: Bevorzugt' }))
    await waitFor(() => expect(aiApi.deleteMemory).toHaveBeenCalledWith(learned.id))

    const feld = await screen.findByLabelText('Erinnerungen durchsuchen')
    expect(feld).toHaveValue('ram')
    expect(screen.getByRole('group', { name: 'Nach Herkunft filtern' })).toBeInTheDocument()
    // Und man kommt auch wieder heraus.
    fireEvent.change(feld, { target: { value: '' } })
    expect(screen.getByText('Europe/Berlin')).toBeInTheDocument()
  })

  /**
   * 5.000 Einträge, vier je Seite. Die Seitengröße kommt aus der Antwort, nicht
   * aus der Oberfläche — deshalb steht sie hier und nicht als Konstante im Code.
   */
  const grosserVorrat = seite(viele, { total: 5000, clearable: 5000, limit: 4 })

  it('nennt die Gesamtzahl und blättert, statt still zu deckeln', async () => {
    // Der Vorrat darf 5.000 Einträge fassen, und jeder kostet beim Öffnen einen
    // eigenen Aufruf an den DIS-Sidecar — alles auf einmal wären gemessen rund
    // zehn Sekunden. Ein stiller Deckel wäre hier trotzdem die falsche Antwort:
    // wer aufräumen will, darf nicht 200 von 5.000 zu sehen bekommen, ohne dass
    // es irgendwo steht. Also eine Seite, die Gesamtzahl daneben, und ein Weg
    // zur nächsten.
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(grosserVorrat)
    render(<AiMemoryManager />)

    expect(await screen.findByText('5000 Einträge insgesamt')).toBeInTheDocument()
    expect(screen.getByText('Seite 1 von 1250')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    // Der Offset ist die zweite Seite, nicht der zweite Eintrag: die Größe
    // bestimmt der Server und sagt sie in `limit`.
    await waitFor(() => expect(aiApi.listPersonalMemory).toHaveBeenLastCalledWith(4, OHNE_FILTER))
    expect(await screen.findByText('Seite 2 von 1250')).toBeInTheDocument()
  })

  it('sagt in der Suchbeschriftung, dass sie nur diese Seite kennt', async () => {
    // Der Wert liegt verschlüsselt in der Datenbank; eine Suche über den ganzen
    // Bestand hieße, alle 5.000 Zeilen zu öffnen — genau das, wogegen die
    // Seitenweise gebaut ist. Sie kann also nur die geladene Seite durchsuchen,
    // und dann muss sie das auch sagen.
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(grosserVorrat)
    render(<AiMemoryManager />)

    expect(await screen.findByLabelText('Diese Seite durchsuchen')).toBeInTheDocument()
    // Und der Filter bleibt beim Blättern stehen, statt sich stillschweigend zu
    // leeren: die Suche gilt weiter, nur eben für die nächste Seite.
    fireEvent.change(screen.getByLabelText('Diese Seite durchsuchen'), { target: { value: 'berlin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    await waitFor(() => expect(aiApi.listPersonalMemory).toHaveBeenLastCalledWith(4, OHNE_FILTER))
    expect(screen.getByLabelText('Diese Seite durchsuchen')).toHaveValue('berlin')
  })

  it('fragt beim Leeren nach der Zahl des Servers, nicht nach der Seitenlänge', async () => {
    // Die Ansicht sieht vier von 5.000. Rechnete sie die Frage aus dem aus, was
    // vor ihr liegt, fragte sie nach vier und löschte 5.000 — und der Benutzer
    // hätte genau einmal Gelegenheit, das zu bemerken.
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(grosserVorrat)
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alle löschen' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Wirklich alle 5000 Einträge löschen? Das lässt sich nicht rückgängig machen.',
    })))
  })

  it('rutscht auf die letzte Seite, wenn die eigene beim Löschen verschwindet', async () => {
    // Wer die letzte Zeile der letzten Seite löscht, stünde sonst vor „Seite 2
    // von 1" und einer leeren Liste — die aussieht wie ein leeres Gedächtnis,
    // obwohl vier Einträge da sind.
    vi.mocked(aiApi.listPersonalMemory)
      .mockReset()
      .mockResolvedValueOnce(seite(viele, { total: 8, limit: 4, clearable: 8 }))
      // Die zweite Seite ist inzwischen weg: nur noch vier Einträge insgesamt.
      .mockResolvedValueOnce(seite([], { total: 4, limit: 4, clearable: 4 }))
      .mockResolvedValue(seite(viele, { total: 4, limit: 4, clearable: 4 }))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Weiter' }))

    // Ein Nachschlag auf Offset 0 statt einer leeren Seite 2.
    await waitFor(() => expect(aiApi.listPersonalMemory).toHaveBeenLastCalledWith(0, OHNE_FILTER))
    expect(await screen.findByText('Europe/Berlin')).toBeInTheDocument()
    // Eine Seite bleibt übrig, also verschwindet die Leiste ganz.
    expect(screen.queryByRole('navigation', { name: 'Seitennavigation' })).toBeNull()
  })

  it('blättert auch im Teamwissen', async () => {
    // Der Teambereich hängt am Rollenlimit seines Gründers und darf 5.000
    // Einträge fassen — dieselbe Zehn-Sekunden-Wartezeit wie im Profil, denn
    // jede Zeile kostet beim Öffnen einen eigenen Aufruf an den DIS-Sidecar.
    // Er lud trotzdem alles auf einmal, während das Profil längst blätterte.
    vi.mocked(aiApi.listScopeMemory)
      .mockResolvedValue(bereichsSeite(viele, { total: 5000, clearable: 5000, limit: 4 }))
    render(<AiMemoryManager scope={{ kind: 'team', teamId: 7, canManage: true }} />)

    expect(await screen.findByText('5000 Einträge insgesamt')).toBeInTheDocument()
    expect(screen.getByText('Seite 1 von 1250')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    await waitFor(() => expect(aiApi.listScopeMemory)
      .toHaveBeenLastCalledWith('team', undefined, 7, 4, OHNE_FILTER))
    expect(await screen.findByText('Seite 2 von 1250')).toBeInTheDocument()
  })

  it('fragt beim Leeren eines Bereichs nach dessen ganzem Bestand', async () => {
    // Im Profil ist `clearable` kleiner als `total` — die Servernotizen stehen
    // in derselben Liste und bleiben stehen. In einem Bereich räumt „Alle
    // löschen" die ganze Kennung ab, und die Frage muss das sagen, statt nach
    // den vier Zeilen zu fragen, die gerade vor einem liegen.
    vi.mocked(aiApi.listScopeMemory)
      .mockResolvedValue(bereichsSeite(viele, { total: 5000, clearable: 5000, limit: 4 }))
    render(<AiMemoryManager scope={{ kind: 'team', teamId: 7, canManage: true }} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alle löschen' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Wirklich alle 5000 Einträge löschen? Das lässt sich nicht rückgängig machen.',
    })))
  })

  it('blättert nicht, wo es nichts zu blättern gibt', async () => {
    // Panel und Serverwissen sind bei hundert Einträgen fest gedeckelt und
    // passen immer auf eine Seite. Sie gehen trotzdem denselben Weg — eine
    // Ansicht mit zwei Leseroutinen liefe irgendwann auseinander. Eine Leiste
    // mit zwei toten Knöpfen wäre dort aber nur Kulisse.
    vi.mocked(aiApi.listScopeMemory).mockResolvedValue(bereichsSeite(viele))
    render(<AiMemoryManager scope={{ kind: 'panel', canManage: true }} />)

    expect(await screen.findByText('Europe/Berlin')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Seitennavigation' })).toBeNull()
    expect(screen.getByLabelText('Erinnerungen durchsuchen')).toBeInTheDocument()
  })

  it('fasst gleichnamige Themen zu einem Filter zusammen und filtert beim Server', async () => {
    // „Familie“ allgemein und „familie“ zu einem Server sind zwei Themen in
    // zwei Bereichen, für den Menschen aber ein Wort. Gefiltert wird beim
    // Server: die Liste ist eine Seite, ein Filter darüber fände nur sie.
    vi.mocked(aiApi.listPersonalTopics).mockResolvedValue([
      { id: 'thema-allgemein', name: 'Familie', anzahl: 2 },
      { id: 'thema-server', name: 'familie', anzahl: 1 },
      { id: 'thema-arbeit', name: 'Arbeit', anzahl: 4 },
    ])
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([satz]))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: 'Nach Thema filtern' }))
    const optionen = await screen.findAllByRole('option')
    expect(optionen.map((option) => option.textContent)).toEqual(['Alle Themen', 'Familie3', 'Arbeit4'])
    fireEvent.click(screen.getByRole('option', { name: /Familie/ }))

    await waitFor(() => expect(aiApi.listPersonalMemory).toHaveBeenLastCalledWith(0, {
      status: 'aktiv', thema: ['thema-allgemein', 'thema-server'],
    }))
  })

  it('zeigt Vergessenes mit Zurückholen und endgültigem Löschen, ohne Formular', async () => {
    const vergessen: AiMemoryEntry = {
      ...satz, id: '...-501', status: 'vergessen', origin: 'ai', quelle: 'gespraech',
      vergessen_am: '2026-10-01T08:00:00Z',
    }
    vi.mocked(aiApi.listPersonalMemory)
      .mockResolvedValueOnce(seite([entry]))
      .mockResolvedValue(seite([vergessen]))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Vergessen' }))

    await waitFor(() => expect(aiApi.listPersonalMemory).toHaveBeenLastCalledWith(0, {
      status: 'vergessen', thema: [],
    }))
    expect(await screen.findByText(/30 Tage hier/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Erinnerung')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Alle löschen' })).toBeNull()

    const name = 'Der Benutzer startet seine Server am liebsten abends.'
    fireEvent.click(screen.getByRole('button', { name: /^Der Benutzer startet/, expanded: false }))
    expect(screen.getByText('Vergessen am 01.10.2026, zurückholbar bis 31.10.2026')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: `Zurückholen: ${name}` }))
    await waitFor(() => expect(aiApi.restoreMemory).toHaveBeenCalledWith('...-501'))

    fireEvent.click(screen.getByRole('button', { name: `Endgültig löschen: ${name}` }))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: `„${name}“ endgültig löschen? Das lässt sich nicht rückgängig machen.`,
    })))
    await waitFor(() => expect(aiApi.deleteMemory).toHaveBeenCalledWith('...-501'))
  })

  it('sagt beim Zurückholen, wenn der Bereich voll ist', async () => {
    // Was zurückkommt, zählt wieder gegen das Kontingent der Rolle. Ist es
    // erschöpft, sagt der Server mit Stand und Grenze, woran es liegt.
    const vergessen: AiMemoryEntry = { ...satz, id: '...-502', status: 'vergessen', vergessen_am: '2026-10-01T08:00:00Z' }
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([vergessen]))
    vi.mocked(aiApi.restoreMemory).mockRejectedValue(new client.SanitizedApiError(
      'Voll — dein Gedächtnis führt 100 von 100 erlaubten Einträgen. Einer muss weichen, bevor ein neuer passt.',
      { status: 409 },
    ))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Vergessen' }))
    fireEvent.click(await screen.findByRole('button', { name: /^Zurückholen:/ }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Voll — dein Gedächtnis führt 100 von 100 erlaubten Einträgen. Einer muss weichen, bevor ein neuer passt.',
    ))
  })

  it('zeigt frühere Fassungen und holt eine davon zurück', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([mitTitel]))
    vi.mocked(aiApi.listMemoryVersions).mockResolvedValue([{
      id: 'fassung-1', text: 'Backups laufen jede Nacht um zwei Uhr.', titel: 'Backupzeit',
      grund: 'bearbeitet', von: 'user', erstellt: '2026-10-05T10:00:00Z',
    }])
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: /Backupzeit/, expanded: false }))
    fireEvent.click(screen.getByRole('button', { name: 'Verlauf: Backupzeit' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Backupzeit — Backups laufen jede Nacht um zwei Uhr.')).toBeInTheDocument()
    expect(within(dialog).getByText(/Bearbeitet/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Wiederherstellen' }))

    await waitFor(() => expect(aiApi.restoreMemoryVersion).toHaveBeenCalledWith(mitTitel.id, 'fassung-1', 2))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('bietet keinen Verlauf an, wo es keinen geben kann', async () => {
    // Fassung 1 heißt: nie geändert, also keine frühere Fassung.
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([satz]))
    render(<AiMemoryManager />)

    fireEvent.click(await screen.findByRole('button', { name: /^Der Benutzer startet/, expanded: false }))
    expect(screen.queryByRole('button', { name: /^Verlauf/ })).toBeNull()
  })

  it('formatiert technische Schlüssel automatisch in lesbaren Logbuch-Text', async () => {
    const eintraege: AiMemoryEntry[] = [
      {
        ...entry,
        id: '...-301',
        key: 'vorliebe-getraenke',
        value: 'Kaffee schwarz',
      },
      {
        ...entry,
        id: '...-302',
        key: 'response.language',
        value: 'Deutsch',
      },
    ]
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(eintraege))
    render(<AiMemoryManager />)

    // Formatiert mit Vorliebe-Präfix und Umlauten
    expect(await screen.findByText('Vorliebe: Getränke')).toBeInTheDocument()
    // Formatiert mit Dotted-Category
    expect(screen.getByText('Response: Language')).toBeInTheDocument()
  })

  it('klappt Einträge standardmäßig ein und öffnet sie beim Anklicken (Accordion)', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([entry]))
    render(<AiMemoryManager />)

    // Standardmäßig eingeklappt: aria-expanded ist false
    const toggleButton = await screen.findByRole('button', { expanded: false })
    expect(toggleButton).toBeInTheDocument()
    // Im eingeklappten Zustand steht der Rohschlüssel noch nicht in den Detail-Metadaten
    expect(screen.queryByText('Schlüssel: response.language')).toBeNull()

    // Beim Klick aufklappen
    fireEvent.click(toggleButton)
    expect(toggleButton).toHaveAttribute('aria-expanded', 'true')
    // Nun sind die Detail-Metadaten sichtbar
    expect(screen.getByText('Schlüssel: response.language')).toBeInTheDocument()
    expect(screen.getByText(/Gemerkt:/)).toBeInTheDocument()

    // Erneuter Klick schließt wieder
    fireEvent.click(toggleButton)
    expect(toggleButton).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Schlüssel: response.language')).toBeNull()
  })

  it('unterstützt das gemeinsame Auf- und Zuklappen aller Einträge', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(viele))
    render(<AiMemoryManager />)

    expect(await screen.findByText('Europe/Berlin')).toBeInTheDocument()
    const alleAufklappenBtn = screen.getByRole('button', { name: 'Alle aufklappen' })
    fireEvent.click(alleAufklappenBtn)

    // Jetzt sind alle 4 Einträge aufgeklappt
    expect(screen.getAllByRole('button', { expanded: true })).toHaveLength(4)
    expect(screen.getByText('Schlüssel: response.language')).toBeInTheDocument()
    expect(screen.getByText('Schlüssel: ram.bevorzugt')).toBeInTheDocument()

    // Klick auf "Alle einklappen"
    const alleEinklappenBtn = screen.getByRole('button', { name: 'Alle einklappen' })
    fireEvent.click(alleEinklappenBtn)
    expect(screen.getAllByRole('button', { expanded: false })).toHaveLength(4)
  })

  it('findet Einträge über die Suche anhand des formatierten Schlüssels mit Umlauten', async () => {
    const eintraege: AiMemoryEntry[] = [
      {
        ...entry,
        id: '...-401',
        key: 'vorliebe-getraenke',
        value: 'Kaffee schwarz',
      },
      {
        ...entry,
        id: '...-402',
        key: 'zeitzone',
        value: 'Europe/Berlin',
      },
      {
        ...entry,
        id: '...-403',
        key: 'editor-theme',
        value: 'Dark',
      },
      {
        ...entry,
        id: '...-404',
        key: 'anrede',
        value: 'Du',
      },
    ]
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(eintraege))
    render(<AiMemoryManager />)

    expect(await screen.findByText('Kaffee schwarz')).toBeInTheDocument()

    // Der Benutzer sucht nach "getränke" (mit Umlaut, wie in der UI angezeigt)
    const suchFeld = screen.getByLabelText('Erinnerungen durchsuchen')
    fireEvent.change(suchFeld, { target: { value: 'getränke' } })

    // Der Eintrag mit Schlüssel "vorliebe-getraenke" muss gefunden werden
    expect(screen.getByText('Vorliebe: Getränke')).toBeInTheDocument()
    expect(screen.queryByText('Europe/Berlin')).toBeNull()
    expect(screen.queryByText('Dark')).toBeNull()
  })

  it('findet Sätze auch über Titel und Thema', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([
      satz, mitTitel,
      { ...satz, id: '...-601', value: 'Der Benutzer mag Katzen.' },
      { ...satz, id: '...-602', value: 'Der Benutzer wohnt am Meer.' },
    ]))
    render(<AiMemoryManager />)

    fireEvent.change(await screen.findByLabelText('Erinnerungen durchsuchen'), { target: { value: 'betrieb' } })

    expect(screen.getByText('Backupzeit')).toBeInTheDocument()
    expect(screen.queryByText('Der Benutzer mag Katzen.')).toBeNull()
  })

  it('begrenzt die Anzeige bei vielen Einträgen initial und lädt per Klick progressiv nach (Lazy Loading)', async () => {
    const vieleEintraege: AiMemoryEntry[] = Array.from({ length: 45 }, (_, i) => ({
      ...entry,
      id: `item-${i + 1}`,
      key: `schluessel-${i + 1}`,
      value: `Wert ${i + 1}`,
    }))

    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite(vieleEintraege))
    render(<AiMemoryManager />)

    expect(await screen.findByText('30 von 45 geladen')).toBeInTheDocument()
    // Initial sind genau 30 Elemente gerendert
    expect(screen.getAllByRole('button', { expanded: false })).toHaveLength(30)

    // Klick auf "Mehr laden"
    const mehrLadenBtn = screen.getByRole('button', { name: 'Mehr laden' })
    fireEvent.click(mehrLadenBtn)

    // Jetzt sind alle 45 Elemente sichtbar
    expect(screen.getAllByRole('button', { expanded: false })).toHaveLength(45)
    expect(screen.queryByRole('button', { name: 'Mehr laden' })).toBeNull()
  })

  it('übernimmt bei Klick auf Bearbeiten die Daten in das Schnellerfassungsformular', async () => {
    vi.mocked(aiApi.listPersonalMemory).mockResolvedValue(seite([entry]))
    render(<AiMemoryManager />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()

    // Klick auf Bearbeiten-Button
    const editBtn = screen.getByRole('button', { name: 'Erinnerung bearbeiten: Response: Language' })
    fireEvent.click(editBtn)

    // Formular zeigt "Eintrag bearbeiten" und hat vorbelegte Werte
    expect(screen.getByText('Eintrag bearbeiten')).toBeInTheDocument()
    expect(screen.getByLabelText('Erinnerung')).toHaveValue('Synthetic test preference')

    // Schließen / Abbrechen
    const cancelBtn = screen.getByRole('button', { name: 'Abbrechen' })
    fireEvent.click(cancelBtn)
    expect(screen.getByText('Neuer Eintrag')).toBeInTheDocument()
    expect(screen.getByLabelText('Erinnerung')).toHaveValue('')
  })
})
