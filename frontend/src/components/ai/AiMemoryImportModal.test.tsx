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

const posten = (teil: Partial<AiMemoryImportPreviewItem>): AiMemoryImportPreviewItem => ({
  key: 'name',
  value: 'Heißt Alex',
  category: 'demografie',
  evidence: null,
  status: 'new',
  existing_key: null,
  existing_value: null,
  similarity: null,
  ...teil,
})

const vorschau = (items: AiMemoryImportPreviewItem[], rest: Partial<AiMemoryImportPreview> = {}): AiMemoryImportPreview => ({
  detected_source: 'Gemini',
  items,
  total_detected: items.length,
  total_valid: items.filter((item) => item.status !== 'has_secret').length,
  total_conflicts: items.filter((item) => item.status === 'exact_duplicate' || item.status === 'similar_existing').length,
  total_secrets_blocked: items.filter((item) => item.status === 'has_secret').length,
  available_slots: 100,
  memory_enabled: true,
  ...rest,
})

const MISCHUNG = [
  posten({ key: 'name', value: 'Heißt Alex', status: 'new' }),
  posten({
    key: 'antworte_deutsch', value: 'Antworte immer auf Deutsch.', category: 'anweisung',
    status: 'similar_existing', existing_key: 'sprach_praeferenz',
    existing_value: 'Antworte auf Deutsch.', similarity: 0.61,
  }),
  posten({
    key: 'wohnort', value: 'Wohnt in Berlin.', status: 'exact_duplicate',
    existing_key: 'wohnort', existing_value: 'Wohnt in Hamburg',
  }),
  posten({ key: 'beruf', value: 'Arbeitet als Entwickler.', status: 'exact_duplicate',
    existing_key: 'beruf', existing_value: 'Arbeitet als Entwickler.' }),
  posten({ key: 'passwort', value: 'Das Passwort ist [REDACTED]', category: 'anweisung', status: 'has_secret' }),
]

const ANTWORT = '1. Demografische Informationen\n* Die Person heißt Alex.'

async function bisZurVorschau(items = MISCHUNG, rest: Partial<AiMemoryImportPreview> = {}) {
  vi.mocked(aiApi.importMemoryPreview).mockResolvedValue(vorschau(items, rest))
  const onImported = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <AiMemoryImportModal open onOpenChange={onOpenChange} scope={{ kind: 'user' }} onImported={onImported} />,
  )
  fireEvent.change(screen.getByRole('textbox', { name: 'Antwort' }), { target: { value: ANTWORT } })
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByRole('button', { name: 'Zurück' })
  return { onImported, onOpenChange }
}

