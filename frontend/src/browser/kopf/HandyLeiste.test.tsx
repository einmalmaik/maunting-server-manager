/**
 * Die Oberfläche am Handy: Leiste unten, Tab-Übersicht, Panel über dem ganzen
 * Bildschirm, und was Android nicht kann, steht nicht da.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { HandyLeiste } = await import('./HandyLeiste')
const { Panel } = await import('../leiste/Panel')
const { EinstellungenSeite } = await import('../einstellungen/EinstellungenSeite')
const { useTabsStore } = await import('../services/tabsStore')
const { useEinstellungenStore } = await import('../services/einstellungenStore')
const { useSitzung } = await import('../services/sitzung')
const { DownloadsPanel } = await import('../panels/Downloads')
const { useDownloadsStore } = await import('../services/downloadsStore')
const { nativ } = await import('../services/nativ')

const UA = navigator.userAgent
const alsAndroid = (an: boolean) =>
  Object.defineProperty(navigator, 'userAgent', {
    value: an ? 'Mozilla/5.0 (Linux; Android 15; Pixel) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36' : UA,
    configurable: true,
  })

function Ort() {
  return <span data-testid="ort">{useLocation().pathname}</span>
}

const zeigen = (inhalt: React.ReactNode, start = '/') =>
  render(
    <MemoryRouter initialEntries={[start]}>
      {inhalt}
      <Ort />
    </MemoryRouter>,
  )

describe('Am Handy', () => {
  beforeEach(() => {
    alsAndroid(true)
    useSitzung.setState({ stand: 'aus' })
    useEinstellungenStore.setState({ leiste: 'menue', ausgeblendet: [] })
    useTabsStore.setState({ tabs: [], aktivId: null })
    const { neuerTab } = useTabsStore.getState()
    neuerTab('https://eins.example/', { hintergrund: true })
    neuerTab('https://zwei.example/', { hintergrund: true })
    useTabsStore.getState().aktivieren(useTabsStore.getState().tabs[0].id)
    useTabsStore.setState((s) => ({ tabs: s.tabs.map((t) => ({ ...t, laedt: false })) }))
  })
  afterEach(() => alsAndroid(false))

  it('hat unten Tabs und Menü; Vor, Lesezeichen, Suchen und Neu laden stehen im Menü', () => {
    zeigen(<HandyLeiste />)
    expect(screen.getByRole('button', { name: 'Alle Tabs (2)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Vor' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Menü' }))
    const menue = screen.getByRole('dialog', { name: 'Menü' })
    for (const name of ['Vor', 'Lesezeichen setzen (Strg+D)', 'In der Seite suchen', 'Neu laden']) {
      expect(within(menue).getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('zeigt alle Tabs als Raster mit einem Tab-Halt, öffnet und schließt daraus', () => {
    zeigen(<HandyLeiste />)
    fireEvent.click(screen.getByRole('button', { name: 'Alle Tabs (2)' }))
    const uebersicht = screen.getByRole('dialog', { name: 'Tabs' })
    const kacheln = uebersicht.querySelectorAll<HTMLElement>('[data-kachel]')
    expect(kacheln).toHaveLength(2)
    expect([...kacheln].map((k) => k.tabIndex)).toEqual([0, -1])

    fireEvent.keyDown(kacheln[0], { key: 'Delete' })
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual(['https://zwei.example/'])

    fireEvent.click(within(uebersicht).getByRole('button', { name: 'Neuer privater Tab' }))
    expect(screen.queryByRole('dialog', { name: 'Tabs' })).not.toBeInTheDocument()
    const tabs = useTabsStore.getState().tabs
    expect(tabs).toHaveLength(2)
    expect(useTabsStore.getState().aktivId).toBe(tabs[1].id)
    expect(tabs[1].privat).toBe(true)
  })

  it('legt das Panel als Dialog über den ganzen Bildschirm, ohne Griff zum Ziehen', () => {
    zeigen(<Panel seite="voll" />, '/verlauf')
    const panel = screen.getByRole('dialog', { name: 'Verlauf' })
    expect(panel).toHaveAttribute('aria-modal', 'true')
    expect(screen.queryByRole('separator')).not.toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Panel schließen' }))
    expect(screen.getByTestId('ort')).toHaveTextContent('/')
  })

  it('bietet in den Einstellungen nicht an, was Android nicht kann', () => {
    const { unmount } = zeigen(<EinstellungenSeite teil="verlauf" />)
    expect(screen.queryByRole('tab', { name: 'Leistung' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Leistung' })).not.toBeInTheDocument()
    expect(screen.getByText(/Android löscht Cookies/)).toBeInTheDocument()
    unmount()
    alsAndroid(false)
    zeigen(<EinstellungenSeite teil="verlauf" />)
    expect(screen.getAllByText('Leistung').length).toBeGreaterThan(0)
  })

  it('verspricht keinen eingeschränkten Modus von YouTube und kein Windows Hello', async () => {
    const regeln = { aktiv: false, kategorien: [], eigene: [], ausnahmen: [], huerde: { art: 'countdown' as const, minuten: 15 } }
    vi.spyOn(nativ, 'schutzStand').mockResolvedValue({ regeln, antrag: null, listen: [] })
    const { unmount } = zeigen(<EinstellungenSeite teil="jugendschutz" />)
    expect(await screen.findByText(/Den eingeschränkten Modus von YouTube kann der Browser unter Android nicht einschalten/)).toBeInTheDocument()
    expect(screen.queryByText(/und der eingeschränkte Modus von YouTube/)).not.toBeInTheDocument()
    unmount()
    zeigen(<EinstellungenSeite teil="passwoerter" />)
    expect(screen.getByText(/nach der Bildschirmsperre/)).toBeInTheDocument()
    expect(screen.queryByText(/Windows Hello/)).not.toBeInTheDocument()
  })

  it('legt Downloads beim System ab: kein Ordner, keine Rückfrage, die Liste öffnet die Downloads', () => {
    const { unmount } = zeigen(<EinstellungenSeite teil="downloads" />)
    expect(screen.getByText(/Ordner „Download“ des Telefons/)).toBeInTheDocument()
    expect(screen.queryByText('Bei jedem Download fragen')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ordner wählen' })).not.toBeInTheDocument()
    unmount()

    useDownloadsStore.setState({
      downloads: [{ nr: 1, url: 'https://eins.example/bericht.pdf', datei: 'bericht.pdf', stand: 'fertig', zeit: 0 }],
    })
    zeigen(<DownloadsPanel />)
    expect(screen.getByRole('button', { name: 'bericht.pdf in den Downloads zeigen' })).toBeInTheDocument()
  })
})
