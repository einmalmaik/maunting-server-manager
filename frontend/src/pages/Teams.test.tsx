import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teamsApi, type Team, type TeamDetail } from '@/api/teams'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { Teams } from './Teams'

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    addMember: vi.fn(),
    updateMember: vi.fn(),
    removeMember: vi.fn(),
    setServerGrants: vi.fn(),
    assignableServers: vi.fn(),
  },
}))

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn().mockResolvedValue([]) }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const personal: Team = {
  id: 1, name: 'einmalmaik', is_personal: true, owner_user_id: 1, is_owner: true,
  can_manage_skills: true, can_manage_memory: true, member_count: 1,
  created_at: '2026-08-09T10:00:00Z',
}

const real: Team = { ...personal, id: 2, name: 'Betrieb', is_personal: false, member_count: 2 }

const realDetail: TeamDetail = {
  ...real,
  members: [
    { user_id: 1, username: 'einmalmaik', role: 'owner', can_manage_skills: true, can_manage_memory: true, joined_at: '2026-08-09T10:00:00Z' },
    { user_id: 2, username: 'kollege', role: 'member', can_manage_skills: false, can_manage_memory: false, joined_at: '2026-08-09T10:00:00Z' },
  ],
  servers: [{ server_id: 7, server_name: 'Valheim', permission_keys: ['server.view'] }],
}

/**
 * Der wichtigste Punkt dieser Seite ist nicht das Formular, sondern die
 * Obergrenze: die Serverauswahl darf nur zeigen, was der Gründer selbst direkt
 * hält. Genau das setzt `permission_service` bei jedem Zugriff durch — hier
 * wird geprüft, dass die Oberfläche dieselbe Grenze zeigt, statt sie erst beim
 * Speichern als Fehler erlebbar zu machen.
 */
describe('Teams', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: ['teams.create'], server_keys: {} },
      isLoading: false, error: null,
    })
    vi.mocked(teamsApi.list).mockReset().mockResolvedValue([real, personal])
    vi.mocked(teamsApi.get).mockReset().mockResolvedValue(realDetail)
    vi.mocked(teamsApi.assignableServers).mockReset().mockResolvedValue([
      { server_id: 7, server_name: 'Valheim', permission_keys: ['server.view', 'server.start'] },
    ])
    vi.mocked(teamsApi.updateMember).mockReset().mockResolvedValue(realDetail)
  })

  it('bietet nur die Rechte an, die der Gründer selbst direkt hält', async () => {
    render(<Teams />)
    await screen.findByText('Betrieb')

    // Das Backend liefert für diesen Server nur zwei Schlüssel — genau die,
    // die der Gründer hält. Ein drittes Recht darf hier nicht auftauchen.
    await waitFor(() => expect(teamsApi.assignableServers).toHaveBeenCalledWith(2))
    const select = await screen.findByLabelText('Rechte: Valheim')
    expect(select).toBeInTheDocument()
    expect(screen.queryByText('server.console.exec')).not.toBeInTheDocument()
  })

  it('erklärt beim persönlichen Team, warum es allein bleibt', async () => {
    vi.mocked(teamsApi.get).mockResolvedValue({ ...personal, members: [], servers: [] })
    vi.mocked(teamsApi.list).mockResolvedValue([personal])
    render(<Teams />)

    // Kein Mitglieder- und kein Serverbereich — stattdessen die Erklärung.
    expect(await screen.findByText(/persönliche Team gehört zu deinem Konto/)).toBeInTheDocument()
    expect(screen.queryByText('Mitglieder')).not.toBeInTheDocument()
  })

  it('schaltet den Verwaltungsschalter eines Mitglieds um', async () => {
    render(<Teams />)
    await screen.findByText('Betrieb')

    fireEvent.click(await screen.findByLabelText('Skills verwalten: kollege'))

    await waitFor(() => expect(teamsApi.updateMember).toHaveBeenCalledWith(2, 2, {
      can_manage_skills: true, can_manage_memory: false,
    }))
  })

  it('blendet das Gründen aus, wenn die Berechtigung fehlt', async () => {
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: [], server_keys: {} },
      isLoading: false, error: null,
    })
    render(<Teams />)
    await screen.findByText('Betrieb')

    expect(screen.queryByLabelText('Teamname')).not.toBeInTheDocument()
  })
})
