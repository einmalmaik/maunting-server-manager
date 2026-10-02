import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActionMenu } from './ActionMenu'
import { Dialog, DialogContent } from './Dialog'
import { fakeLayout } from '@/test/fakeLayout'

describe('ActionMenu', () => {
  it('supports menu focus, arrow navigation, and focus restoration', async () => {
    render(
      <ActionMenu
        label="Mehr"
        items={[
          { key: 'disabled', label: 'Nicht verfügbar', disabled: true, onSelect: vi.fn() },
          { key: 'rename', label: 'Umbenennen', onSelect: vi.fn() },
          { key: 'delete', label: 'Löschen', onSelect: vi.fn() },
        ]}
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Mehr' })
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })

    const rename = await screen.findByRole('menuitem', { name: 'Umbenennen' })
    const remove = screen.getByRole('menuitem', { name: 'Löschen' })
    await waitFor(() => expect(rename).toHaveFocus())

    fireEvent.keyDown(document, { key: 'End' })
    expect(remove).toHaveFocus()
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(rename).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveFocus()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('schließt mit der Zurück-Taste', async () => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    render(<ActionMenu label="Mehr" items={[{ key: 'a', label: 'Umbenennen', onSelect: vi.fn() }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    await screen.findByRole('menu')
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
  })

  it('klappt unten rechts am Fensterrand nach oben und nach links', () => {
    // Bis 02.10.2026 hing das Menü immer unter dem Knopf und lief unten aus dem Fenster.
    const layout = fakeLayout({
      fenster: { breite: 1024, hoehe: 768 },
      anker: { left: 950, top: 720, width: 60, height: 32 },
      popover: { width: 200, height: 150 },
    })
    try {
      render(<ActionMenu label="Mehr" items={[{ key: 'a', label: 'Umbenennen', onSelect: vi.fn() }]} />)
      fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
      const menue = screen.getByRole('menu')
      expect(menue.style.top).toBe(`${720 - 8 - 150}px`)
      expect(menue.style.left).toBe(`${1010 - 200}px`)
      expect(layout.imFenster(menue)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })

  it('wird nie breiter als das Fenster und kürzt lange Einträge', () => {
    // Ein langer Albumname machte das Menü bis 02.10.2026 breiter als das Telefon.
    const layout = fakeLayout({
      fenster: { breite: 375, hoehe: 700 },
      anker: { left: 300, top: 100, width: 60, height: 32 },
      // So breit, wie `max-w` es im Browser zulässt; jsdom kennt kein CSS.
      popover: { width: 375 - 16, height: 100 },
    })
    try {
      const lang = 'Urlaub an der Ostsee mit der ganzen Familie und den Nachbarn 2026'
      render(<ActionMenu label="Mehr" items={[{ key: 'a', label: lang, onSelect: vi.fn() }]} />)
      fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
      const menue = screen.getByRole('menu')
      expect(menue.className).toContain('max-w-[calc(100vw-1rem)]')
      expect(screen.getByText(lang).className).toContain('truncate')
      expect(layout.imFenster(menue)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })
})

describe('ActionMenu in einem Dialog', () => {
  it('schließt mit Escape nur das Menü, nicht den Dialog darunter', async () => {
    // Bis 02.10.2026 hörte der Dialog Escape am Dokument und hielt sich für
    // den obersten: Escape schloss das Menü und den Dialog mitsamt Eingaben.
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const dialogZu = vi.fn()
    render(
      <Dialog open onOpenChange={dialogZu}>
        <DialogContent>
          <ActionMenu label="Mehr" items={[{ key: 'a', label: 'Umbenennen', onSelect: vi.fn() }]} />
        </DialogContent>
      </Dialog>,
    )
    const knopf = screen.getByRole('button', { name: 'Mehr' })
    fireEvent.click(knopf)
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.keyDown(knopf, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(dialogZu).not.toHaveBeenCalled()
  })
})
