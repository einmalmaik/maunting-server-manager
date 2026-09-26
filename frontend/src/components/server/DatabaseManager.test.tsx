/**
 * Nach dem Löschen einer Datenbank blieb ihre Kennung ausgewählt: `fetchResources`
 * glaubte dem gemerkten Wert bedingungslos (`current ?? …`). Die Oberfläche
 * meldete „Datenbank gelöscht" und zeigte weiter die verschwundene Datenbank;
 * jede Folgeaktion schickte deren Kennung ans Backend und scheiterte ohne
 * erkennbaren Grund. Heute hängt das Studio an der Auswahl — es muss auf die
 * verbleibende Datenbank wechseln.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatabaseManager } from './DatabaseManager'
import { api } from '@/api/client'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  getCsrfToken: vi.fn(() => null),
  clearCsrfTokenMemory: vi.fn(),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))

vi.mock('@/stores/promptStore', () => ({
  prompt: vi.fn(async (opts: { expectedValue?: string }) => opts.expectedValue ?? ''),
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

// Das Studio selbst hat eigene Tests; hier zählt nur, welche Datenbank es bekommt.
vi.mock('@/components/postgres/PostgresStudio', () => ({
  PostgresStudio: ({ databaseId, databaseName }: { databaseId: number; databaseName: string }) => (
    <div data-testid="studio">{`${databaseId}:${databaseName}`}</div>
  ),
}))

const ERSTE = { id: 1, name: 'alpha', owner_role: 'msm', is_power_user: false }
const ZWEITE = { id: 2, name: 'beta', owner_role: 'msm', is_power_user: false }

/** Datenbanken, die `/databases` liefert — der Löschaufruf kürzt die Liste. */
let vorhandeneDatenbanken = [ERSTE, ZWEITE]

describe('DatabaseManager', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vorhandeneDatenbanken = [ERSTE, ZWEITE]
    vi.mocked(api).mockReset()
    vi.mocked(api).mockImplementation(async (pfad: string) =>
      (pfad.endsWith('/databases') ? { databases: vorhandeneDatenbanken, users: [] } : {}) as any,
    )
  })

  it('wählt nach dem Löschen die verbleibende Datenbank und gibt sie dem Studio', async () => {
    render(<DatabaseManager serverId={7} />)
    expect(await screen.findByTestId('studio')).toHaveTextContent('1:alpha')

    vorhandeneDatenbanken = [ZWEITE]
    fireEvent.click(screen.getByRole('button', { name: i18n.t('databaseManager.actions') }))
    fireEvent.click(await screen.findByRole('menuitem', { name: i18n.t('databaseManager.deleteDatabase') }))

    await waitFor(() => expect(screen.getByTestId('studio')).toHaveTextContent('2:beta'))
    const loeschaufrufe = vi.mocked(api).mock.calls.filter(([pfad]) => String(pfad).endsWith('/servers/7/databases/1'))
    expect(loeschaufrufe).toHaveLength(1)
    expect(JSON.parse(String((loeschaufrufe[0][1] as any).body))).toEqual({ confirm_name: 'alpha' })
  })

  it('bietet auf dem Datenbankserver keinen Power-User an', async () => {
    render(<DatabaseManager serverId={7} dedicated />)
    await screen.findByTestId('studio')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('databaseManager.actions') }))
    await screen.findByRole('menuitem', { name: i18n.t('databaseManager.newDatabase') })
    expect(screen.queryByRole('menuitem', { name: i18n.t('databaseManager.powerUserEnable') })).toBeNull()
  })
})
