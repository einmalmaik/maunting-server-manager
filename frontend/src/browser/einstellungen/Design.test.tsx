import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({ api: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))
vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))

const { Design } = await import('./Design')
const { useEinstellungenStore } = await import('../services/einstellungenStore')

const ortSuchen = async (begriff: string) => {
  render(<Design />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Wetterort' }), { target: { value: begriff } })
  await waitFor(() => expect(api).toHaveBeenCalled())
}

beforeEach(() => {
  api.mockReset()
  useEinstellungenStore.setState({ wetterOrt: null, widgetsAus: [] })
})

describe('Wetterort suchen', () => {
  it('zeigt bei einem Suchfehler keine erfundenen Treffer', async () => {
    api.mockRejectedValue(new Error('Netz weg'))

    await ortSuchen('ber')

    expect(screen.queryByRole('button', { name: /Berlin/ })).toBeNull()
  })

  it('verwirft eine Antwort, deren Orte keine Liste sind', async () => {
    api.mockResolvedValue({ orte: 'kaputt' })

    await ortSuchen('ber')

    expect(screen.queryByRole('button', { name: /Berlin/ })).toBeNull()
  })

  it('zeigt nur vollständige Orte aus der Antwort', async () => {
    api.mockResolvedValue({
      orte: [
        { name: 'Berlin', land: 'DE', lat: 52.52, lon: 13.41 },
        { name: 'Kaputt', land: 'DE', lat: '52.5', lon: 13.41 },
      ],
    })

    await ortSuchen('ber')

    expect(screen.getByRole('button', { name: /Berlin/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Kaputt/ })).toBeNull()
  })
})
