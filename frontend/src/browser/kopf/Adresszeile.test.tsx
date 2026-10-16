import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({
  invoke: () => Promise.resolve(null),
}))

const { useTabsStore } = await import('../services/tabsStore')
const { useEinstellungenStore } = await import('../services/einstellungenStore')
const { Adresszeile } = await import('./Adresszeile')

const warten = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

describe('Adresszeile Suchwahl-Dropdown', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useEinstellungenStore.setState({
      suchmaschine: null,
    })
    useTabsStore.setState({
      tabs: [
        {
          id: 'tab-1',
          url: 'https://example.com/artikel',
          titel: 'Example Artikel',
          favicon: null,
          privat: false,
          laedt: false,
          ton: false,
          stumm: false,
          ruhe: null,
          nativDa: true,
          elternId: null,
          baumEingeklappt: false,
          zurueck: false,
          vor: false,
          werbung: 0,
          tracker: 0,
          abgestuerzt: false,
          fehler: null,
          gesperrt: null,
          status: '',
          treffer: null,
          vorher: null,
        },
      ],
      aktivId: 'tab-1',
    })
  })

  it('zeigt den Suchwahl-Knopf auch auf Webseiten an und ermöglicht das Umschalten der Suchmaschine', async () => {
    render(<Adresszeile />)

    // Auf einer Webseite: Suchwahl-Knopf und Schloss vorhanden
    const suchKnopf = screen.getByRole('button', { name: /Suchmaschine/i })
    expect(suchKnopf).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Verbindung verschlüsselt/i })).toBeInTheDocument()

    // Klick auf den Suchwahl-Knopf öffnet das Dropdown
    fireEvent.click(suchKnopf)
    await warten()

    // Liste mit Suchmaschinen ist offen
    const optionen = screen.getAllByRole('option')
    expect(optionen.length).toBeGreaterThan(0)

    // Auswahl von Brave
    const braveOpt = optionen.find((o) => o.textContent?.includes('Brave'))
    expect(braveOpt).toBeDefined()
    fireEvent.click(braveOpt!)
    await warten()

    // Suchmaschine im Store geändert
    expect(useEinstellungenStore.getState().suchmaschine).toBe('brave')
  })
})
