import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { Navigationsleiste } = await import('../kopf/Navigationsleiste')
const { Seitenleiste } = await import('./Seitenleiste')
const { Panel } = await import('./Panel')
const { Anordnung } = await import('../einstellungen/Anordnung')
const { ALLE_EINTRAEGE, geordnet, leistenziel } = await import('./eintraege')
const { useEinstellungenStore } = await import('../services/einstellungenStore')
const { useSitzung } = await import('../services/sitzung')

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

const leistenNamen = () =>
  within(screen.getByRole('navigation', { name: 'Seitenleiste' }))
    .getAllByRole('button')
    .map((b) => b.getAttribute('aria-label'))

describe('Wo die Einträge stehen', () => {
  beforeEach(() => {
    useSitzung.setState({ stand: 'aus' })
    useEinstellungenStore.setState({ leiste: 'menue', panelSeite: 'rechts', anordnung: [], ausgeblendet: [], panelBreite: 420 })
  })

  it('ordnet nach der Wahl des Nutzers, Neues folgt in der Grundordnung', () => {
    const namen = geordnet(ALLE_EINTRAEGE, ['downloads', 'singra']).map(leistenziel)
    expect(namen).toEqual(['downloads', 'singra', 'messenger', 'notizen', 'kalender', 'tresor', 'lesezeichen', 'verlauf'])
  })

  it('öffnet aus dem Menü ein Panel und schließt das Menü', () => {
    zeigen(<Navigationsleiste />)
    expect(screen.queryByRole('button', { name: 'Verlauf' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Menü' }))
    const menue = screen.getByRole('dialog', { name: 'Menü' })
    fireEvent.click(within(menue).getByRole('button', { name: 'Verlauf' }))
    expect(screen.getByTestId('ort')).toHaveTextContent('/verlauf')
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument()
  })

  it('zeigt die Einträge oben als Symbole, und ohne Menüknopf', () => {
    useEinstellungenStore.setState({ leiste: 'oben' })
    zeigen(<Navigationsleiste />)
    expect(screen.queryByRole('button', { name: 'Menü' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Lesezeichen' }))
    expect(screen.getByTestId('ort')).toHaveTextContent('/lesezeichen')
    expect(screen.getByRole('button', { name: 'Einstellungen' })).toBeInTheDocument()
  })

  it('lässt ausgeblendete Knöpfe weg', () => {
    useEinstellungenStore.setState({ ausgeblendet: ['neuLaden', 'schild', 'stern'] })
    zeigen(<Navigationsleiste />)
    expect(screen.queryByRole('button', { name: 'Neu laden' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Lesezeichen setzen/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Schutz|Werbung/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zurück' })).toBeInTheDocument()
  })

  it('ordnet und blendet unter Design, und die Leiste folgt', () => {
    useEinstellungenStore.setState({ leiste: 'links' })
    zeigen(
      <>
        <Anordnung />
        <Seitenleiste seite="links" />
      </>,
    )
    expect(leistenNamen().slice(-4, -2)).toEqual(['Verlauf', 'Downloads'])
    fireEvent.click(screen.getByRole('button', { name: 'Downloads nach oben' }))
    expect(leistenNamen().slice(-4, -2)).toEqual(['Downloads', 'Verlauf'])
    fireEvent.click(screen.getByRole('switch', { name: 'Verlauf' }))
    expect(leistenNamen()).not.toContain('Verlauf')
    expect(useEinstellungenStore.getState().ausgeblendet).toEqual(['verlauf'])
  })

  it('zieht das Panel links mit dem Griff auf seiner rechten Seite breiter', () => {
    zeigen(<Panel seite="links" />, '/verlauf')
    const griff = screen.getByRole('separator', { name: /Breite/ })
    expect(griff.className).toContain('right-0')
    act(() => {
      fireEvent.keyDown(griff, { key: 'ArrowRight' })
    })
    expect(useEinstellungenStore.getState().panelBreite).toBe(444)
  })
})
