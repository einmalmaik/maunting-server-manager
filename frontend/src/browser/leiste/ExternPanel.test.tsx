import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({
  tabLaden: vi.fn(async () => null),
  panelRahmen: vi.fn(async () => null),
  tabSchliessen: vi.fn(async () => null),
  tabAktivieren: vi.fn(async () => null),
}))
const listener = vi.hoisted(() => ({ callback: null as null | ((e: any) => void) }))
const store = vi.hoisted(() => ({ aktivId: 'tab-main', neuerTab: vi.fn() }))
const registrieren = vi.hoisted(() => vi.fn())
const standbild = vi.hoisted(() => ({ bilder: {} as Record<string, string> }))

vi.mock('../services/nativ', () => ({
  nativ,
  SCHUTZ_NICHT_BEREIT: 'schutz',
  tabEreignisse: vi.fn(async (cb: (e: any) => void) => {
    listener.callback = cb
    return () => {}
  }),
}))
vi.mock('../services/tabsStore', () => ({ useTabsStore: { getState: () => store } }))
vi.mock('../services/ueberdeckung', () => ({
  panelRegistrieren: registrieren,
  useStandbild: (selector: (s: { bilder: Record<string, string> }) => unknown) => selector({ bilder: standbild.bilder }),
}))
vi.mock('../seite/SeitenDialoge', () => ({ SeitenDialoge: () => null }))
vi.mock('../seite/SeitenMenue', () => ({ SeitenMenue: () => null }))
vi.mock('../seite/Sperrseite', () => ({ Sperrseite: () => <div>Sperrseite</div> }))
vi.mock('@/Singra/UI/Zustandsflaeche', () => ({ Zustandsflaeche: ({ titel }: { titel: string }) => <div>{titel}</div> }))

import { ExternPanel } from './ExternPanel'

