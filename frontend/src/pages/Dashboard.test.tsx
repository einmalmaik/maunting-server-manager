import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { Dashboard } from './Dashboard'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => false,
}))

// Das Dashboard holt neben `/servers` noch Systemzustand und Node-Kapazität.
// Beide brauchen eine plausible Form, sonst scheitert der Render an ihnen
// statt an dem, was hier geprüft wird.
function antwort(path: string): any {
  if (path === '/system/health') return { services: {} }
  if (path.startsWith('/nodes/capacity-summary')) return { nodes: [], total_nodes: 0 }
  return []
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
}

/**
 * Das Dashboard ist die erste Seite nach dem Login. Bricht `/servers` weg,
 * darf es nicht "Noch keine Server" behaupten — der Betreiber liest das als
 * "deine Server existieren nicht mehr".
 */
describe('Dashboard — Ladefehler statt Leerzustand', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    await i18n.changeLanguage('de')
  })

  it('zeigt bei abgelehntem /servers die Fehlermeldung und nicht den Leerzustand', async () => {
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (path === '/servers') throw new Error('502')
      return antwort(path)
    })
    renderDashboard()

    expect(await screen.findByText('Die Serverliste konnte nicht geladen werden.')).toBeInTheDocument()
    expect(screen.queryByText('Erstelle deinen ersten Server, um loszulegen.')).not.toBeInTheDocument()
  })

  it('zeigt den Leerzustand weiterhin, wenn die Liste leer geladen wurde', async () => {
    vi.mocked(client.api).mockImplementation(async (path: string) => antwort(path))
    renderDashboard()

    expect(await screen.findByText('Erstelle deinen ersten Server, um loszulegen.')).toBeInTheDocument()
    expect(screen.queryByText('Die Serverliste konnte nicht geladen werden.')).not.toBeInTheDocument()
  })
})