describe('AiMemoryImportModal', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.importMemoryPreview).mockReset()
    vi.mocked(aiApi.executeMemoryImport).mockReset().mockResolvedValue({
      imported_count: 2, updated_count: 0, skipped_count: 0, skipped: [],
    })
    vi.mocked(toast.success).mockClear()
    vi.mocked(toast.warning).mockClear()
    vi.mocked(toast.error).mockClear()
  })

  it('zeigt zuerst nur Kopieren und Einfügen — keine Quellenwahl, der Prompt liegt hinter einem Klick', () => {
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Prompt' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Prompt ansehen' }))
    expect(screen.getByRole('textbox', { name: 'Prompt' })).toBeInTheDocument()
  })

  it('kopiert den Auszugs-Prompt in die Zwischenablage', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<AiMemoryImportModal open onOpenChange={vi.fn()} scope={{ kind: 'user' }} onImported={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prompt kopieren' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const text = writeText.mock.calls[0][0] as string
    // Die Schlusszeile ist das, woran der Parser die Quelle erkennt.
    expect(text).toContain('Importiert aus: <Name>')
    expect(text).toContain('1. Demografische Informationen')
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

  it('listet nur, worüber zu entscheiden ist — Gesperrtes und Gleiches nur als Zahl', async () => {
    await bisZurVorschau()

    expect(screen.getByText('3 Erinnerungen gefunden')).toBeInTheDocument()
    expect(screen.getByText('1 mit Zugangsdaten gesperrt')).toBeInTheDocument()
    expect(screen.getByText('1 schon gespeichert')).toBeInTheDocument()
    expect(screen.queryByText('Das Passwort ist [REDACTED]')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Übernehmen: beruf' })).not.toBeInTheDocument()
    // Technisches bleibt verborgen, bis jemand bearbeitet.
    expect(screen.queryByRole('textbox', { name: 'Schlüssel' })).not.toBeInTheDocument()
  })

  it('wählt Neues und Ähnliches vor und ersetzt nichts ungefragt', async () => {
    await bisZurVorschau()

    expect(screen.getByRole('checkbox', { name: 'Übernehmen: name' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: antworte_deutsch' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: wohnort' })).not.toBeChecked()
    expect(screen.getByText('Ähnlich: Antworte auf Deutsch.')).toBeInTheDocument()
    expect(screen.getByText('Bisher: Wohnt in Hamburg')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '2 importieren' }))

    await waitFor(() => expect(aiApi.executeMemoryImport).toHaveBeenCalledWith({
      scope: 'user',
      source_provider: 'Gemini',
      items: [
        { key: 'name', value: 'Heißt Alex', replace_existing: false },
        // „Ähnlich" wird ein eigener Eintrag — zusammenlegen ist teurer als doppeln.
        { key: 'antworte_deutsch', value: 'Antworte immer auf Deutsch.', replace_existing: false },
      ],
    }))
  })

  it('ersetzt einen ähnlichen oder belegten Eintrag nur, wenn es gewählt wurde', async () => {
    await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: 'Ersetzen' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Übernehmen: wohnort' }))
    expect(screen.getAllByText('Ersetzt')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '3 importieren' }))

    await waitFor(() => expect(aiApi.executeMemoryImport).toHaveBeenCalled())
    expect(vi.mocked(aiApi.executeMemoryImport).mock.calls[0][0].items).toEqual([
      { key: 'name', value: 'Heißt Alex', replace_existing: false },
      { key: 'sprach_praeferenz', value: 'Antworte immer auf Deutsch.', replace_existing: true },
      { key: 'wohnort', value: 'Wohnt in Berlin.', replace_existing: true },
    ])
  })

  it('bearbeitet per Antippen und hält ungültige Schlüssel zurück', async () => {
    await bisZurVorschau([posten({ key: 'name', value: 'Heißt Alex' })])

    fireEvent.click(screen.getByRole('button', { name: 'Heißt Alex' }))
    const zeile = screen.getByRole('checkbox', { name: 'Übernehmen: name' }).closest('li')!
    fireEvent.change(within(zeile).getByRole('textbox', { name: 'Schlüssel' }), { target: { value: 'mein name' } })
    expect(within(zeile).getByText(/Schlüssel: nur a–z/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '1 importieren' })).toBeDisabled()

    fireEvent.change(within(zeile).getByRole('textbox', { name: 'Schlüssel' }), { target: { value: 'rufname' } })
    fireEvent.change(within(zeile).getByRole('textbox', { name: 'Inhalt' }), { target: { value: 'Alex' } })
    fireEvent.click(within(zeile).getByRole('button', { name: 'Fertig' }))
    fireEvent.click(screen.getByRole('button', { name: '1 importieren' }))

    await waitFor(() => expect(vi.mocked(aiApi.executeMemoryImport).mock.calls[0][0].items).toEqual([
      { key: 'rufname', value: 'Alex', replace_existing: false },
    ]))
  })

  it('wählt mit einem Klick alles ab und wieder an', async () => {
    await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: 'Alle' }))
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: wohnort' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Keine' }))
    expect(screen.getByRole('checkbox', { name: 'Übernehmen: name' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '0 importieren' })).toBeDisabled()
  })

  it('hält den Import an, solange mehr Neues gewählt ist, als Platz frei ist', async () => {
    await bisZurVorschau(MISCHUNG, { available_slots: 1 })

    expect(screen.getByText('Zu wenig Platz: 1 abwählen oder ersetzen.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2 importieren' })).toBeDisabled()

    // Ersetzen kostet keinen Platz.
    fireEvent.click(screen.getByRole('button', { name: 'Ersetzen' }))
    expect(screen.getByRole('button', { name: '2 importieren' })).toBeEnabled()
  })

  it('meldet Übersprungenes und lädt die Liste nach einem Import neu', async () => {
    vi.mocked(aiApi.executeMemoryImport).mockResolvedValue({
      imported_count: 1, updated_count: 0, skipped_count: 1,
      skipped: [{ key: 'antworte_deutsch', reason: 'full' }],
    })
    const { onImported, onOpenChange } = await bisZurVorschau()

    fireEvent.click(screen.getByRole('button', { name: '2 importieren' }))

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1))
    expect(toast.success).toHaveBeenCalledWith('Importiert: 1 neu, 0 ersetzt.')
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
    fireEvent.change(screen.getByRole('textbox', { name: 'Antwort' }), { target: { value: 'Name: Alex' } })
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

    await waitFor(() => expect(aiApi.importMemoryPreview).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'team', team_id: 7 }),
    ))
  })
})
