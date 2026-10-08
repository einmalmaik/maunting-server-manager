import { createEvent, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SchutzRegeln, SchutzStand } from '../services/nativ'

const { gerufen, rust } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: Record<string, unknown> }[],
  rust: { stand: null as unknown, fehler: null as string | null },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
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

const regeln = (teil: Partial<SchutzRegeln> = {}): SchutzRegeln => ({
  aktiv: true,
  kategorien: ['gluecksspiel'],
  eigene: [],
  ausnahmen: [],
  huerde: { art: 'countdown', minuten: 15 },
  ...teil,
})

function stand(teil: Partial<SchutzStand> = {}): SchutzStand {
  return { regeln: regeln(), antrag: null, listen: [], ...teil }
}

const aufgerufen = (befehl: string) => gerufen.filter((g) => g.befehl === befehl)

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  gerufen.length = 0
  rust.stand = stand()
  rust.fehler = null
})

describe('Jugend- und Suchtschutz in den Einstellungen', () => {
  it('schickt eine neue Kategorie an Rust', async () => {
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Shopping' }))
    await waitFor(() => expect(aufgerufen('schutz_aendern')).toHaveLength(1))
    expect(aufgerufen('schutz_aendern')[0].args.regeln).toMatchObject({ kategorien: ['gluecksspiel', 'shopping'] })
  })

  it('zeigt eine wartende Lockerung mit Countdown und nimmt sie zurück', async () => {
    rust.stand = stand({ antrag: { ziel: regeln({ kategorien: [] }), rest_sekunden: 899, text: null } })
    render(<Jugendschutz />)
    const karte = (await screen.findByRole('heading', { name: 'Lockerung wartet' })).closest('section')!
    expect(within(karte).getByText('Glücksspiel nicht mehr sperren')).toBeInTheDocument()
    expect(within(karte).getByText('14:59')).toBeInTheDocument()
    // Der Schalter zeigt den Wunsch, nicht den geltenden Stand.
    expect(screen.getByRole('switch', { name: 'Glücksspiel' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(within(karte).getByRole('button', { name: 'Schutz behalten' }))
    await waitFor(() => expect(aufgerufen('schutz_abbrechen')).toHaveLength(1))
  })

  it('geht bei jeder Änderung vom Wunsch aus, damit eine wartende Lockerung bleibt', async () => {
    rust.stand = stand({ antrag: { ziel: regeln({ kategorien: [] }), rest_sekunden: 600, text: null } })
    render(<Jugendschutz />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Spiele' }))
    await waitFor(() => expect(aufgerufen('schutz_aendern')).toHaveLength(1))
    expect(aufgerufen('schutz_aendern')[0].args.regeln).toMatchObject({ kategorien: ['spiele'] })
  })

  it('lässt beim Abtippen nichts einfügen und schickt erst den richtigen Text', async () => {
    const text = 'tamo keli rusa'
    rust.stand = stand({
      regeln: regeln({ huerde: { art: 'abtippen' } }),
      antrag: { ziel: regeln({ aktiv: false, huerde: { art: 'abtippen' } }), rest_sekunden: 30, text },
    })
    render(<Jugendschutz />)
    expect(await screen.findByText('Schutz ausschalten')).toBeInTheDocument()
    const feld = screen.getByRole('textbox', { name: 'Abgetippter Text' })
    const einfuegen = createEvent.paste(feld)
    fireEvent(feld, einfuegen)
    expect(einfuegen.defaultPrevented).toBe(true)

    fireEvent.change(feld, { target: { value: 'tamo keli' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lockern' }))
    expect(await screen.findByText('Der Text stimmt noch nicht.')).toBeInTheDocument()
    expect(aufgerufen('schutz_bestaetigen')).toHaveLength(0)

    rust.fehler = 'zu_schnell'
    fireEvent.change(feld, { target: { value: 'tamo  keli rusa' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lockern' }))
    expect(await screen.findByText(/So schnell tippt niemand/)).toBeInTheDocument()
    expect(aufgerufen('schutz_bestaetigen')[0].args).toEqual({ text: 'tamo  keli rusa' })
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
