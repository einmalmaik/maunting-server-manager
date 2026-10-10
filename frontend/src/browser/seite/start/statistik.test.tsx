import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({
  gesamt: { werbung: 18420, tracker: 6311, cookies: 214, seiten: 3000, seit: 1_760_000_000 } as Record<string, number>,
  symbole: {} as Record<string, string | null>,
  gerufen: [] as [string, unknown][],
}))
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    nativ.gerufen.push([befehl, args])
    if (befehl === 'schild_gesamt') return Promise.resolve(nativ.gesamt)
    if (befehl === 'kachel_symbol') return Promise.resolve(nativ.symbole[args.url as string] ?? null)
    return Promise.resolve(null)
  },
}))
vi.mock('@/lib/offlineSync', () => ({
  kalenderVorkommenLokal: () => [],
  loadCalendarEventsOfflineFirst: () => Promise.resolve({ events: [], isOffline: false }),
  getOfflineNotes: () => [],
  loadNotesOfflineFirst: () => Promise.resolve({ notes: [], isOffline: false }),
}))
vi.mock('@/services/notesCalendarCrypto', () => ({ NOTE_CIPHERTEXT_PREFIX: 'sv-note-v1:', CALENDAR_CIPHERTEXT_PREFIX: 'sv-cal-v1:' }))

const { Startseite } = await import('../Startseite')
const { Schnellzugriffe } = await import('./Schnellzugriffe')
const { datenText, geschaetzt, zeitText } = await import('./schaetzung')
const { useEinstellungenStore } = await import('../../services/einstellungenStore')

const zeigen = (inhalt: React.ReactNode) => render(<MemoryRouter>{inhalt}</MemoryRouter>)

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  nativ.gerufen.length = 0
  nativ.symbole = {}
  useEinstellungenStore.setState({
    statistikZeigen: true,
    schnellzugriffe: [
      { url: 'https://de.wikipedia.org/', titel: 'Wikipedia' },
      { url: 'https://codeberg.org/', titel: 'Codeberg' },
    ],
  })
  // Ohne Bewegung stehen die Zahlen gleich da.
  window.matchMedia = ((q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} })) as never
})
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
})

describe('Statistik auf der Startseite', () => {
  it('zeigt die Zähler aus Rust, das Datum und die Schätzung samt Quelle', async () => {
    zeigen(<Startseite privat={false} />)
    const statistik = await screen.findByRole('region', { name: 'Was das Schild für dich geblockt hat' })
    expect(await within(statistik).findByText('18.420')).toBeInTheDocument()
    expect(within(statistik).getByText('6.311')).toBeInTheDocument()
    expect(within(statistik).getByText('214')).toBeInTheDocument()
    // 3000 Seiten mit Blockierungen: 1,5 GB und 25 Minuten.
    // Intl setzt ein geschütztes Leerzeichen, die Suche sieht ein normales.
    const text = (s: string) => `≈ ${s}`.replace(/\s/g, ' ')
    expect(within(statistik).getByText(text(datenText(1.5e9, 'de')))).toBeInTheDocument()
    expect(within(statistik).getByText(text(zeitText(1500, 'de')))).toBeInTheDocument()
    expect(within(statistik).getByText(/^Seit dem 9\. Oktober 2025/)).toBeInTheDocument()
    expect(within(statistik).getByRole('button', { name: 'Zur Messung' })).toBeInTheDocument()
  })

  it('fehlt im privaten Tab und wenn sie ausgeschaltet ist', () => {
    const { unmount } = zeigen(<Startseite privat />)
    expect(screen.queryByRole('region', { name: 'Was das Schild für dich geblockt hat' })).not.toBeInTheDocument()
    expect(nativ.gerufen.some(([b]) => b === 'schild_gesamt')).toBe(false)
    unmount()
    useEinstellungenStore.setState({ statistikZeigen: false })
    zeigen(<Startseite privat={false} />)
    expect(screen.queryByRole('region', { name: 'Was das Schild für dich geblockt hat' })).not.toBeInTheDocument()
  })

  it('schätzt je Seite mit Blockierungen 500 KB und eine halbe Sekunde', () => {
    expect(geschaetzt({ seiten: 4 })).toEqual({ bytes: 2_000_000, sekunden: 2 })
    expect(datenText(480_000, 'de')).toMatch(/^480\s?kB$/)
    expect(datenText(2_500_000, 'en')).toMatch(/^2\.5\s?MB$/)
    expect(zeitText(30, 'en')).toMatch(/^30\s?sec/)
    expect(zeitText(3 * 86_400, 'de')).toMatch(/^3\s?Tg?/)
  })
})

describe('Symbole der Kacheln', () => {
  it('zeigt das Symbol, das Rust liefert, und sonst den Anfangsbuchstaben', async () => {
    // Eigene Adressen: je Sitzung fragt die Oberfläche einmal je Adresse.
    useEinstellungenStore.setState({
      schnellzugriffe: [
        { url: 'https://de.wikipedia.org/wiki/Hauptseite', titel: 'Wikipedia' },
        { url: 'https://codeberg.org/explore', titel: 'Codeberg' },
      ],
    })
    nativ.symbole['https://de.wikipedia.org/wiki/Hauptseite'] = 'data:image/png;base64,iVBORw0KGgo='
    zeigen(<Schnellzugriffe />)
    const wiki = screen.getByRole('button', { name: 'Wikipedia' })
    await vi.waitFor(() => expect(wiki.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo='))
    const codeberg = screen.getByRole('button', { name: 'Codeberg' })
    expect(codeberg.querySelector('img')).toBeNull()
    expect(codeberg.querySelector('span[aria-hidden="true"]')?.textContent).toBe('c')
    // Nur die Adresse der Kachel geht an Rust; geladen wird in der Oberfläche nichts.
    expect(nativ.gerufen.filter(([b]) => b === 'kachel_symbol').map(([, a]) => (a as { url: string }).url)).toEqual(
      expect.arrayContaining(['https://de.wikipedia.org/wiki/Hauptseite', 'https://codeberg.org/explore']),
    )
  })

  it('lässt die Symbole entfernter Kacheln löschen', async () => {
    zeigen(<Schnellzugriffe />)
    await vi.waitFor(() =>
      expect(nativ.gerufen).toContainEqual(['kachel_symbole_behalten', { adressen: ['https://de.wikipedia.org/', 'https://codeberg.org/'] }]),
    )
    useEinstellungenStore.setState({ schnellzugriffe: [{ url: 'https://codeberg.org/', titel: 'Codeberg' }] })
    await vi.waitFor(() => expect(nativ.gerufen).toContainEqual(['kachel_symbole_behalten', { adressen: ['https://codeberg.org/'] }]))
  })
})
