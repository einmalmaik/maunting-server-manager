import { useCallback, useEffect, useState } from 'react'
import { Plus, Server, Trash2, UserPlus, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api, SanitizedApiError } from '@/api/client'
import { teamsApi, type Team, type TeamDetail, type TeamServer } from '@/api/teams'
import { Button, Dropdown, MultiSelect, Switch } from '@/Singra/UI'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { useHasPermission } from '@/hooks/useHasPermission'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

interface UserOption {
  id: number
  username: string
}

/**
 * Teams verwalten: Mitglieder, ihre beiden Schalter und die Server des Teams.
 *
 * Der wichtigste Gedanke der Seite steckt in `assignableServers`: die
 * Serverauswahl zeigt ausschließlich, was der Gründer **selbst direkt** hält.
 * Das ist dieselbe Obergrenze, die `permission_service` bei jedem Zugriff
 * durchsetzt — sie hier sichtbar zu machen erspart die Erfahrung, eine
 * Einstellung zu speichern und dafür eine Fehlermeldung zu bekommen.
 */
export function Teams() {
  const { t } = useTranslation()
  const canCreate = useHasPermission('teams.create')
  const canReadUsers = useHasPermission('users.read')

  const [teams, setTeams] = useState<Team[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<TeamDetail | null>(null)
  const [assignable, setAssignable] = useState<TeamServer[]>([])
  const [users, setUsers] = useState<UserOption[]>([])
  const [newName, setNewName] = useState('')
  const [newMemberId, setNewMemberId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const reloadTeams = useCallback(async () => {
    const rows = await teamsApi.list()
    setTeams(rows)
    setSelectedId((current) => current ?? rows[0]?.id ?? null)
    return rows
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      teamsApi.list(),
      // Ohne `users.read` kann man niemanden aufnehmen — die Liste bleibt dann
      // leer statt die ganze Seite scheitern zu lassen.
      canReadUsers ? api<UserOption[]>('/admin/users').catch(() => [] as UserOption[])
        : Promise.resolve([] as UserOption[]),
    ])
      .then(([rows, userRows]) => {
        if (!active) return
        setTeams(rows)
        setSelectedId(rows[0]?.id ?? null)
        setUsers(userRows.map((row) => ({ id: row.id, username: row.username })))
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('teams.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [canReadUsers, t])

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null)
      return
    }
    let active = true
    teamsApi.get(selectedId)
      .then((row) => {
        if (!active) return
        setDetail(row)
        // Nur der Gründer darf Server zuordnen; für alle anderen wäre die
        // Abfrage ein garantierter 403.
        if (row.is_owner && !row.is_personal) {
          teamsApi.assignableServers(row.id)
            .then((rows) => { if (active) setAssignable(rows) })
            .catch(() => { if (active) setAssignable([]) })
        } else {
          setAssignable([])
        }
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('teams.errors.load'))
      })
    return () => { active = false }
  }, [selectedId, t])

  const run = async (action: () => Promise<unknown>, successKey?: string) => {
    if (busy) return
    setBusy(true)
    try {
      await action()
      if (successKey) toast.success(t(successKey))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('teams.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const createTeam = async () => {
    const name = newName.trim()
    if (!name) return
    await run(async () => {
      const created = await teamsApi.create(name)
      setNewName('')
      await reloadTeams()
      setSelectedId(created.id)
    }, 'teams.created')
  }

  const removeTeam = async (team: TeamDetail) => {
    if (!await confirm({
      title: t('teams.removeTitle'),
      message: t('teams.removeConfirm', { name: team.name }),
      confirmText: t('common.delete'),
      danger: true,
    })) return
    await run(async () => {
      await teamsApi.remove(team.id)
      setSelectedId(null)
      const rows = await reloadTeams()
      setSelectedId(rows[0]?.id ?? null)
    }, 'teams.removed')
  }

  const addMember = async () => {
    if (!detail || !newMemberId) return
    await run(async () => {
      const updated = await teamsApi.addMember(detail.id, {
        user_id: Number(newMemberId), can_manage_skills: false, can_manage_memory: false,
      })
      setDetail(updated)
      setNewMemberId(null)
      await reloadTeams()
    }, 'teams.memberAdded')
  }

  const setServerKeys = async (serverId: number, keys: string[]) => {
    if (!detail) return
    await run(async () => {
      setDetail(await teamsApi.setServerGrants(detail.id, {
        server_id: serverId, permission_keys: keys,
      }))
    }, 'teams.serversSaved')
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center" aria-label={t('common.loading')}>
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  const memberCandidates = users.filter(
    (user) => !detail?.members.some((member) => member.user_id === user.id),
  )

  return (
    <div className="msm-page space-y-5">
      <PageHeader
        eyebrow={t('teams.eyebrow')}
        title={t('teams.title')}
        description={t('teams.description')}
      />

      {canCreate && (
        <section className="msm-card p-6" aria-labelledby="team-create">
          <h2 id="team-create" className="mb-3 font-headline text-lg font-semibold text-on-surface">
            {t('teams.create')}
          </h2>
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-[16rem] flex-1 space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('teams.name')}
              </span>
              <input
                className="msm-input"
                maxLength={64}
                value={newName}
                disabled={busy}
                onChange={(event) => setNewName(event.target.value)}
                aria-label={t('teams.name')}
              />
            </label>
            <Button type="button" disabled={busy || newName.trim().length < 2} onClick={() => void createTeam()}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              {t('teams.create')}
            </Button>
          </div>
        </section>
      )}

      <section className="msm-card p-6" aria-labelledby="team-select">
        <label className="block w-full max-w-sm space-y-1.5">
          <span id="team-select" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('teams.select')}
          </span>
          <Dropdown
            value={selectedId === null ? null : String(selectedId)}
            onChange={(value) => setSelectedId(Number(value))}
            options={teams.map((team) => ({
              value: String(team.id),
              label: team.name,
              hint: team.is_personal ? t('teams.personal') : t('teams.memberCount', { count: team.member_count }),
            }))}
            disabled={busy}
            aria-label={t('teams.select')}
          />
        </label>
        {detail?.is_personal && (
          <p className="mt-3 max-w-2xl rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-xs leading-5 text-on-surface-variant">
            {t('teams.personalHint')}
          </p>
        )}
      </section>

      {detail && !detail.is_personal && (
        <>
          <section className="msm-card p-6" aria-labelledby="team-members">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Users className="h-5 w-5 text-primary" aria-hidden="true" />
                <h2 id="team-members" className="font-headline text-lg font-semibold text-on-surface">
                  {t('teams.members')}
                </h2>
              </div>
              {detail.is_owner && (
                <Button type="button" variant="destructive" size="sm" disabled={busy} onClick={() => void removeTeam(detail)}>
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                  {t('teams.remove')}
                </Button>
              )}
            </div>

            <ul className="space-y-2">
              {detail.members.map((member) => (
                <li
                  key={member.user_id}
                  className="flex flex-wrap items-center gap-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4"
                >
                  <span className="min-w-[8rem] flex-1 text-sm font-medium text-on-surface">
                    {member.username}
                    {member.role === 'owner' && (
                      <span className="ml-2 text-xs text-on-surface-variant">{t('teams.founder')}</span>
                    )}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-on-surface-variant">{t('teams.manageSkills')}</span>
                    <Switch
                      checked={member.can_manage_skills}
                      disabled={!detail.is_owner || busy}
                      onCheckedChange={(next) => void run(async () => {
                        setDetail(await teamsApi.updateMember(detail.id, member.user_id, {
                          can_manage_skills: next, can_manage_memory: member.can_manage_memory,
                        }))
                      }, 'teams.memberSaved')}
                      aria-label={`${t('teams.manageSkills')}: ${member.username}`}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-on-surface-variant">{t('teams.manageMemory')}</span>
                    <Switch
                      checked={member.can_manage_memory}
                      disabled={!detail.is_owner || busy}
                      onCheckedChange={(next) => void run(async () => {
                        setDetail(await teamsApi.updateMember(detail.id, member.user_id, {
                          can_manage_skills: member.can_manage_skills, can_manage_memory: next,
                        }))
                      }, 'teams.memberSaved')}
                      aria-label={`${t('teams.manageMemory')}: ${member.username}`}
                    />
                  </div>
                  {detail.is_owner && member.role !== 'owner' && (
                    <Button
                      type="button" variant="ghost" size="sm" disabled={busy}
                      onClick={() => void run(async () => {
                        setDetail(await teamsApi.removeMember(detail.id, member.user_id))
                        await reloadTeams()
                      }, 'teams.memberRemoved')}
                      aria-label={`${t('teams.removeMember')}: ${member.username}`}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>

            {detail.is_owner && memberCandidates.length > 0 && (
              <div className="mt-4 flex flex-wrap items-end gap-3">
                <label className="min-w-[14rem] flex-1 space-y-1.5">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('teams.addMember')}
                  </span>
                  <Dropdown
                    value={newMemberId}
                    onChange={setNewMemberId}
                    options={memberCandidates.map((user) => ({ value: String(user.id), label: user.username }))}
                    placeholder={t('teams.selectUser')}
                    disabled={busy}
                    aria-label={t('teams.addMember')}
                  />
                </label>
                <Button type="button" disabled={busy || !newMemberId} onClick={() => void addMember()}>
                  <UserPlus className="h-4 w-4" aria-hidden="true" />
                  {t('teams.addMember')}
                </Button>
              </div>
            )}
          </section>

          {detail.is_owner && (
            <section className="msm-card p-6" aria-labelledby="team-servers">
              <div className="mb-2 flex items-center gap-2">
                <Server className="h-5 w-5 text-secondary" aria-hidden="true" />
                <h2 id="team-servers" className="font-headline text-lg font-semibold text-on-surface">
                  {t('teams.servers')}
                </h2>
              </div>
              <p className="mb-4 max-w-3xl text-sm text-on-surface-variant">{t('teams.serversHint')}</p>

              {assignable.length === 0 && (
                <p className="text-sm text-on-surface-variant">{t('teams.noAssignableServers')}</p>
              )}

              <div className="space-y-3">
                {assignable.map((server) => {
                  const current = detail.servers.find((item) => item.server_id === server.server_id)
                  return (
                    <div
                      key={server.server_id}
                      className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4"
                    >
                      <span className="block text-sm font-medium text-on-surface">{server.server_name}</span>
                      <MultiSelect
                        options={server.permission_keys.map((key) => ({ value: key, label: key }))}
                        values={current?.permission_keys ?? []}
                        onChange={(keys) => void setServerKeys(server.server_id, keys)}
                        disabled={busy}
                        placeholder={t('teams.noAccess')}
                        aria-label={`${t('teams.permissions')}: ${server.server_name}`}
                      />
                    </div>
                  )
                })}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
