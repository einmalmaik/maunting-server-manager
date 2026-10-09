import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const sync = vi.hoisted(() => ({
  kalenderVorkommenLokal: vi.fn(),
  loadCalendarEventsOfflineFirst: vi.fn(),
  getOfflineNotes: vi.fn(),
  loadNotesOfflineFirst: vi.fn(),
}))
vi.mock('@/lib/offlineSync', () => sync)
vi.mock('@/services/notesCalendarCrypto', () => ({ NOTE_CIPHERTEXT_PREFIX: 'sv-note-v1:', CALENDAR_CIPHERTEXT_PREFIX: 'sv-cal-v1:' }))

const { Schnellzugriffe } = await import('./Schnellzugriffe')
const { kachelAdresse } = await import('./KachelDialog')
const { Widgets } = await import('./Widgets')
const { abrufeVergessen, letzteNotizen, naechsteTermine } = await import('./widgetDaten')
const { useEinstellungenStore, VORGABE_SCHNELLZUGRIFFE } = await import('../../services/einstellungenStore')
const { useSitzung } = await import('../../services/sitzung')
const { useVerlaufStore } = await import('../../services/verlaufStore')
const { useToastStore } = await import('@/stores/toastStore')
const { usePermissionsStore } = await import('@/stores/permissionsStore')

const zeigen = (inhalt: React.ReactNode) => render(<MemoryRouter>{inhalt}</MemoryRouter>)
const kacheln = () => screen.getAllByRole('button').filter((b) => b.hasAttribute('data-kachel'))
const urls = () => useEinstellungenStore.getState().schnellzugriffe.map((s) => s.url)

const vorkommen = (titel: string, start: string, ende: string) => ({ event_id: titel, schluessel: titel, title: titel, start, end: ende, vorkommen: '', istSerie: false })

beforeEach(() => {
  useEinstellungenStore.setState({ schnellzugriffe: VORGABE_SCHNELLZUGRIFFE, widgetOrdnung: [], widgetsAus: ['zuletzt'] })
  useSitzung.setState({ stand: 'aus' })
  useToastStore.setState({ toasts: [] })
  useVerlaufStore.setState({ verlauf: [] })
  abrufeVergessen()
  Object.values(sync).forEach((f) => f.mockReset())
  sync.kalenderVorkommenLokal.mockReturnValue([])
  sync.getOfflineNotes.mockReturnValue([])
  sync.loadCalendarEventsOfflineFirst.mockResolvedValue({ events: [], isOffline: false })
  sync.loadNotesOfflineFirst.mockResolvedValue({ notes: [], isOffline: false })
})

describe('Adresse einer Kachel', () => {
  it('nimmt nur Webadressen ohne Zugangsdaten', () => {
    // Ohne Schema `http://`: HTTPS versucht der Browser beim Öffnen selbst (`tabs/https.rs`).
    expect(kachelAdresse('github.com')).toBe('http://github.com/')
    expect(kachelAdresse('localhost:3000')).toBe('http://localhost:3000/')
    expect(kachelAdresse('javascript:alert(1)')).toBeNull()
    expect(kachelAdresse('https://nutzer:geheim@example.com')).toBeNull()
    expect(kachelAdresse('zwei wörter')).toBeNull()
  })
})

describe('Kacheln aus früheren Fassungen', () => {
  it('übernimmt die Lesezeichen, die die Startseite bisher zeigte', async () => {
    localStorage.setItem('msb:einstellungen', JSON.stringify({ state: { suchmaschine: 'duckduckgo' }, version: 1 }))
    localStorage.setItem('msb:verlauf', JSON.stringify({ state: { lesezeichen: [{ url: 'https://codeberg.org/', titel: 'Codeberg', zeit: 1 }] }, version: 1 }))
    await useEinstellungenStore.persist.rehydrate()
    expect(useEinstellungenStore.getState().schnellzugriffe).toEqual([{ url: 'https://codeberg.org/', titel: 'Codeberg' }])
    localStorage.clear()
  })
})

