import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { useOhneEdgeMenue, useEigenesMenue } = await import('./EigenesMenue')
const { useTabsStore } = await import('../services/tabsStore')
const { leererTab } = await import('../services/tab')

function Oberflaeche({ tab }: { tab: ReturnType<typeof leererTab> }) {
  useOhneEdgeMenue()
  const eigenes = useEigenesMenue(tab)
  return (
    <main data-testid="flaeche" onContextMenu={eigenes.oeffnen}>
      <p>Startseite</p>
      <input aria-label="Suche" />
      {eigenes.ansicht}
    </main>
  )
}

const rechtsklick = (el: Element) => {
  const e = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 40 })
  el.dispatchEvent(e)
  return e.defaultPrevented
}

describe('Rechtsklick in der Oberfläche', () => {
  const aktion = vi.fn()
  beforeEach(() => {
    aktion.mockReset()
    useTabsStore.setState({ aktion })
  })

  it('zeigt nie das Menü von Edge, außer in Eingaben', () => {
    render(<Oberflaeche tab={leererTab('tab-a')} />)
    expect(rechtsklick(screen.getByText('Startseite'))).toBe(true)
    expect(rechtsklick(document.body)).toBe(true)
    expect(rechtsklick(screen.getByRole('textbox', { name: 'Suche' }))).toBe(false)
  })

  it('zeigt auf einer eigenen Seite das Menü des Browsers mit Zurück', () => {
    render(<Oberflaeche tab={{ ...leererTab('tab-a'), zurueck: true }} />)
    fireEvent.contextMenu(screen.getByText('Startseite'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Zurück' }))
    expect(aktion).toHaveBeenCalledWith('zurueck')
  })

  it('zeigt ohne Verlauf und ohne Auswahl gar kein Menü', () => {
    render(<Oberflaeche tab={leererTab('tab-a')} />)
    fireEvent.contextMenu(screen.getByText('Startseite'))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
