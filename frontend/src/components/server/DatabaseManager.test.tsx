/**
 * Nach dem Löschen einer Datenbank blieb ihre Kennung ausgewählt: `fetchResources`
 * glaubte dem gemerkten Wert bedingungslos (`current ?? …`). Die Oberfläche
 * meldete „Datenbank gelöscht" und zeigte weiter die Tabellen und Zeilen der
 * verschwundenen Datenbank; jede Folgeaktion schickte deren Kennung ans Backend
 * und scheiterte ohne erkennbaren Grund.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatabaseManager } from './DatabaseManager'
import { api } from '@/api/client'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  getCsrfToken: vi.fn(() => null),
  clearCsrfTokenMemory: vi.fn(),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn(async () => true),
}))

vi.mock('@/stores/promptStore', () => ({
  prompt: vi.fn(async (opts: { expectedValue?: string }) => opts.expectedValue ?? ''),
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const ERSTE = { id: 1, name: 'alpha', owner_role: 'msm', is_power_user: false }
const ZWEITE = { id: 2, name: 'beta', owner_role: 'msm', is_power_user: false }

/** Datenbanken, die `/databases` liefert — der Löschaufruf kürzt die Liste. */
let vorhandeneDatenbanken = [ERSTE, ZWEITE]

function statistikFuer(pfad: string, opts?: { body?: string }) {
  const body = opts?.body ? JSON.parse(opts.body) : {}
  if (pfad.endsWith('/databases')) {
    return { databases: vorhandeneDatenbanken, users: [] }
  }
  if (pfad.endsWith('/databases/stats')) {
    return { database_id: body.database_id, size_bytes: 0, table_count: 0, connection_count: 0 }
  }
  if (pfad.endsWith('/databases/tables/list')) {
    return { tables: [] }
  }
  return {}
}

describe('DatabaseManager', () => {
  // Die Konsole holt ihre Beschriftungen aus der Sprachdatei. Ohne geladene und
  // festgelegte Sprache stünde hier der rohe Schlüssel statt „Datenbank löschen",
  // und der Test suchte einen Text, den die Umgebung bestimmt.
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vorhandeneDatenbanken = [ERSTE, ZWEITE]
    vi.mocked(api).mockReset()
    vi.mocked(api).mockImplementation(async (pfad: string, opts?: any) => statistikFuer(pfad, opts) as any)
    useAuthStore.setState({
      user: { id: 1, username: 'tester' } as any,
      isAuthenticated: true,
      isLoading: false,
    })
  })

  it('wählt nach dem Löschen die verbleibende Datenbank und lädt deren Daten', async () => {
    render(<DatabaseManager serverId={7} />)

    // Erst steht die zuerst gelieferte Datenbank in der Auswahl.
    const auswahl = await screen.findByRole('button', { name: 'alpha' })

    // Löschen über die Auswahlliste: die erste Datenbank verschwindet.
    fireEvent.click(auswahl)
    vorhandeneDatenbanken = [ZWEITE]
    fireEvent.click(screen.getByText('Datenbank löschen'))

    await waitFor(() => {
      const kennungen = vi
        .mocked(api)
        .mock.calls.filter(([pfad]) => String(pfad).endsWith('/databases/stats'))
        .map(([, opts]) => JSON.parse(String((opts as any).body)).database_id)
      expect(kennungen).toContain(ZWEITE.id)
    })

    // Und die gelöschte Kennung wird nicht erneut abgefragt, nachdem sie fort ist.
    const nachDemLoeschen = vi
      .mocked(api)
      .mock.calls.filter(([pfad]) => String(pfad).endsWith('/servers/7/databases/1'))
    expect(nachDemLoeschen).toHaveLength(1)
    expect(await screen.findByRole('button', { name: 'beta' })).toBeInTheDocument()
  })
})
