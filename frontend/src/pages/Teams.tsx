import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, LogOut, Mail, Plus, Server, Trash2, User, UserPlus, Users, UsersRound, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { api, SanitizedApiError } from '@/api/client'
import {
  teamsApi,
  type Team,
  type TeamDetail,
  type TeamInvitation,
  type TeamMember,
  type TeamServer,
} from '@/api/teams'
import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import type { AiKnowledgeScope, AiSkillScope } from '@/components/ai/knowledgeScope'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { Button, Dropdown, MultiSelect, Switch } from '@/Singra/UI'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { useHasPermission } from '@/hooks/useHasPermission'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

interface UserOption {
  id: number
  username: string
}

type Bereich = 'personal' | 'teams'

const BEREICHE: TabDef<Bereich>[] = [
  { id: 'personal', labelKey: 'teams.areaPersonal', icon: User },
  { id: 'teams', labelKey: 'teams.areaTeams', icon: UsersRound },
]

/**
 * Erinnerungen eines **echten** Teams.
 *
 * Für das persönliche Team gibt es hier bewusst keinen Fall: persönliche
 * Erinnerungen stehen im Profil und nirgends sonst. Bis eben bildete diese
 * Funktion `is_personal` auf `{kind:'user'}` ab — die Teamseite zeigte dann
 * dasselbe persönliche Gedächtnis wie das Profil.
 */
function memoryScope(detail: TeamDetail): AiKnowledgeScope {
  return { kind: 'team', teamId: detail.id, canManage: detail.can_manage_memory }
}

/**
 * Skills eines Bereichs — auch das persönliche Team ist hier ein Team.
 *
 * Anders als beim Gedächtnis ist das kein Kompromiss, sondern die Bauart:
 * `scope_identity` kennt für Skills nur `"global"` und `"team:{id}"`. Das
 * Ein-Mann-Team ist ihr persönlicher Ort, und weil es eine echte Teamzeile mit
 * beiden Verwaltungsschaltern ist, stimmt `can_manage_skills` dort ohnehin.
 */
