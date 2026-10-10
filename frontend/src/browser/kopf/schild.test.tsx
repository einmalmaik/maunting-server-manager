import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const befehle = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => befehle)

const { Schild } = await import('./Navigationsleiste')

describe('Schild-Fenster', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    befehle.invoke.mockImplementation((befehl: string) =>
      Promise.resolve(befehl === 'schild_gesamt' ? { werbung: 18420, tracker: 6311 } : null),
    )
  })
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    befehle.invoke.mockReset()
  })

  // Was insgesamt geblockt wurde, steht seit 10.10.2026 auf der Startseite (`SchildStatistik.tsx`).
  it('zeigt nur die Zahlen der Seite, nicht die Gesamtzahl', async () => {
    render(<Schild />)
    fireEvent.click(screen.getByRole('button', { name: /Schutz/ }))
    await screen.findByRole('dialog')
    expect(befehle.invoke).not.toHaveBeenCalledWith('schild_gesamt', undefined)
    expect(screen.queryByText(/18\.420|Installation/)).not.toBeInTheDocument()
  })
})
