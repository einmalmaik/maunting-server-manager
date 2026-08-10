import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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

const personalDetail: TeamDetail = { ...personal, members: [], servers: [] }

const realDetail: TeamDetail = {
  ...real,
  members: [
    { user_id: 1, username: 'einmalmaik', role: 'owner', can_manage_skills: true, can_manage_memory: true, joined_at: '2026-08-09T10:00:00Z' },
    { user_id: 2, username: 'kollege', role: 'member', can_manage_skills: false, can_manage_memory: false, joined_at: '2026-08-09T10:00:00Z' },
  ],
  servers: [{ server_id: 7, server_name: 'Valheim', permission_keys: ['server.view'] }],
}

function renderTeams() {
  return render(<MemoryRouter><Teams /></MemoryRouter>)
}

/** Wechselt in den Teambereich — die Seite startet bewusst bei „Persönlich". */
async function zuTeams() {
  fireEvent.click(await screen.findByRole('tab', { name: 'Teams' }))
}

/**
 * Zwei Zusicherungen liegen hier übereinander.
 *
 * Die ältere ist die Obergrenze: die Serverauswahl darf nur zeigen, was der
 * Gründer selbst direkt hält — dieselbe Grenze, die `permission_service` bei
 * jedem Zugriff durchsetzt.
 *
 * Die neuere ist die Trennung: persönliches Wissen und Teamwissen sind zwei
 * Welten. Unter „Persönlich" stehen die eigenen Skills und **kein**
 * Gedächtnis — persönliche Erinnerungen gehören ins Profil, und bis vor Kurzem
 * zeigte diese Seite genau dieselbe Liste noch einmal.
 */
describe('Teams', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        global_keys: ['teams.create', 'ai.skills.use', 'ai.memory.use'], server_keys: {},
      },
      isLoading: false, error: null,
    })
    vi.mocked(teamsApi.list).mockReset().mockResolvedValue([real, personal])
    vi.mocked(teamsApi.get).mockReset().mockImplementation(async (id: number) =>
      (id === 1 ? personalDetail : realDetail))
    vi.mocked(teamsApi.assignableServers).mockReset().mockResolvedValue([
      { server_id: 7, server_name: 'Valheim', permission_keys: ['server.view', 'server.start'] },
    ])
    vi.mocked(teamsApi.updateMember).mockReset().mockResolvedValue(realDetail)
  })

  it('bietet nur die Rechte an, die der Gründer selbst direkt hält', async () => {
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    // Das Backend liefert für diesen Server nur zwei Schlüssel — genau die,
    // die der Gründer hält. Ein drittes Recht darf hier nicht auftauchen.
    await waitFor(() => expect(teamsApi.assignableServers).toHaveBeenCalledWith(2))
    const select = await screen.findByLabelText('Rechte: Valheim')
    expect(select).toBeInTheDocument()
    expect(screen.queryByText('server.console.exec')).not.toBeInTheDocument()
  })

  it('zeigt unter Persönlich Skills, aber kein Gedächtnis', async () => {
    renderTeams()

    // Der Bereich „Persönlich" ist die Voreinstellung: das eigene Wissen ist
    // das, was man am häufigsten sucht.
    expect(await screen.findByRole('tab', { name: 'Persönlich' })).toHaveAttribute('aria-selected', 'true')
    await screen.findByLabelText('Skills')
    expect(screen.queryByLabelText('Persönliches KI-Memory')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Wissen dieses Teams')).not.toBeInTheDocument()
    expect(screen.getByText(/Erinnerungen stehen nicht hier/)).toBeInTheDocument()

    // Mitglieder und Server gibt es beim Ein-Mann-Team nicht.
    expect(screen.queryByText('Mitglieder')).not.toBeInTheDocument()
  })

  it('führt das persönliche Team nicht in der Teamauswahl', async () => {
    renderTeams()
    await zuTeams()

    // Die Auswahl aufklappen und hineinsehen: „einmalmaik" ist der Name des
    // persönlichen Teams. In einer Liste, aus der man ein Team auswählt, hat es
    // nichts verloren — es ist keins. (Derselbe Name steht daneben als
    // Mitglied des echten Teams; deshalb wird hier gezielt die Liste geprüft
    // und nicht die ganze Seite.)
    const auswahl = (await screen.findAllByLabelText('Team'))
      .find((element) => element.getAttribute('aria-haspopup') === 'listbox')
    expect(auswahl).toBeDefined()
    fireEvent.click(auswahl as HTMLElement)

    const optionen = (await screen.findAllByRole('option')).map((element) => element.textContent)
    expect(optionen.some((text) => text?.includes('Betrieb'))).toBe(true)
    expect(optionen.some((text) => text?.includes('einmalmaik'))).toBe(false)
  })

  it('zeigt beim echten Team beides — Gedächtnis und Skills', async () => {
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    expect(await screen.findByLabelText('Wissen dieses Teams')).toBeInTheDocument()
    expect(await screen.findByLabelText('Skills')).toBeInTheDocument()
  })

  it('zeigt keine Skills ohne das Recht, sie zu benutzen', async () => {
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        global_keys: ['teams.create', 'ai.memory.use'], server_keys: {},
      },
      isLoading: false, error: null,
    })
    renderTeams()

    await screen.findByRole('tab', { name: 'Persönlich' })
    // Vorher rendert die Seite das Panel ungeprüft, und der Endpunkt dahinter
    // verlangt `ai.skills.use` — es gab eine Fehlermeldung statt einer Ansicht.
    await waitFor(() => expect(screen.queryByLabelText('Skills')).not.toBeInTheDocument())
  })

  it('schaltet den Verwaltungsschalter eines Mitglieds um', async () => {
    renderTeams()
    await zuTeams()
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
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    expect(screen.queryByLabelText('Teamname')).not.toBeInTheDocument()
  })
})
