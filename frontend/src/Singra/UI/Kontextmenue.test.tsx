import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { Kontextmenue, menueLage } from './Kontextmenue'
import { Dialog, DialogContent } from './Dialog'
import { fakeLayout } from '@/test/fakeLayout'

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

describe('Kontextmenue in einem Dialog', () => {
  it('schließt mit Escape nur das Menü, nicht den Dialog darunter', async () => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const dialogZu = vi.fn()
    function Aufbau() {
      const [ort, setOrt] = useState<{ x: number; y: number } | null>(null)
      return (
        <Dialog open onOpenChange={dialogZu}>
          <DialogContent>
            <button type="button" onClick={() => setOrt({ x: 10, y: 10 })}>
              öffnen
            </button>
            <Kontextmenue ort={ort} items={[{ key: 'a', label: 'Umbenennen', onSelect: vi.fn() }]} label="Aktionen" onSchliessen={() => setOrt(null)} />
          </DialogContent>
        </Dialog>
      )
    }
    render(<Aufbau />)
    // Erst nach dem Dialog geöffnet, wie im echten Gebrauch: sonst hört das Menü zuerst.
    fireEvent.click(screen.getByText('öffnen'))
    fireEvent.keyDown(screen.getByRole('menuitem', { name: 'Umbenennen' }), { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(dialogZu).not.toHaveBeenCalled()
  })
})

describe('Kontextmenue in einem niedrigen Fenster', () => {
  it('bleibt im Fenster und rollt, wenn es höher ist als das Fenster', () => {
    // Bis 02.10.2026 ragte ein langes Menü unten aus dem Fenster, ohne zu rollen.
    const layout = fakeLayout({
      fenster: { breite: 1000, hoehe: 400 },
      anker: { left: 0, top: 0, width: 0, height: 0 },
      popover: { width: 200, height: 600 },
    })
    try {
      render(<Kontextmenue ort={{ x: 100, y: 300 }} items={[{ key: 'a', label: 'Umbenennen', onSelect: vi.fn() }]} label="Aktionen" onSchliessen={vi.fn()} />)
      const menue = screen.getByRole('menu')
      expect(menue.style.top).toBe('8px')
      expect(menue.className).toContain('max-h-[calc(100dvh-1rem)]')
      expect(menue.className).toContain('overflow-y-auto')
    } finally {
      layout.aufraeumen()
    }
  })
})
