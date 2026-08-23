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

/**
 * Eine ausgesprochene, noch nicht angenommene Einladung.
 *
 * Dieselbe Zeile aus zwei Blickwinkeln: der Gründer sieht in seiner Teamansicht,
 * wen er angeschrieben hat, der Eingeladene in seiner eigenen Liste, wer ihn
 * haben will und was ihm dabei angeboten wird. Ein Eingeladener ist **kein**
 * Mitglied — Wissen und Serverrechte des Teams erreichen ihn erst mit der
 * Annahme.
 */
export interface TeamInvitation {
  team_id: number
  team_name: string
  user_id: number
  username: string
  invited_by_username: string | null
  can_manage_skills: boolean
  can_manage_memory: boolean
  invited_at: string
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
  /** Offene Einladungen dieses Teams — das Backend füllt sie nur für den Gründer. */
  invitations: TeamInvitation[]
}

export const teamsApi = {
  list: () => api<Team[]>('/teams'),
  get: (teamId: number) => api<TeamDetail>(`/teams/${teamId}`),
  create: (name: string) => api<Team>('/teams', {
    method: 'POST', body: JSON.stringify({ name }),
  }),
  remove: (teamId: number) => api(`/teams/${teamId}`, { method: 'DELETE' }),
  /**
   * Lädt einen Benutzer ein. Mitglied wird er erst, wenn er annimmt — die
   * Antwort führt ihn deshalb unter `invitations` und nicht unter `members`.
   * Der Name sagt das absichtlich: bis eben hieß dieser Aufruf `addMember`,
   * und die Oberfläche meldete danach eine Aufnahme, die nie stattfand.
   */
  inviteMember: (teamId: number, payload: {
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
  /** Die eigenen offenen Einladungen — über sie entscheidet nur der Eingeladene. */
  invitations: () => api<TeamInvitation[]>('/teams/invitations'),
  /** Beitreten. Erst hier entsteht die Mitgliedschaft. */
  acceptInvitation: (teamId: number) => api<TeamDetail>(
    `/teams/invitations/${teamId}/accept`, { method: 'POST' },
  ),
  declineInvitation: (teamId: number) => api(
    `/teams/invitations/${teamId}`, { method: 'DELETE' },
  ),
}
