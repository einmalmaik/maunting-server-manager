import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SchutzRegeln, SchutzStand } from '../services/nativ'

const { gerufen, rust } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: Record<string, unknown> }[],
  rust: { stand: null as unknown, fehler: null as string | null, halten: false, offen: [] as (() => void)[] },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    if (befehl === 'schutz_stand') return Promise.resolve(rust.stand)
    if (befehl === 'schutz_aendern' && rust.halten) {
      // Rust nach `schutz.rs`, verkürzt: fällt eine Kategorie weg, wird es ein
      // Antrag, sonst gilt es sofort. Die Antwort kommt erst, wenn der Test sie freigibt.
      const s = rust.stand as SchutzStand
      const neu = args.regeln as SchutzRegeln
      const weg = s.regeln.kategorien.filter((k) => !neu.kategorien.includes(k))
      const dazu = neu.kategorien.filter((k) => !s.regeln.kategorien.includes(k))
      rust.stand = weg.length
        ? { ...s, regeln: { ...s.regeln, kategorien: [...s.regeln.kategorien, ...dazu] }, antrag: { ziel: neu, rest_sekunden: 86400, fenster_sekunden: 3600 } }
        : { ...s, regeln: neu, antrag: null }
      const antwort = rust.stand
      return new Promise((r) => rust.offen.push(() => r(antwort)))
    }
    if (befehl.startsWith('schutz_')) {
      if (rust.fehler) return Promise.reject(rust.fehler)
      return Promise.resolve(rust.stand)
    }
    return Promise.resolve(null)
  },
}))
vi.mock('@tauri-apps/api/event', () => ({ listen: () => Promise.resolve(() => {}) }))

const { Jugendschutz } = await import('./Jugendschutz')
const { useTabsStore } = await import('../services/tabsStore')
const { Sperrseite } = await import('../seite/Sperrseite')
const { ConfirmDialog } = await import('@/components/ui/ConfirmDialog')
const { useToastStore } = await import('@/stores/toastStore')

const regeln = (teil: Partial<SchutzRegeln> = {}): SchutzRegeln => ({
  aktiv: true,
  kategorien: ['gluecksspiel'],
  eigene: [],
  ausnahmen: [],
  wartezeit_stunden: 24,
  ...teil,
})

function stand(teil: Partial<SchutzStand> = {}): SchutzStand {
  return {
    regeln: regeln(),
    antrag: null,
    gebunden_sekunden: 0,
    ungebunden: false,
    abkuehlen_sekunden: 0,
    serie: { tage: 0, rekord: 0 },
    beschaedigt: false,
    listen: [],
    ...teil,
  }
}

const aufgerufen = (befehl: string) => gerufen.filter((g) => g.befehl === befehl)
const toasts = () => useToastStore.getState().toasts.map((t) => t.message)

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  gerufen.length = 0
  rust.stand = stand()
  rust.fehler = null
  rust.halten = false
  rust.offen = []
  useToastStore.setState({ toasts: [] })
})