function skillScope(detail: TeamDetail): AiSkillScope {
  return {
    kind: 'team',
    teamId: detail.id,
    personal: detail.is_personal,
    canManage: detail.can_manage_skills,
  }
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
  const canUseSkills = useHasPermission('ai.skills.use')
  // Die eigene Zeile in der Mitgliederliste trägt nicht „Mitglied entfernen",
  // sondern „Team verlassen" — und sie trägt es auch ohne Gründerrechte.
  const benutzerId = useAuthStore((state) => state.user?.id ?? null)

  const [bereich, setBereich] = useState<Bereich>('personal')
  const [teams, setTeams] = useState<Team[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<TeamDetail | null>(null)
  const [assignable, setAssignable] = useState<TeamServer[]>([])
  const [users, setUsers] = useState<UserOption[]>([])
  // Die Einladungen, die an *diesen* Benutzer gehen. Sie hängen an keinem Team
  // aus `teams` — solange er nicht angenommen hat, gehört er keinem davon an.
  const [einladungen, setEinladungen] = useState<TeamInvitation[]>([])
  const [newName, setNewName] = useState('')
  const [newMemberId, setNewMemberId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // Die beiden Welten sauber getrennt: das Ein-Mann-Team gehört nicht in eine
  // Liste, aus der man „ein Team auswählt". Es ist keins.
  const persoenlich = useMemo(() => teams.find((team) => team.is_personal) ?? null, [teams])
  const echte = useMemo(() => teams.filter((team) => !team.is_personal), [teams])
  const aktiveId = bereich === 'personal' ? persoenlich?.id ?? null : selectedId

  const reloadTeams = useCallback(async () => {
    const rows = await teamsApi.list()
    setTeams(rows)
    setSelectedId((current) => current ?? rows.find((row) => !row.is_personal)?.id ?? null)
    return rows
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      teamsApi.list(),
      // Ohne `users.read` kann man niemanden einladen — die Liste bleibt dann
      // leer statt die ganze Seite scheitern zu lassen.
      canReadUsers ? api<UserOption[]>('/admin/users').catch(() => [] as UserOption[])
        : Promise.resolve([] as UserOption[]),
      // Einladungen hängen an keinem Recht: bekommen kann sie jeder, und
      // entscheiden darf über sie nur der Eingeladene selbst.
      teamsApi.invitations(),
    ])
      .then(([rows, userRows, invitationRows]) => {
        if (!active) return
        setTeams(rows)
        setSelectedId(rows.find((row) => !row.is_personal)?.id ?? null)
        setUsers(userRows.map((row) => ({ id: row.id, username: row.username })))
        setEinladungen(invitationRows)
        // Wer eine Einladung offen hat, soll sie sehen und nicht suchen. Die
        // Seite startet sonst bei „Persönlich", und genau der Benutzer, der
        // noch keinem Team angehört, fände dort nur den Hinweis, dass er
        // keinem Team angehört.
        if (invitationRows.length > 0) setBereich('teams')
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
    if (aktiveId === null) {
      setDetail(null)
      return
    }
    let active = true
    teamsApi.get(aktiveId)
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
  }, [aktiveId, t])

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

  /**
   * Spricht eine Einladung aus — und nimmt **niemanden** auf.
   *
   * Die Antwort führt den Eingeladenen unter `invitations`; die Mitgliederliste
   * und damit `member_count` ändern sich erst mit seiner Zusage. Deshalb steht
   * hier auch kein `reloadTeams()` mehr: es gäbe dieselbe Liste zurück.
   */
  const inviteMember = async () => {
    if (!detail || !newMemberId) return
    await run(async () => {
      setDetail(await teamsApi.inviteMember(detail.id, {
        user_id: Number(newMemberId), can_manage_skills: false, can_manage_memory: false,
      }))
      setNewMemberId(null)
    }, 'teams.memberInvited')
  }

  /**
   * Zusagen. Beim Beitritt entsteht hier die Mitgliedschaft, bei einer
   * Anhebung stellt sich der Verwaltungsschalter um — auslösen kann beides
   * nur der Betroffene, deshalb steht beides an derselben Stelle.
   *
   * Das zugesagte Team wird gleich das ausgewählte, sonst bliebe ein Beitritt
   * eine Zeile, die verschwindet. Die Antwort trägt seinen frischen Stand:
   * bei einer Anhebung ist es dasselbe Team wie eben, und ohne sie stünden
   * darunter weiter die alten Schalter.
   */
  const acceptInvitation = async (invitation: TeamInvitation, anhebung: boolean) => {
    await run(async () => {
      const aktualisiert = await teamsApi.acceptInvitation(invitation.team_id)
      setEinladungen((rows) => rows.filter((row) => row.team_id !== invitation.team_id))
      await reloadTeams()
      setSelectedId(invitation.team_id)
      setDetail(aktualisiert)
    }, anhebung ? 'teams.upgradeAccepted' : 'teams.invitationAccepted')
  }

  const declineInvitation = async (invitation: TeamInvitation, anhebung: boolean) => {
    await run(async () => {
      await teamsApi.declineInvitation(invitation.team_id)
      setEinladungen((rows) => rows.filter((row) => row.team_id !== invitation.team_id))
    }, anhebung ? 'teams.upgradeDeclined' : 'teams.invitationDeclined')
  }

  /**
   * Setzt die beiden Schalter eines Mitglieds — und meldet, was wirklich
   * geschehen ist.
   *
   * Zurücknehmen tut das Backend sofort, anheben nicht: ein nachträglich
   * eingeschalteter Schalter macht das Team zum Lernziel der KI des
   * Betroffenen, und darüber entscheidet nur er. Aus der Anhebung wird
   * deshalb eine Einladung; die Antwort führt ihn dann unter `invitations`,
   * die Mitgliederzeile bleibt wie sie war, und der Schalter springt sichtbar
   * zurück. „Gespeichert." wäre genau dort die Unwahrheit.
   */
  const setMemberSwitches = async (
    member: TeamMember, next: { can_manage_skills: boolean; can_manage_memory: boolean },
  ) => {
    if (!detail) return
    await run(async () => {
      const aktualisiert = await teamsApi.updateMember(detail.id, member.user_id, next)
      setDetail(aktualisiert)
      const angefragt = aktualisiert.invitations.some((row) => row.user_id === member.user_id)
      toast.success(angefragt
        ? t('teams.memberConsentPending', { name: member.username })
        : t('teams.memberSaved'))
    })
  }

  const removeMember = async (member: TeamMember) => {
    if (!detail) return
    await run(async () => {
      setDetail(await teamsApi.removeMember(detail.id, member.user_id))
      await reloadTeams()
    }, 'teams.memberRemoved')
  }

  /**
   * Selbst gehen. Das Gegenstück zur Einladung: wer zustimmen muss, um
   * beizutreten, kommt auch ohne fremde Zustimmung wieder heraus — das
   * Backend lässt den eigenen Austritt seit dem 23.08.2026 ausdrücklich zu
   * (`team_service.remove_member`). Danach ist das Team keins mehr von
   * diesem Benutzer, also fällt auch die Auswahl darauf weg.
   */
  const leaveTeam = async (team: TeamDetail) => {
    if (benutzerId === null) return
    if (!await confirm({
      title: t('teams.leaveTitle'),
      message: t('teams.leaveConfirm', { name: team.name }),
      confirmText: t('teams.leave'),
      danger: true,
    })) return
    await run(async () => {
      await teamsApi.removeMember(team.id, benutzerId)
      setSelectedId(null)
      const rows = await reloadTeams()
      setSelectedId(rows.find((row) => !row.is_personal)?.id ?? null)
    }, 'teams.leftTeam')
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

  // Wer schon eingeladen ist, steht nicht noch einmal zur Wahl. Sonst klickt
  // der Gründer denselben Benutzer beliebig oft an und sieht nie einen
  // Unterschied — aufgenommen wird davon niemand, das Backend überschreibt nur
  // dieselbe offene Einladung.
  const memberCandidates = users.filter(
    (user) => !detail?.members.some((member) => member.user_id === user.id)
      && !detail?.invitations.some((invitation) => invitation.user_id === user.id),
  )

  // ── Zwei Angebote in einer Liste ──────────────────────────────────
  // `invitations` führt beides: den Beitritt eines Nichtmitglieds und die
  // Anhebung eines Verwaltungsschalters bei einem, der längst dabei ist. Für
  // den Angefragten ist das ein Unterschied ums Ganze — „noch kein Mitglied"
  // stimmt beim zweiten Fall nicht, und „Team beigetreten." auch nicht.
  // Unterscheiden lässt sich beides an der Mitgliederliste desselben Teams.
  const istMitglied = (invitation: TeamInvitation) =>
    detail?.members.some((member) => member.user_id === invitation.user_id) ?? false
  const offeneBeitritte = detail?.invitations.filter((row) => !istMitglied(row)) ?? []
  const offeneAnhebungen = detail?.invitations.filter(istMitglied) ?? []

  // Dieselbe Trennung von der anderen Seite: steht das Team schon in *meiner*
  // Teamliste, bin ich dort Mitglied — dann ist das Angebot eine Anhebung.
  const meinTeam = (invitation: TeamInvitation) =>
    teams.some((team) => team.id === invitation.team_id)
  const meineBeitritte = einladungen.filter((row) => !meinTeam(row))
  const meineAnhebungen = einladungen.filter(meinTeam)

  return (
    <div className="msm-page space-y-5">
      <PageHeader
        eyebrow={t('teams.eyebrow')}
        title={t('teams.title')}
        description={t('teams.description')}
      />

      {/* ── Zwei Welten, sichtbar getrennt ────────────────────────────
          Vorher stand das persönliche Ein-Mann-Team als ein Eintrag unter den
          anderen im selben Dropdown, unterschieden nur durch den Zusatz
          „persönlich". Es ist aber kein Team, dem man beitritt — es ist der
          eigene Bereich, und man kann daneben beliebig vielen echten Teams
          angehören. Diese Unterscheidung gehört an den Anfang der Seite. */}
      <TabBar
        tabs={BEREICHE}
        active={bereich}
        onChange={setBereich}
        ariaLabel={t('teams.areaLabel')}
      />

      {bereich === 'personal' && (
        <>
          <section className="msm-card p-6" aria-labelledby="personal-title">
            <div className="flex items-center gap-2">
              <User className="h-5 w-5 text-secondary" aria-hidden="true" />
              <h2 id="personal-title" className="font-headline text-lg font-semibold text-on-surface">
                {t('teams.areaPersonal')}
              </h2>
            </div>
            <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">
              {t('teams.personalKnowledgeHint')}
            </p>
            {/* Persönliche Erinnerungen stehen im Profil und nur dort. Hier
                steht der Weg dorthin, nicht eine zweite Ansicht derselben
                Liste — die gab es bis eben, und sie war der Grund, warum
                persönliches Wissen unter „Teams" auftauchte. */}
            <p className="mt-3 max-w-2xl rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-xs leading-5 text-on-surface-variant">
              {t('teams.personalMemoryElsewhere')}{' '}
              <Link to="/profile" className="text-primary underline underline-offset-2">
                {t('teams.personalMemoryLink')}
              </Link>
            </p>
          </section>

          {detail?.is_personal && canUseSkills && <AiSkillManager scope={skillScope(detail)} />}
        </>
      )}

      {/* ── Angebote an dich ──────────────────────────────────────────
          Der Gegenpart zur Einladung: weder eine Mitgliedschaft noch ein
          Verwaltungsschalter entsteht ohne den Betroffenen. Bis eben trug ihn
          der Gründer einfach ein — jetzt ist das hier die Stelle, an der ein
          Team überhaupt erst zustande kommt. Sie steht deshalb vor allem
          anderen im Teambereich.

          Zwei Blöcke, weil zwei Dinge angeboten werden: der Beitritt bringt
          Wissen und Serverrechte des Teams mit, die Anhebung nur einen
          Schalter an einer Mitgliedschaft, die es längst gibt. */}
      {bereich === 'teams' && (
        <>
          <AngebotsBlock
            kennung="team-my-invitations"
            titel={t('teams.myInvitations')}
            hinweis={t('teams.myInvitationsHint')}
            einladungen={meineBeitritte}
            busy={busy}
            onAnnehmen={(invitation) => void acceptInvitation(invitation, false)}
            onAblehnen={(invitation) => void declineInvitation(invitation, false)}
          />
          <AngebotsBlock
            kennung="team-my-upgrades"
            titel={t('teams.myUpgrades')}
            hinweis={t('teams.myUpgradesHint')}
            einladungen={meineAnhebungen}
            busy={busy}
            onAnnehmen={(invitation) => void acceptInvitation(invitation, true)}
            onAblehnen={(invitation) => void declineInvitation(invitation, true)}
          />
        </>
      )}

      {bereich === 'teams' && canCreate && (
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

      {bereich === 'teams' && echte.length === 0 && (
        <section className="msm-card p-6 text-sm text-on-surface-variant">
          {t('teams.noTeams')}
        </section>
      )}

      {bereich === 'teams' && echte.length > 0 && (
        <section className="msm-card p-6" aria-labelledby="team-select">
          <label className="block w-full max-w-sm space-y-1.5">
            <span id="team-select" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('teams.select')}
            </span>
            <Dropdown
              value={selectedId === null ? null : String(selectedId)}
              onChange={(value) => setSelectedId(Number(value))}
              options={echte.map((team) => ({
                value: String(team.id),
                label: team.name,
                hint: t('teams.memberCount', { count: team.member_count }),
              }))}
              disabled={busy}
              aria-label={t('teams.select')}
            />
          </label>
        </section>
      )}

      {/* ── Das geteilte KI-Wissen dieses Teams ───────────────────────
          Erinnerungen und Skills des Teams, nicht die des Benutzers. Dieselben
          Panels wie im Profil bzw. unter „Persönlich", nur mit anderem
          Bereich — eine zweite Ansicht daneben wäre auseinandergelaufen,
          sobald jemand nur eine davon anfasst. */}
      {bereich === 'teams' && detail && !detail.is_personal && (
        <section className="space-y-4" aria-labelledby="team-knowledge">
          <div className="msm-card p-6">
            <h2 id="team-knowledge" className="font-headline text-lg font-semibold text-on-surface">
              {t('teams.knowledge')}
            </h2>
            <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{t('teams.knowledgeHint')}</p>
          </div>
          <AiMemoryManager scope={memoryScope(detail)} />
          {canUseSkills && <AiSkillManager scope={skillScope(detail)} />}
        </section>
      )}

      {bereich === 'teams' && detail && !detail.is_personal && (
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
                      onCheckedChange={(next) => void setMemberSwitches(member, {
                        can_manage_skills: next, can_manage_memory: member.can_manage_memory,
                      })}
                      aria-label={`${t('teams.manageSkills')}: ${member.username}`}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-on-surface-variant">{t('teams.manageMemory')}</span>
                    <Switch
                      checked={member.can_manage_memory}
                      disabled={!detail.is_owner || busy}
                      onCheckedChange={(next) => void setMemberSwitches(member, {
                        can_manage_skills: member.can_manage_skills, can_manage_memory: next,
                      })}
                      aria-label={`${t('teams.manageMemory')}: ${member.username}`}
                    />
                  </div>
                  {/* Derselbe Platz, zwei Bedeutungen: der Gründer entlässt
                      hier ein Mitglied, jedes Mitglied geht hier selbst.
                      Ausgenommen bleibt nur der Gründer — sein Konto ist die
                      Obergrenze für alles, was das Team weitergibt. Ohne den
                      Austritt käme niemand mehr heraus: hinein führt seit dem
                      23.08.2026 nur die eigene Zusage, und die gilt für
                      immer. */}
                  {member.user_id === benutzerId && member.role !== 'owner' && (
                    <Button
                      type="button" variant="ghost" size="sm" disabled={busy}
                      onClick={() => void leaveTeam(detail)}
                      aria-label={t('teams.leave')}
                    >
                      <LogOut className="h-4 w-4" aria-hidden="true" />
                      {t('teams.leave')}
                    </Button>
                  )}
                  {detail.is_owner && member.user_id !== benutzerId && member.role !== 'owner' && (
                    <Button
                      type="button" variant="ghost" size="sm" disabled={busy}
                      onClick={() => void removeMember(member)}
                      aria-label={`${t('teams.removeMember')}: ${member.username}`}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>

            {/* Offene Angebote stehen sichtbar neben den Mitgliedern, aber
                nicht in derselben Liste. Die gestrichelte Linie sagt das ohne
                Worte — bei den Eingeladenen, weil sie noch keine Mitglieder
                sind, bei den Anhebungen, weil der Schalter noch nicht gilt.
                Zurücknehmen kann der Gründer beides nicht: die Entscheidung
                gehört dem, den sie betrifft. */}
            {detail.is_owner && (
              <>
                <OffeneGruppe
                  kennung="team-pending-invitations"
                  titel={t('teams.pendingInvitations')}
                  status={t('teams.invitationPending')}
                  hinweis={t('teams.pendingInvitationsHint')}
                  einladungen={offeneBeitritte}
                />
                <OffeneGruppe
                  kennung="team-pending-upgrades"
                  titel={t('teams.pendingUpgrades')}
                  status={t('teams.upgradePending')}
                  hinweis={t('teams.pendingUpgradesHint')}
                  einladungen={offeneAnhebungen}
                />
              </>
            )}

            {detail.is_owner && memberCandidates.length > 0 && (
              <div className="mt-4 flex flex-wrap items-end gap-3">
                <label className="min-w-[14rem] flex-1 space-y-1.5">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('teams.inviteMember')}
                  </span>
                  <Dropdown
                    value={newMemberId}
                    onChange={setNewMemberId}
                    options={memberCandidates.map((user) => ({ value: String(user.id), label: user.username }))}
                    placeholder={t('teams.selectUser')}
                    disabled={busy}
                    aria-label={t('teams.inviteMember')}
                  />
                </label>
                <Button type="button" disabled={busy || !newMemberId} onClick={() => void inviteMember()}>
                  <UserPlus className="h-4 w-4" aria-hidden="true" />
                  {t('teams.inviteMember')}
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
                        options={server.permission_keys.map((key) => ({
                          value: key,
                          label: t(`permissionDetails.${key.replace(/\./g, '_')}.title`, { defaultValue: key }),
                        }))}
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

/**
 * Was ein Angebot mitbringt. Wer zusagt, soll sehen, wozu — und der Gründer,
 * was er angeboten hat: der Schalter in der Mitgliederzeile springt bis zur
 * Zusage zurück und verrät es nicht.
 */
function AngeboteneSchalter({ invitation }: { invitation: TeamInvitation }) {
  const { t } = useTranslation()
  if (!invitation.can_manage_skills && !invitation.can_manage_memory) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      {invitation.can_manage_skills && (
        <span className="rounded-full bg-surface-container-high px-2.5 py-1 text-xs text-on-surface-variant">
          {t('teams.manageSkills')}
        </span>
      )}
      {invitation.can_manage_memory && (
        <span className="rounded-full bg-surface-container-high px-2.5 py-1 text-xs text-on-surface-variant">
          {t('teams.manageMemory')}
        </span>
      )}
    </div>
  )
}

/**
 * Ein Block eigener Angebote — Beitritte oder Anhebungen.
 *
 * Zu entscheiden ist beidesmal dasselbe (annehmen oder ablehnen), verschieden
 * ist nur, worüber: der Titel und der Hinweis darüber sagen es, die Zeilen
 * sehen deshalb gleich aus.
 */
function AngebotsBlock({
  kennung, titel, hinweis, einladungen, busy, onAnnehmen, onAblehnen,
}: {
  kennung: string
  titel: string
  hinweis: string
  einladungen: TeamInvitation[]
  busy: boolean
  onAnnehmen: (invitation: TeamInvitation) => void
  onAblehnen: (invitation: TeamInvitation) => void
}) {
  const { t } = useTranslation()
  if (einladungen.length === 0) return null

  return (
    <section className="msm-card p-6" aria-labelledby={kennung}>
      <div className="flex items-center gap-2">
        <Mail className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 id={kennung} className="font-headline text-lg font-semibold text-on-surface">
          {titel}
        </h2>
      </div>
      <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{hinweis}</p>

      <ul className="mt-4 space-y-2">
        {einladungen.map((invitation) => (
          <li
            key={invitation.team_id}
            className="flex flex-wrap items-center gap-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4"
          >
            <span className="min-w-[10rem] flex-1 text-sm text-on-surface">
              <span className="font-medium">{invitation.team_name}</span>
              {invitation.invited_by_username && (
                <span className="ml-2 text-xs text-on-surface-variant">
                  {t('teams.invitedBy', { name: invitation.invited_by_username })}
                </span>
              )}
            </span>
            <AngeboteneSchalter invitation={invitation} />
            <div className="flex items-center gap-2">
              <Button
                type="button" size="sm" disabled={busy}
                onClick={() => onAnnehmen(invitation)}
                aria-label={`${t('teams.acceptInvitation')}: ${invitation.team_name}`}
              >
                <Check className="h-4 w-4" aria-hidden="true" />
                {t('teams.acceptInvitation')}
              </Button>
              <Button
                type="button" variant="ghost" size="sm" disabled={busy}
                onClick={() => onAblehnen(invitation)}
                aria-label={`${t('teams.declineInvitation')}: ${invitation.team_name}`}
              >
                <X className="h-4 w-4" aria-hidden="true" />
                {t('teams.declineInvitation')}
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Die Gegenseite: was der Gründer ausgesprochen hat und was darauf noch fehlt. */
function OffeneGruppe({
  kennung, titel, status, hinweis, einladungen,
}: {
  kennung: string
  titel: string
  status: string
  hinweis: string
  einladungen: TeamInvitation[]
}) {
  if (einladungen.length === 0) return null

  return (
    <div className="mt-5">
      <h3 id={kennung} className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
        {titel}
      </h3>
      <ul className="mt-2 space-y-2" aria-labelledby={kennung}>
        {einladungen.map((invitation) => (
          <li
            key={invitation.user_id}
            className="flex flex-wrap items-center gap-4 rounded-xl border border-dashed border-outline-variant/50 p-4"
          >
            <span className="min-w-[8rem] flex-1 text-sm font-medium text-on-surface">
              {invitation.username}
            </span>
            <AngeboteneSchalter invitation={invitation} />
            <span className="text-xs text-on-surface-variant">{status}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 max-w-3xl text-xs text-on-surface-variant">{hinweis}</p>
    </div>
  )
}
