import { api } from './client'

/**
 * Teams: geteiltes KI-Wissen und gebündelte Serverrechte.
 *
 * Jeder Benutzer hat ein persönliches Ein-Mann-Team (`is_personal`), damit die
 * KI immer einen Ort zum Lernen hat. Ein echtes Team gründet, wer `teams.create`
 * besitzt.
 */
export interface TeamMember {
  user_id: number
  username: string
  role: 'owner' | 'member'
  can_manage_skills: boolean
  can_manage_memory: boolean
  joined_at: string
}

export interface TeamServer {
  server_id: number
  server_name: string
  permission_keys: string[]
}

export interface Team {
  id: number
  name: string
  is_personal: boolean
  owner_user_id: number
  /** Ob der Abrufende dieses Team verwalten darf — spart eine zweite Abfrage. */
  is_owner: boolean
  can_manage_skills: boolean
  can_manage_memory: boolean
  member_count: number
  created_at: string
}

export interface TeamDetail extends Team {
  members: TeamMember[]
  servers: TeamServer[]
}

export const teamsApi = {
  list: () => api<Team[]>('/teams'),
  get: (teamId: number) => api<TeamDetail>(`/teams/${teamId}`),
  create: (name: string) => api<Team>('/teams', {
    method: 'POST', body: JSON.stringify({ name }),
  }),
  rename: (teamId: number, name: string) => api<Team>(`/teams/${teamId}`, {
    method: 'PUT', body: JSON.stringify({ name }),
  }),
  remove: (teamId: number) => api(`/teams/${teamId}`, { method: 'DELETE' }),
  addMember: (teamId: number, payload: {
    user_id: number; can_manage_skills: boolean; can_manage_memory: boolean
  }) => api<TeamDetail>(`/teams/${teamId}/members`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  updateMember: (teamId: number, userId: number, payload: {
    can_manage_skills: boolean; can_manage_memory: boolean
  }) => api<TeamDetail>(`/teams/${teamId}/members/${userId}`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  removeMember: (teamId: number, userId: number) => api<TeamDetail>(
    `/teams/${teamId}/members/${userId}`, { method: 'DELETE' },
  ),
  setServerGrants: (teamId: number, payload: {
    server_id: number; permission_keys: string[]
  }) => api<TeamDetail>(`/teams/${teamId}/servers`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  /**
   * Was der Gründer dem Team überhaupt geben könnte — nur, was er **direkt**
   * selbst hält. Damit zeigt die Oberfläche dieselbe Obergrenze, die das
   * Backend später durchsetzt, statt sie erst beim Speichern als Fehler zu
   * offenbaren.
   */
  assignableServers: (teamId: number) => api<TeamServer[]>(`/teams/${teamId}/assignable-servers`),
}
