import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Kontextmenue, menueLage } from './Kontextmenue'

const fenster = { breite: 1000, hoehe: 800 }

describe('menueLage', () => {
  it('öffnet am Zeiger, solange Platz ist', () => {
    expect(menueLage(100, 100, 200, 150, fenster)).toEqual({ left: 100, top: 100 })
  })

  it('klappt am rechten und unteren Rand zur anderen Seite', () => {
    expect(menueLage(950, 780, 200, 150, fenster)).toEqual({ left: 750, top: 630 })
  })

  it('bleibt im Fenster, auch wenn auf keiner Seite genug Platz ist', () => {
    expect(menueLage(50, 50, 200, 900, fenster)).toEqual({ left: 50, top: 8 })
  })
})

describe('Kontextmenue', () => {
  const eintraege = (onSelect = vi.fn()) => [
    { key: 'a', label: 'Umbenennen', onSelect },
    { key: 'b', label: 'Gesperrt', disabled: true, onSelect: vi.fn() },
    { key: 'c', label: 'Löschen', destructive: true, separatorBefore: true, onSelect: vi.fn() },
  ]

  it('wandert mit den Pfeiltasten über freie Einträge und führt beim Klick aus', async () => {
    const onSelect = vi.fn()
    const onSchliessen = vi.fn()
    render(<Kontextmenue ort={{ x: 10, y: 10 }} items={eintraege(onSelect)} label="Aktionen" onSchliessen={onSchliessen} />)

    const umbenennen = screen.getByRole('menuitem', { name: 'Umbenennen' })
    await waitFor(() => expect(umbenennen).toHaveFocus())
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(screen.getByRole('menuitem', { name: 'Löschen' })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Home' })
    expect(umbenennen).toHaveFocus()

    fireEvent.click(umbenennen)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSchliessen).toHaveBeenCalledTimes(1)
  })

  it('gibt den Fokus nach Escape an den Auslöser zurück und schließt bei Klick daneben', () => {
    const ausloeser = document.createElement('button')
    document.body.appendChild(ausloeser)
    const onSchliessen = vi.fn()
    render(<Kontextmenue ort={{ x: 10, y: 10 }} items={eintraege()} label="Aktionen" ausloeser={ausloeser} onSchliessen={onSchliessen} />)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onSchliessen).toHaveBeenCalledTimes(1)
    expect(ausloeser).toHaveFocus()
    fireEvent.mouseDown(document.body)
    expect(onSchliessen).toHaveBeenCalledTimes(2)
    ausloeser.remove()
  })

  it('zeigt nichts, solange kein Ort gesetzt ist', () => {
    render(<Kontextmenue ort={null} items={eintraege()} label="Aktionen" onSchliessen={vi.fn()} />)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('schließt mit der Zurück-Taste', async () => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const onSchliessen = vi.fn()
    render(<Kontextmenue ort={{ x: 10, y: 10 }} items={eintraege()} label="Aktionen" onSchliessen={onSchliessen} />)
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(onSchliessen).toHaveBeenCalled())
  })
})
