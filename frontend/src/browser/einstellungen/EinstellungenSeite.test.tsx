import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen } = vi.hoisted(() => ({ gerufen: [] as { befehl: string; args: unknown }[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: unknown) => {
    gerufen.push({ befehl, args })
    if (befehl === 'seitenrechte') return Promise.resolve([{ art: 'kamera', herkunft: 'https://meet.example', erlaubt: true }])
    return Promise.resolve(null)
  },
}))

const { EinstellungenSeite } = await import('./EinstellungenSeite')
const { ConfirmDialog } = await import('@/components/ui/ConfirmDialog')
const { useTabsStore } = await import('../services/tabsStore')
const { useVerlaufStore } = await import('../services/verlaufStore')

function Seite() {
  const url = useTabsStore((s) => s.tabs.find((t) => t.id === s.aktivId)?.url ?? '')
  return <EinstellungenSeite teil={url.split('/')[3] ?? null} />
}

const zeigen = () =>
  render(
    <MemoryRouter>
      <Seite />
      <ConfirmDialog />
    </MemoryRouter>,
  )

describe('Einstellungsseite', () => {
  beforeEach(() => {
    gerufen.length = 0
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useTabsStore.setState({ tabs: [{ ...useTabsStore.getState().tabs[0], id: 'tab-e', url: '' }], aktivId: 'tab-e' })
    useTabsStore.getState().einstellungen()
  })

  it('wechselt die Kategorie über die Adresse des Tabs', () => {
    zeigen()
    const liste = screen.getByRole('navigation', { name: 'Einstellungen' })
    expect(within(liste).getByRole('button', { name: 'Allgemein' })).toHaveAttribute('aria-current', 'page')
    fireEvent.click(within(liste).getByRole('button', { name: 'Verlauf und Daten' }))
    expect(useTabsStore.getState().tabs[0].url).toBe('msb://einstellungen/verlauf')
    expect(screen.getByText('Verlauf automatisch löschen')).toBeInTheDocument()
  })

  it('löscht Browserdaten nur für den gewählten Zeitraum, erst nach der Rückfrage', async () => {
    const jetzt = Date.now()
    useVerlaufStore.setState({
      verlauf: [
        { url: 'https://neu.example/', titel: 'Neu', zeit: jetzt - 60_000 },
        { url: 'https://alt.example/', titel: 'Alt', zeit: jetzt - 3 * 60 * 60 * 1000 },
      ],
    })
    useTabsStore.getState().einstellungen('verlauf')
    zeigen()
    fireEvent.click(screen.getByRole('button', { name: 'Löschen' }))
    expect(gerufen.some((g) => g.befehl === 'seitendaten_loeschen')).toBe(false)
    const rueckfrage = await screen.findByRole('dialog')
    fireEvent.click(within(rueckfrage).getByRole('button', { name: 'Löschen' }))
    await waitFor(() => expect(gerufen.some((g) => g.befehl === 'seitendaten_loeschen')).toBe(true))
    const { seit } = gerufen.find((g) => g.befehl === 'seitendaten_loeschen')!.args as { seit: number }
    expect(jetzt - seit).toBeGreaterThan(59 * 60 * 1000)
    expect(jetzt - seit).toBeLessThan(61 * 60 * 1000)
    expect(useVerlaufStore.getState().verlauf.map((e) => e.titel)).toEqual(['Alt'])
  })

  it('zeigt gespeicherte Website-Rechte und setzt eines zurück', async () => {
    useTabsStore.getState().einstellungen('schutz')
    zeigen()
    const knopf = await screen.findByRole('button', { name: 'Kamera für https://meet.example zurücksetzen' })
    fireEvent.click(knopf)
    await waitFor(() => expect(screen.getByText(/Noch keine Seite/)).toBeInTheDocument())
    expect(gerufen).toContainEqual({ befehl: 'seitenrecht_zuruecksetzen', args: { art: 'kamera', herkunft: 'https://meet.example' } })
  })

  it('öffnet die Datenschutzerklärung und führt zurück zu „Über“', async () => {
    useTabsStore.getState().einstellungen('ueber')
    zeigen()
    fireEvent.click(screen.getByRole('button', { name: 'Öffnen' }))
    expect(useTabsStore.getState().tabs[0].url).toBe('msb://einstellungen/datenschutzerklaerung')
    const zurueck = await screen.findByRole('link', { name: /Zurück/ })
    await act(async () => {
      fireEvent.click(zurueck)
    })
    expect(useTabsStore.getState().tabs[0].url).toBe('msb://einstellungen/ueber')
  })
})