class Beobachter {
  observe() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', Beobachter)

function Ort() {
  return <span data-testid="ort">{useLocation().pathname}</span>
}

const rechteck = { left: 100, top: 50, width: 400, height: 600, right: 500, bottom: 650, x: 100, y: 50, toJSON: () => ({}) }
const rahmen = { x: 100, y: 50, breite: 400, hoehe: 600 }

describe('ExternPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    for (const fn of Object.values(nativ)) fn.mockClear()
    store.neuerTab.mockClear()
    registrieren.mockClear()
    listener.callback = null
    standbild.bilder = {}
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(rechteck as DOMRect)
  })

  it('lädt die externe WebView, registriert sie und meldet ihre Geometrie', async () => {
    const { unmount } = render(<MemoryRouter><ExternPanel id="whatsapp" voll={false} /></MemoryRouter>)
    expect(nativ.tabLaden).toHaveBeenCalledWith('tab-panel-whatsapp', 'https://web.whatsapp.com', false, undefined)
    await waitFor(() => expect(nativ.panelRahmen).toHaveBeenCalledWith('tab-panel-whatsapp', rahmen))
    expect(registrieren).toHaveBeenCalledWith('tab-panel-whatsapp')
    expect(nativ.tabAktivieren).not.toHaveBeenCalled()
    unmount()
    expect(nativ.tabSchliessen).toHaveBeenCalledWith('tab-panel-whatsapp')
    expect(registrieren).toHaveBeenCalledWith(null)
  })

  it('lädt auch Instagram mit fester ID und URL', async () => {
    const { unmount } = render(<MemoryRouter><ExternPanel id="instagram" voll={false} /></MemoryRouter>)
    expect(nativ.tabLaden).toHaveBeenCalledWith('tab-panel-instagram', 'https://www.instagram.com', false, undefined)
    await waitFor(() => expect(nativ.panelRahmen).toHaveBeenCalledWith('tab-panel-instagram', rahmen))
    unmount()
    expect(nativ.tabSchliessen).toHaveBeenCalledWith('tab-panel-instagram')
  })

  it('ändert die Geometrie ohne die WebView neu zu laden', async () => {
    render(<MemoryRouter><ExternPanel id="discord" voll={false} /></MemoryRouter>)
    await waitFor(() => expect(nativ.panelRahmen).toHaveBeenCalledWith('tab-panel-discord', rahmen))
    nativ.tabLaden.mockClear()
    nativ.panelRahmen.mockClear()
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ ...rechteck, left: 120, top: 70 } as DOMRect)
    act(() => {
      fireEvent(window, new Event('resize'))
    })
    await waitFor(() =>
      expect(nativ.panelRahmen).toHaveBeenCalledWith('tab-panel-discord', { x: 120, y: 70, breite: 400, hoehe: 600 }),
    )
    expect(nativ.tabLaden).not.toHaveBeenCalled()
  })

  it('zeigt Sperr-, Fehler- und Absturzflächen und versteckt die WebView', async () => {
    render(<MemoryRouter><ExternPanel id="telegram" voll={false} /></MemoryRouter>)
    await waitFor(() => expect(listener.callback).not.toBeNull())
    act(() => {
      listener.callback!({ art: 'gesperrt', id: 'tab-panel-telegram', url: 'https://web.telegram.org', grund: 'sozial' })
    })
    expect(screen.getByText('Sperrseite')).toBeInTheDocument()
    expect(nativ.panelRahmen).toHaveBeenLastCalledWith('tab-panel-telegram', null)
    act(() => {
      listener.callback!({ art: 'fehlerseite', id: 'tab-panel-telegram', url: 'https://web.telegram.org', grund: 'offline' })
    })
    expect(screen.getByText('Keine Verbindung')).toBeInTheDocument()
    act(() => {
      listener.callback!({ art: 'absturz', id: 'tab-panel-telegram' })
    })
    expect(screen.getByText('Diese Seite ist abgestürzt')).toBeInTheDocument()
  })

  it('lädt nach Verwerfen neu auf', async () => {
    render(<MemoryRouter><ExternPanel id="whatsapp" voll={false} /></MemoryRouter>)
    await waitFor(() => expect(listener.callback).not.toBeNull())
    nativ.tabLaden.mockClear()
    act(() => {
      listener.callback!({ art: 'verworfen', id: 'tab-panel-whatsapp' })
    })
    expect(nativ.tabLaden).toHaveBeenCalledWith('tab-panel-whatsapp', 'https://web.whatsapp.com', false, undefined)
  })

  it('öffnet Popups als normalen Tab und schließt das Panel auf Seitenwunsch', async () => {
    render(
      <MemoryRouter initialEntries={['/whatsapp']}>
        <ExternPanel id="whatsapp" voll={false} />
        <Ort />
      </MemoryRouter>,
    )
    await waitFor(() => expect(listener.callback).not.toBeNull())
    act(() => {
      listener.callback!({ art: 'neuer_tab', id: 'tab-panel-whatsapp', url: 'https://example.com/anmelden' })
    })
    expect(store.neuerTab).toHaveBeenCalledWith('https://example.com/anmelden', { privat: false })
    act(() => {
      listener.callback!({ art: 'schliessen', id: 'tab-panel-whatsapp' })
    })
    expect(screen.getByTestId('ort')).toHaveTextContent('/')
  })

  it('blendet im Vollbild den Haupttab aus und stellt ihn wieder her', async () => {
    const { unmount } = render(<MemoryRouter><ExternPanel id="discord" voll /></MemoryRouter>)
    expect(nativ.tabAktivieren).toHaveBeenCalledWith(null)
    unmount()
    expect(nativ.tabAktivieren).toHaveBeenCalledWith('tab-main')
  })

  it('zeigt bei Verdeckung das Standbild der externen WebView', () => {
    standbild.bilder['tab-panel-whatsapp'] = 'blob:panel'
    const { container } = render(<MemoryRouter><ExternPanel id="whatsapp" voll={false} /></MemoryRouter>)
    expect(container.querySelector('img')).toHaveAttribute('src', 'blob:panel')
  })
})