describe('Jugend- und Suchtschutz in den Einstellungen', () => {
  it('schickt eine neue Kategorie an Rust', async () => {
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Shopping' }))
    await waitFor(() => expect(aufgerufen('schutz_aendern')).toHaveLength(1))
    expect(aufgerufen('schutz_aendern')[0].args.regeln).toMatchObject({ kategorien: ['gluecksspiel', 'shopping'] })
  })

  it('zeigt eine wartende Lockerung mit Countdown, ohne Knopf zum Lockern, und nimmt sie zurück', async () => {
    rust.stand = stand({ antrag: { ziel: regeln({ kategorien: [] }), rest_sekunden: 90061, fenster_sekunden: 3600 } })
    render(<Jugendschutz />)
    const karte = (await screen.findByRole('heading', { name: 'Lockerung beantragt' })).closest('section')!
    expect(within(karte).getByText('Glücksspiel nicht mehr sperren')).toBeInTheDocument()
    expect(within(karte).getByText('1 Tag, 01:01:01')).toBeInTheDocument()
    expect(within(karte).queryByRole('button', { name: 'Lockern' })).toBeNull()
    // Der Schalter zeigt den Wunsch, nicht den geltenden Stand.
    expect(screen.getByRole('switch', { name: 'Glücksspiel' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(within(karte).getByRole('button', { name: 'Schutz behalten' }))
    await waitFor(() => expect(aufgerufen('schutz_abbrechen')).toHaveLength(1))
  })

  it('lässt im Fenster bestätigen und sagt, wenn es verfallen ist', async () => {
    rust.stand = stand({ antrag: { ziel: regeln({ aktiv: false }), rest_sekunden: 0, fenster_sekunden: 1800 } })
    render(<Jugendschutz />)
    expect(await screen.findByText('30:00')).toBeInTheDocument()
    rust.fehler = 'verfallen'
    fireEvent.click(screen.getByRole('button', { name: 'Lockern' }))
    await waitFor(() => expect(aufgerufen('schutz_bestaetigen')).toEqual([{ befehl: 'schutz_bestaetigen', args: undefined }]))
    await waitFor(() => expect(toasts()).toContain('Die Stunde zum Bestätigen ist vorbei, der Antrag ist verfallen.'))
  })

  it('geht bei jeder Änderung vom Wunsch aus, damit eine wartende Lockerung bleibt', async () => {
    rust.stand = stand({ antrag: { ziel: regeln({ kategorien: [] }), rest_sekunden: 600, fenster_sekunden: 3600 } })
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Spiele' }))
    await waitFor(() => expect(aufgerufen('schutz_aendern')).toHaveLength(1))
    expect(aufgerufen('schutz_aendern')[0].args.regeln).toMatchObject({ kategorien: ['spiele'] })
  })

  // Bugjagd 08.10.2026: der zweite Klick rechnete mit dem Stand vor dem ersten.
  it('macht aus zwei schnellen Klicks zum Verschärfen keine Lockerung', async () => {
    rust.halten = true
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Shopping' }))
    fireEvent.click(screen.getByRole('switch', { name: 'Spiele' }))
    // Rust antwortet der Reihe nach, wie es die Aufrufe bekommt.
    for (let n = 1; n <= 2; n++) {
      await waitFor(() => expect(rust.offen).toHaveLength(n))
      rust.offen[n - 1]()
    }
    const zweite = aufgerufen('schutz_aendern')[1].args.regeln as SchutzRegeln
    expect(zweite.kategorien).toEqual(expect.arrayContaining(['gluecksspiel', 'shopping', 'spiele']))
    expect((rust.stand as SchutzStand).antrag).toBeNull()
  })

  it('sagt, warum sich nichts lockern lässt', async () => {
    render(<Jugendschutz />)
    for (const [fehler, satz] of [
      ['gebunden', 'Du hast dich gebunden. Bis die Bindung endet, lässt sich nichts lockern.'],
      ['ohne_netz', 'Ohne Internet lässt sich nichts lockern. Die Wartezeit misst der Browser an der Uhrzeit aus dem Netz, nicht an der des Geräts.'],
      ['abkuehlen', 'Nach einem zurückgenommenen oder verfallenen Antrag geht ein neuer erst nach einem Tag.'],
    ]) {
      rust.fehler = fehler
      fireEvent.click(await screen.findByRole('switch', { name: 'Glücksspiel' }))
      await waitFor(() => expect(toasts()).toContain(satz))
    }
  })

  it('bindet nach Rückfrage und zeigt Bindung und Serie', async () => {
    rust.stand = stand({ gebunden_sekunden: 2 * 86400, serie: { tage: 3, rekord: 12 } })
    render(
      <>
        <Jugendschutz />
        <ConfirmDialog />
      </>,
    )
    expect(await screen.findByText('3 Tage ohne gesperrte Seite.')).toBeInTheDocument()
    expect(screen.getByText('Rekord: 12 Tage')).toBeInTheDocument()
    expect(screen.getByText('Gebunden noch 2 Tage, 00:00:00. So lange lässt sich nichts lockern.')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Verlängern' })[0])
    const frage = await screen.findByRole('dialog')
    expect(within(frage).getByText('Für 7 Tage binden?')).toBeInTheDocument()
    expect(aufgerufen('schutz_binden')).toHaveLength(0)
    fireEvent.click(within(frage).getByRole('button', { name: 'Verlängern' }))
    await waitFor(() => expect(aufgerufen('schutz_binden')[0]?.args).toEqual({ tage: 7 }))
  })

  it('sagt ohne Bindung, dass Ausschalten sofort gilt, und nach einer Bindung, dass es ein Antrag ist', async () => {
    rust.stand = stand({ ungebunden: true })
    const { unmount } = render(<Jugendschutz />)
    expect(await screen.findByText(/^Nicht gebunden: Lockern und Ausschalten gelten sofort\./)).toBeInTheDocument()
    unmount()
    rust.stand = stand({ ungebunden: false })
    render(<Jugendschutz />)
    expect(await screen.findByText(/^Nicht gebunden\. Lockern geht per Antrag\./)).toBeInTheDocument()
  })

  it('bietet keine Hürde zum Abtippen mehr an, nur Wartezeiten', async () => {
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('button', { name: 'Wartezeit vor dem Lockern' }))
    const namen = screen.getAllByRole('option').map((o) => o.textContent)
    expect(namen).toEqual(['24 Stunden', '3 Tage', '7 Tage'])
  })

  it('sagt, wenn die Schutzdatei kaputt war', async () => {
    rust.stand = stand({ beschaedigt: true })
    render(<Jugendschutz />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Die Einstellungsdatei des Schutzes war nicht zu lesen.')
  })
})

describe('Sperrseite', () => {
  it('nennt Kategorie und Seite und führt zurück', async () => {
    const vorlage = useTabsStore.getState().tabs[0]
    useTabsStore.setState({ tabs: [{ ...vorlage, id: 'tab-a', url: 'https://alt.example/', nativDa: true, zurueck: true }], aktivId: 'tab-a' })
    useTabsStore.getState().ereignis({ art: 'gesperrt', id: 'tab-a', url: 'https://casino.example/spiel', grund: 'gluecksspiel' })
    await waitFor(() => expect(aufgerufen('tab_aktivieren').at(-1)?.args).toEqual({ id: null }))
    const tab = useTabsStore.getState().tabs[0]
    expect(tab).toMatchObject({ fehler: 'gesperrt', gesperrt: 'gluecksspiel', url: 'https://casino.example/spiel' })
    render(<Sperrseite tab={tab} />)
    expect(screen.getByText('Diese Seite ist gesperrt')).toBeInTheDocument()
    expect(screen.getByText('casino.example gehört zu „Glücksspiel“. So ist es im Jugend- und Suchtschutz eingestellt.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Zurück' }))
    await waitFor(() => expect(aufgerufen('tab_aktion').at(-1)?.args).toEqual({ id: 'tab-a', aktion: 'zurueck' }))
  })
})
