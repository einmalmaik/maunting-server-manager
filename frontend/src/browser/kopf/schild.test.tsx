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

  it('nennt, was seit der Installation geblockt wurde, erst beim Öffnen', async () => {
    render(<Schild />)
    expect(befehle.invoke).not.toHaveBeenCalledWith('schild_gesamt', undefined)
    fireEvent.click(screen.getByRole('button', { name: /Schutz/ }))
    expect(await screen.findByText('Seit der Installation: 18.420 Werbung · 6.311 Tracker')).toBeInTheDocument()
  })
})
