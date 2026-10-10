import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiMemoryImportPreview, type AiMemoryImportPreviewItem } from '@/api/ai'
import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'

import { AiMemoryImportModal } from './AiMemoryImportModal'

vi.mock('@/api/ai', () => ({
  aiApi: {
    importMemoryPreview: vi.fn(),
    executeMemoryImport: vi.fn(),
  },
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

// Alle Personen sind erfunden.
const posten = (teil: Partial<AiMemoryImportPreviewItem>): AiMemoryImportPreviewItem => ({
  text: 'Der Benutzer heißt Jonas.',
  titel: 'Name',
  thema: 'Person',
  art: 'fakt',
  wichtigkeit: 3,
  ersetzt: [],
  ...teil,
})

const vorschau = (items: AiMemoryImportPreviewItem[], rest: Partial<AiMemoryImportPreview> = {}): AiMemoryImportPreview => ({
  detected_source: 'Gemini',
  items,
  total_detected: items.length,
  total_known: 0,
  total_secrets_blocked: 0,
  unread_parts: 0,
  available_slots: 100,
  memory_enabled: true,
  ...rest,
})

const MISCHUNG = [
  posten({ text: 'Der Benutzer heißt Jonas.', titel: 'Name', thema: 'Person' }),
  posten({
    text: 'Der Benutzer trinkt morgens gern Kaffee.', titel: 'Kaffee am Morgen', thema: 'Vorlieben', art: 'vorliebe',
    ersetzt: [
      { id: 'e-1', fassung: 2, text: 'Mag Kaffee.', titel: null },
      { id: 'e-2', fassung: 1, text: 'Trinkt morgens Kaffee.', titel: null },
    ],
  }),
  posten({ text: 'Der Benutzer spielt Schach im Verein.', titel: 'Schach', thema: 'vorlieben', art: 'vorliebe' }),
  posten({ text: 'Antworte immer auf Deutsch.', titel: null, thema: null, art: 'anweisung' }),
]

const ANTWORT = '### 1. Demografische Informationen\n* Der Name der Person ist Jonas.'

async function bisZurVorschau(items = MISCHUNG, rest: Partial<AiMemoryImportPreview> = {}) {
  vi.mocked(aiApi.importMemoryPreview).mockResolvedValue(vorschau(items, rest))
  const onImported = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <AiMemoryImportModal open onOpenChange={onOpenChange} scope={{ kind: 'user' }} onImported={onImported} />,
  )
  fireEvent.change(screen.getByRole('textbox', { name: 'Text' }), { target: { value: ANTWORT } })
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByRole('button', { name: 'Zurück' })
  return { onImported, onOpenChange }
}

const gesendet = () => vi.mocked(aiApi.executeMemoryImport).mock.calls[0][0].items

describe('AiMemoryImportModal', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.importMemoryPreview).mockReset()
    vi.mocked(aiApi.executeMemoryImport).mockReset().mockResolvedValue({
      imported_count: 2, updated_count: 1, skipped_count: 0, skipped: [],
    })
    vi.mocked(toast.success).mockClear()
    vi.mocked(toast.warning).mockClear()
    vi.mocked(toast.error).mockClear()
    vi.mocked(toast.info).mockClear()
  })

  it('zeigt zuerst nur Kopieren und Einfügen — der Prompt liegt hinter einem Klick', () => {
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Prompt' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Prompt ansehen' }))
    expect(screen.getByRole('textbox', { name: 'Prompt' })).toBeInTheDocument()
  })

  it('sagt vor dem Abschicken, dass der Text an den KI-Anbieter geht', () => {
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    expect(screen.getByText(/geht dafür an den KI-Anbieter, ein langer in Teilen, und zählt zu deinem KI-Kontingent/)).toBeInTheDocument()
  })

  it('kopiert den Auszugs-Prompt in die Zwischenablage', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prompt kopieren' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const text = writeText.mock.calls[0][0] as string
    // An der Schlusszeile erkennt das Backend die Quelle.
    expect(text).toContain('Importiert aus: <Name>')
    expect(await screen.findByRole('button', { name: 'Kopiert' })).toBeInTheDocument()
  })

  it('fügt per Knopf aus der Zwischenablage ein und geht gleich zur Auswahl', async () => {
    const readText = vi.fn().mockResolvedValue(ANTWORT)
    Object.defineProperty(navigator, 'clipboard', { value: { readText, writeText: vi.fn() }, configurable: true })
    vi.mocked(aiApi.importMemoryPreview).mockResolvedValue(vorschau(MISCHUNG))
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Einfügen' }))

    await screen.findByRole('button', { name: 'Zurück' })
    expect(aiApi.importMemoryPreview).toHaveBeenCalledWith({ scope: 'user', raw_text: ANTWORT })
  })

  it('zeigt, dass Singra liest, solange das Modell liest', async () => {
    vi.mocked(aiApi.importMemoryPreview).mockReturnValue(new Promise(() => {}))
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Text' }), { target: { value: ANTWORT } })

    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    expect(await screen.findByRole('button', { name: 'Singra liest …' })).toBeDisabled()
  })

  it('sagt es, wenn im Text nichts zum Merken steht', async () => {
    vi.mocked(aiApi.importMemoryPreview).mockResolvedValue(vorschau([]))
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Text' }), { target: { value: 'Heute war es warm.' } })

    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(
      'Im Text steht nichts, was Singra sich merken müsste.',
    ))
    expect(screen.queryByRole('button', { name: 'Zurück' })).not.toBeInTheDocument()
  })

  it('zeigt die Hinweise statt „nichts gefunden“, wenn alles schon gespeichert oder ungelesen ist', async () => {
    await bisZurVorschau([], { total_known: 3, unread_parts: 1 })

    expect(screen.getByText('3 schon gespeichert')).toBeInTheDocument()
    expect(screen.getByText('1 Teil des Textes wurde nicht gelesen.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '0 importieren' })).toBeDisabled()
    expect(toast.info).not.toHaveBeenCalled()
  })

  it('ordnet nach Thema — gleich geschriebene zusammen, ohne Thema zuletzt', async () => {
    await bisZurVorschau()

    const ueberschriften = screen.getAllByText(/^(Person|Vorlieben|vorlieben|Ohne Thema)$/)
    expect(ueberschriften.map((knoten) => knoten.textContent)).toEqual(['Person', 'Vorlieben', 'Ohne Thema'])
    expect(screen.getByText('4 Erinnerungen gefunden')).toBeInTheDocument()
  })

  it('nennt Gesperrtes, Bekanntes und Ungelesenes nur als Zahl', async () => {
    await bisZurVorschau(MISCHUNG, { total_secrets_blocked: 1, total_known: 2, unread_parts: 1 })

    expect(screen.getByText('1 mit Zugangsdaten gesperrt')).toBeInTheDocument()
    expect(screen.getByText('2 schon gespeichert')).toBeInTheDocument()
    expect(screen.getByText('1 Teil des Textes wurde nicht gelesen.')).toBeInTheDocument()
  })

  it('wählt alles vor und ersetzt, was das Modell als Bestand erkannt hat', async () => {
    await bisZurVorschau()

    expect(screen.getByRole('checkbox', { name: 'Übernehmen: Name' })).toBeChecked()
    expect(screen.getByText('Ersetzt: Mag Kaffee. und 1 weiteren')).toBeInTheDocument()
    expect(screen.getAllByText('Ersetzt')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '4 importieren' }))

    await waitFor(() => expect(aiApi.executeMemoryImport).toHaveBeenCalledTimes(1))
    expect(vi.mocked(aiApi.executeMemoryImport).mock.calls[0][0]).toMatchObject({
      scope: 'user', source_provider: 'Gemini',
    })
    expect(gesendet()[1]).toEqual({
      text: 'Der Benutzer trinkt morgens gern Kaffee.', titel: 'Kaffee am Morgen', thema: 'Vorlieben',
      art: 'vorliebe', wichtigkeit: 3,
      ersetzt: [{ id: 'e-1', fassung: 2 }, { id: 'e-2', fassung: 1 }],
    })
    expect(gesendet()[0].ersetzt).toEqual([])
  })

  it('legt einen eigenen Eintrag an, wenn Ersetzen abgewählt ist', async () => {
    await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: 'Ersetzen' }))
    expect(screen.queryByText('Ersetzt')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '4 importieren' }))

    await waitFor(() => expect(aiApi.executeMemoryImport).toHaveBeenCalledTimes(1))
    expect(gesendet()[1].ersetzt).toEqual([])
  })

  it('bearbeitet per Antippen und hält einen leeren Satz zurück', async () => {
    await bisZurVorschau([posten({})])

    fireEvent.click(screen.getByRole('button', { name: 'Der Benutzer heißt Jonas.' }))
    const zeile = screen.getByRole('checkbox', { name: 'Übernehmen: Name' }).closest('li')!
    const feld = within(zeile).getByRole('textbox', { name: 'Erinnerung' })
    fireEvent.change(feld, { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: '1 importieren' })).toBeDisabled()

    fireEvent.change(feld, { target: { value: 'Der Benutzer heißt Jonas Berger.' } })
    fireEvent.click(within(zeile).getByRole('button', { name: 'Fertig' }))
    fireEvent.click(screen.getByRole('button', { name: '1 importieren' }))

    await waitFor(() => expect(aiApi.executeMemoryImport).toHaveBeenCalledTimes(1))
    expect(gesendet()[0].text).toBe('Der Benutzer heißt Jonas Berger.')
  })

  it('wählt mit einem Klick alles ab und wieder an', async () => {
    await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: 'Keine' }))
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: Name' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '0 importieren' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Alle' }))
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: Schach' })).toBeChecked()
  })

  it('hält den Import an, solange mehr Neues gewählt ist, als Platz frei ist', async () => {
    await bisZurVorschau(MISCHUNG, { available_slots: 1 })

    // Drei neue, einer ersetzt zwei — Ersetzen kostet keinen Platz, und der
    // zweite ersetzte Eintrag geht im ersten auf: 1 + 1 frei für 3 neue.
    expect(screen.getByText('Zu wenig Platz: 1 abwählen oder ersetzen.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '4 importieren' })).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Übernehmen: Schach' }))
    expect(screen.getByRole('button', { name: '3 importieren' })).toBeEnabled()

    // Ohne Ersetzen wird nichts frei, und der Kaffee braucht selbst Platz.
    fireEvent.click(screen.getByRole('button', { name: 'Ersetzen' }))
    expect(screen.getByText('Zu wenig Platz: 2 abwählen oder ersetzen.')).toBeInTheDocument()
  })

  it('kennt bei unbegrenztem Vorrat kein Zuviel', async () => {
    await bisZurVorschau(MISCHUNG, { available_slots: null })

    expect(screen.queryByText(/Zu wenig Platz/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '4 importieren' })).toBeEnabled()
  })

  it('meldet Übersprungenes und lädt die Liste nach einem Import neu', async () => {
    vi.mocked(aiApi.executeMemoryImport).mockResolvedValue({
      imported_count: 2, updated_count: 1, skipped_count: 1, skipped: [{ index: 3, reason: 'full' }],
    })
    const { onImported, onOpenChange } = await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: '4 importieren' }))

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1))
    expect(toast.success).toHaveBeenCalledWith('Importiert: 2 neu, 1 ersetzt.')
    expect(toast.warning).toHaveBeenCalledWith('1 Eintrag übersprungen.')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('sagt, wenn das Gedächtnis ausgeschaltet ist', async () => {
    await bisZurVorschau(MISCHUNG, { memory_enabled: false })

    expect(screen.getByText(/Gedächtnis ist aus/)).toBeInTheDocument()
  })

  it('zielt im Team auf das Team', async () => {
    vi.mocked(aiApi.importMemoryPreview).mockResolvedValue(vorschau(MISCHUNG))
    render(
      <AiMemoryImportModal
        open onOpenChange={vi.fn()} scope={{ kind: 'team', teamId: 7, canManage: true }} onImported={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Text' }), { target: { value: 'Jonas plant die Feier.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    await waitFor(() => expect(aiApi.importMemoryPreview).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'team', team_id: 7 }),
    ))
  })
})
