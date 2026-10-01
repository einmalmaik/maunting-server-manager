/**
 * Zurück schließt die oberste Ansicht, nicht die App (Android) oder die Seite.
 */
import { StrictMode, useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

type Hook = typeof import('./useZurueckSchliesst').useZurueckSchliesst
let useZurueckSchliesst: Hook

beforeEach(async () => {
  // Der Stapel lebt im Modul; jeder Test beginnt mit einem frischen.
  vi.resetModules()
  ;({ useZurueckSchliesst } = await import('./useZurueckSchliesst'))
  window.history.replaceState({ idx: 3, key: 'router' }, '')
})

const tiefe = () => (window.history.state as { msmTiefe?: number } | null)?.msmTiefe ?? 0

function Ansicht({ name, onZu }: { name: string; onZu: () => void }) {
  useZurueckSchliesst(true, onZu)
  return (
    <div>
      {name}
      <button type="button" onClick={onZu}>
        {name} schließen
      </button>
    </div>
  )
}

function Aufbau({ zwei = false }: { zwei?: boolean }) {
  const [a, setA] = useState(true)
  const [b, setB] = useState(zwei)
  return (
    <>
      {a && <Ansicht name="Lichtbox" onZu={() => setA(false)} />}
      {b && <Ansicht name="Rückfrage" onZu={() => setB(false)} />}
    </>
  )
}

describe('useZurueckSchliesst', () => {
  it('legt beim Öffnen einen Eintrag an, und Zurück schließt die Ansicht', async () => {
    const eintragen = vi.spyOn(window.history, 'pushState')
    render(<Aufbau />)
    await waitFor(() => expect(tiefe()).toBe(1))
    expect(eintragen).toHaveBeenCalledTimes(1)
    eintragen.mockRestore()
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByText('Lichtbox')).not.toBeInTheDocument())
    expect(tiefe()).toBe(0)
  })

  it('behält den Zustand des Routers im eigenen Eintrag', async () => {
    render(<Aufbau />)
    await waitFor(() => expect(tiefe()).toBe(1))
    expect(window.history.state).toMatchObject({ idx: 3, key: 'router', msmTiefe: 1 })
  })

  it('schließt mit Zurück nur die oberste von zwei Ansichten', async () => {
    render(<Aufbau zwei />)
    await waitFor(() => expect(tiefe()).toBe(2))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByText('Rückfrage')).not.toBeInTheDocument())
    expect(screen.getByText('Lichtbox')).toBeInTheDocument()
    expect(tiefe()).toBe(1)
  })

  it('nimmt den Eintrag heraus, wenn die Ansicht anders geschlossen wird', async () => {
    render(<Aufbau />)
    await waitFor(() => expect(tiefe()).toBe(1))
    fireEvent.click(screen.getByRole('button', { name: 'Lichtbox schließen' }))
    await waitFor(() => expect(tiefe()).toBe(0))
    // Der nächste Druck auf Zurück verlässt die Seite wie gewohnt, kein toter Eintrag.
    expect(window.history.state).toMatchObject({ idx: 3, key: 'router' })
  })

  it('legt unter StrictMode trotz doppelter Montage genau einen Eintrag an', async () => {
    const eintragen = vi.spyOn(window.history, 'pushState')
    const sprung = vi.spyOn(window.history, 'go')
    render(
      <StrictMode>
        <Aufbau />
      </StrictMode>,
    )
    await waitFor(() => expect(tiefe()).toBe(1))
    expect(eintragen).toHaveBeenCalledTimes(1)
    expect(sprung).not.toHaveBeenCalled()
    eintragen.mockRestore()
    sprung.mockRestore()
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByText('Lichtbox')).not.toBeInTheDocument())
  })

  it('springt nicht über eine Navigation des Routers zurück', async () => {
    const sprung = vi.spyOn(window.history, 'go')
    const { unmount } = render(<Aufbau />)
    await waitFor(() => expect(tiefe()).toBe(1))
    // Der Router navigiert, während die Ansicht offen ist; danach baut sie ab.
    window.history.pushState({ idx: 4, key: 'neu' }, '')
    unmount()
    await Promise.resolve()
    await Promise.resolve()
    expect(sprung).not.toHaveBeenCalled()
    expect(window.history.state).toMatchObject({ idx: 4, key: 'neu' })
    sprung.mockRestore()
  })

  it('gibt einer Ansicht, die nicht schließt, ihren Eintrag zurück', async () => {
    function Stur() {
      useZurueckSchliesst(true, () => undefined)
      return <p>arbeitet</p>
    }
    render(<Stur />)
    await waitFor(() => expect(tiefe()).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(tiefe()).toBe(0))
    // Ohne neuen Eintrag beendete der nächste Druck auf Zurück die App.
    await waitFor(() => expect(tiefe()).toBe(1))
    expect(screen.getByText('arbeitet')).toBeInTheDocument()
  })
})