describe('Schnellzugriffe', () => {
  it('ist ein Tab-Halt, die Pfeile wandern, Strg+Pfeil verschiebt', () => {
    zeigen(<Schnellzugriffe />)
    expect(kacheln().filter((k) => k.tabIndex === 0)).toHaveLength(1)
    kacheln()[0].focus()
    fireEvent.keyDown(kacheln()[0], { key: 'ArrowRight' })
    expect(document.activeElement).toBe(kacheln()[1])
    fireEvent.keyDown(kacheln()[1], { key: 'ArrowRight', ctrlKey: true })
    expect(urls()[2]).toBe('https://www.youtube.com/')
  })

  it('legt eine Kachel an und lehnt Unsinn ab', () => {
    zeigen(<Schnellzugriffe />)
    fireEvent.click(screen.getByRole('button', { name: 'Schnellzugriff hinzufügen' }))
    const dialog = screen.getByRole('dialog', { name: 'Schnellzugriff hinzufügen' })
    fireEvent.change(within(dialog).getByLabelText('Adresse'), { target: { value: 'javascript:alert(1)' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    expect(within(dialog).getByText('Das ist keine Webadresse.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Adresse'), { target: { value: 'codeberg.org' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    expect(useEinstellungenStore.getState().schnellzugriffe.at(-1)).toEqual({ url: 'http://codeberg.org/', titel: 'codeberg.org' })
  })

  it('entfernt per Menü und holt mit Rückgängig zurück', () => {
    zeigen(<Schnellzugriffe />)
    fireEvent.contextMenu(screen.getByRole('button', { name: 'GitHub' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Entfernen' }))
    expect(urls()).not.toContain('https://github.com/')
    const [meldung] = useToastStore.getState().toasts
    act(() => meldung.aktion!.ausfuehren())
    expect(urls()).toEqual(VORGABE_SCHNELLZUGRIFFE.map((s) => s.url))
  })
})

describe('Widgets', () => {
  it('zeigt ohne Kopplung keine Termine und Notizen, im privaten Tab nur die Uhr', () => {
    useEinstellungenStore.setState({ widgetsAus: [] })
    useVerlaufStore.setState({ verlauf: [{ url: 'https://example.org/', titel: 'Beispiel', zeit: 1 }] })
    const { unmount } = zeigen(<Widgets privat={false} />)
    expect(screen.getByRole('heading', { name: 'Uhr' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Zuletzt besucht' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nächste Termine' })).not.toBeInTheDocument()
    unmount()
    zeigen(<Widgets privat />)
    expect(screen.getAllByRole('heading').map((h) => h.textContent)).toEqual(['Uhr'])
  })

  it('folgt der Reihenfolge aus den Einstellungen', () => {
    useEinstellungenStore.setState({ widgetsAus: [], widgetOrdnung: ['zuletzt'] })
    zeigen(<Widgets privat={false} />)
    expect(screen.getAllByRole('heading').map((h) => h.textContent)).toEqual(['Zuletzt besucht', 'Uhr'])
  })

  it('liest Termine zuerst aus dem Spiegel und fragt das Panel höchstens alle fünf Minuten', async () => {
    useSitzung.setState({ stand: 'an' })
    usePermissionsStore.setState({ me: { is_owner: true, global_keys: [], server_keys: {} } as never })
    const morgen = new Date(Date.now() + 86_400_000)
    sync.kalenderVorkommenLokal.mockReturnValue([vorkommen('Aus dem Spiegel', morgen.toISOString(), morgen.toISOString())])
    sync.loadCalendarEventsOfflineFirst.mockResolvedValue({ events: [vorkommen('Vom Panel', morgen.toISOString(), morgen.toISOString())], isOffline: false })
    const erste = zeigen(<Widgets privat={false} />)
    expect(await screen.findByText('Vom Panel')).toBeInTheDocument()
    erste.unmount()
    zeigen(<Widgets privat={false} />)
    expect(await screen.findByText('Aus dem Spiegel')).toBeInTheDocument()
    await waitFor(() => expect(sync.kalenderVorkommenLokal).toHaveBeenCalledTimes(2))
    expect(sync.loadCalendarEventsOfflineFirst).toHaveBeenCalledTimes(1)
  })
})

describe('Notizen ohne Schlüssel', () => {
  it('zeigt eine Zeile, die zu den Notizen führt, statt vieler unlesbarer', async () => {
    // Nur ein Widget lädt: zwei gleichzeitige dynamische Importe desselben
    // gemockten Moduls geben in Vitest dem zweiten das echte Modul.
    useEinstellungenStore.setState({ widgetsAus: ['termine', 'zuletzt'] })
    useSitzung.setState({ stand: 'offline' })
    usePermissionsStore.setState({ me: { is_owner: true, global_keys: [], server_keys: {} } as never })
    const zu = (id: number) => ({ id, note_uid: `n${id}`, title: 'sv-note-v1:a', content: 'sv-note-v1:b', updated_at: '2026-10-01T00:00:00Z', is_archived: false })
    sync.getOfflineNotes.mockReturnValue([zu(1), zu(2)])
    zeigen(<Widgets privat={false} />)
    const notizen = (await screen.findByRole('heading', { name: 'Letzte Notizen' })).closest('section')!
    expect(await within(notizen).findAllByRole('button')).toHaveLength(1)
    expect(within(notizen).getByRole('button')).toHaveTextContent('Auf diesem Gerät noch nicht lesbarNotizen öffnen')
  })
})

describe('Was die Widgets aus den Daten nehmen', () => {
  it('lässt vergangene Termine weg und zeigt verschlüsselte Titel nicht', () => {
    const jetzt = Date.parse('2026-10-08T12:00:00Z')
    const liste = naechsteTermine(
      [vorkommen('vorbei', '2026-10-08T08:00:00Z', '2026-10-08T09:00:00Z') as never, vorkommen('sv-cal-v1:abc', '2026-10-08T13:00:00Z', '2026-10-08T14:00:00Z') as never],
      jetzt,
      'sv-cal-v1:',
    )
    expect(liste).toHaveLength(1)
    expect(liste[0].titel).toBeNull()
  })

  it('nimmt die zuletzt geänderten Notizen ohne Archiv, notfalls die erste Zeile', () => {
    const notiz = (id: number, titel: string, inhalt: string, geaendert: string, archiviert = false) =>
      ({ id, note_uid: `n${id}`, title: titel, content: inhalt, updated_at: geaendert, is_archived: archiviert }) as never
    const liste = letzteNotizen(
      [notiz(1, 'Alt', '', '2026-10-01T00:00:00Z'), notiz(2, '', '\nEinkauf\nMilch', '2026-10-07T00:00:00Z'), notiz(3, 'Archiv', '', '2026-10-08T00:00:00Z', true), notiz(4, 'sv-note-v1:x', 'sv-note-v1:y', '2026-10-06T00:00:00Z')],
      'sv-note-v1:',
    )
    expect(liste.map((n) => n.titel)).toEqual(['Einkauf', null, 'Alt'])
  })
})
