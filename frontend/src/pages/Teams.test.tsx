import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { teamsApi, type Team, type TeamDetail, type TeamInvitation } from '@/api/teams'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { toast } from '@/stores/toastStore'
import type { User } from '@/types'
import { Teams } from './Teams'

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    inviteMember: vi.fn(),
    updateMember: vi.fn(),
    removeMember: vi.fn(),
    setServerGrants: vi.fn(),
    assignableServers: vi.fn(),
    invitations: vi.fn(),
    acceptInvitation: vi.fn(),
    declineInvitation: vi.fn(),
  },
}))

// Diese Datei prueft die Teamseite, nicht das Gedaechtnis — der eine Mock
// steht fuer alles, was die eingebetteten KI-Bauteile nebenher holen. Seit dem
// 19.08.2026 antwortet der Gedaechtnisweg aber mit einer **Seite** und nicht
// mehr mit einer Liste (ein Teambereich fasst jetzt bis zu 5.000 Eintraege,
// und jede Zeile kostet beim Oeffnen eine Entschluesselung). Ein pauschales
// `[]` liess `AiMemoryManager` deshalb an `seite.entries` auflaufen und nahm
// die halbe Seite mit — sichtbar als vier Tests, die ihre Beschriftungen nicht
// mehr fanden.
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  const leereSeite = { entries: [], total: 0, clearable: 0, limit: 200 }
  return {
    ...actual,
    api: vi.fn().mockImplementation(async (pfad: string) =>
      typeof pfad === 'string' && pfad.includes('/ai/memory/') ? leereSeite : []
    ),
  }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const personal: Team = {
  id: 1, name: 'einmalmaik', is_personal: true, owner_user_id: 1, is_owner: true,
  can_manage_skills: true, can_manage_memory: true, member_count: 1,
  created_at: '2026-08-09T10:00:00Z',
}

const real: Team = { ...personal, id: 2, name: 'Betrieb', is_personal: false, member_count: 2 }

const personalDetail: TeamDetail = { ...personal, members: [], servers: [], invitations: [] }

/** Der offen Eingeladene: angeschrieben, aber kein Mitglied. */
const offeneEinladung: TeamInvitation = {
  team_id: 2, team_name: 'Betrieb', user_id: 3, username: 'neuer',
  invited_by_username: 'einmalmaik', can_manage_skills: false, can_manage_memory: false,
  invited_at: '2026-08-23T10:00:00Z',
}

const realDetail: TeamDetail = {
  ...real,
  members: [
    { user_id: 1, username: 'einmalmaik', role: 'owner', can_manage_skills: true, can_manage_memory: true, joined_at: '2026-08-09T10:00:00Z' },
    { user_id: 2, username: 'kollege', role: 'member', can_manage_skills: false, can_manage_memory: false, joined_at: '2026-08-09T10:00:00Z' },
  ],
  servers: [{ server_id: 7, server_name: 'Valheim', permission_keys: ['server.view'] }],
  invitations: [offeneEinladung],
}

/** Eine Einladung an den angemeldeten Benutzer selbst — sein Weg ins Team. */
const eigeneEinladung: TeamInvitation = {
  team_id: 5, team_name: 'Nachtschicht', user_id: 1, username: 'einmalmaik',
  invited_by_username: 'chefin', can_manage_skills: true, can_manage_memory: false,
  invited_at: '2026-08-23T11:00:00Z',
}

/**
 * Ein Angebot an ein **Mitglied**: „Betrieb" steht schon in seiner Teamliste,
 * beitreten muss er also nichts mehr. Angeboten wird nur der Schalter — das
 * Backend legt dafür seit dem 23.08.2026 dieselbe Zeile an wie für einen
 * Beitritt, und genau deshalb muss die Oberfläche beides auseinanderhalten.
 */
const eigeneAnhebung: TeamInvitation = {
  team_id: 2, team_name: 'Betrieb', user_id: 1, username: 'einmalmaik',
  invited_by_username: 'chefin', can_manage_skills: true, can_manage_memory: false,
  invited_at: '2026-08-23T12:00:00Z',
}

/**
 * Die Antwort auf eine angehobene Schalterstellung: das Backend hat **nichts**
 * gesetzt, sondern gefragt. „kollege" steht unverändert in der
 * Mitgliederliste und zusätzlich unter den Einladungen.
 */
const anhebungDetail: TeamDetail = {
  ...realDetail,
  invitations: [{
    team_id: 2, team_name: 'Betrieb', user_id: 2, username: 'kollege',
    invited_by_username: 'einmalmaik', can_manage_skills: true, can_manage_memory: false,
    invited_at: '2026-08-23T12:00:00Z',
  }],
}

/** Beides nebeneinander: ein Beitritt („neuer") und eine Anhebung („kollege"). */
const gemischtDetail: TeamDetail = {
  ...realDetail,
  invitations: [offeneEinladung, ...anhebungDetail.invitations],
}

/**
 * Alle Benutzer des Panels: zwei Mitglieder, ein bereits Eingeladener
 * („neuer") und einer, den noch niemand gefragt hat („fremde"). Nur mit beiden
 * Nichtmitgliedern ist überhaupt prüfbar, dass die Auswahl den Eingeladenen
 * aussortiert und nicht einfach alles.
 */
const alleBenutzer = [
  { id: 1, username: 'einmalmaik' },
  { id: 2, username: 'kollege' },
  { id: 3, username: 'neuer' },
  { id: 4, username: 'fremde' },
]

function renderTeams() {
  return render(<MemoryRouter><Teams /></MemoryRouter>)
}

/** Wechselt in den Teambereich — die Seite startet bewusst bei „Persönlich". */
async function zuTeams() {
  fireEvent.click(await screen.findByRole('tab', { name: 'Teams' }))
}

/**
 * Klappt ein Dropdown über sein aria-label auf. Die Optionen hängen per Portal
 * am Body und existieren erst im geöffneten Zustand — anders kommt man nicht an
 * sie heran.
 */
async function oeffneAuswahl(label: string) {
  const knopf = (await screen.findAllByLabelText(label))
    .find((element) => element.getAttribute('aria-haspopup') === 'listbox')
  expect(knopf).toBeDefined()
  fireEvent.click(knopf as HTMLElement)
}

/**
 * Vier Zusicherungen liegen hier übereinander.
 *
 * Die ältere ist die Obergrenze: die Serverauswahl darf nur zeigen, was der
 * Gründer selbst direkt hält — dieselbe Grenze, die `permission_service` bei
 * jedem Zugriff durchsetzt.
 *
 * Die neuere ist die Trennung: persönliches Wissen und Teamwissen sind zwei
 * Welten. Unter „Persönlich" stehen die eigenen Skills und **kein**
 * Gedächtnis — persönliche Erinnerungen gehören ins Profil, und bis vor Kurzem
 * zeigte diese Seite genau dieselbe Liste noch einmal.
 *
 * Die dritte ist die Einladung: eine Mitgliedschaft entsteht nie ohne den
 * Betroffenen. Der Gründer lädt ein, beitreten muss der Eingeladene — und die
 * Oberfläche muss beide Hälften zeigen, sonst meldet sie eine Aufnahme, die
 * nie stattfand, und dem Eingeladenen fehlt jeder Weg zuzusagen.
 *
 * Die jüngste zieht dieselbe Linie zweimal weiter: auch ein *angehobener*
 * Verwaltungsschalter braucht die Zusage des Betroffenen — die Meldung darf
 * ihn also nicht als gespeichert ausgeben, und die beiden Angebotsarten
 * gehören auseinander. Und wer nur mit eigener Zusage hineinkommt, muss ohne
 * fremde wieder hinaus: der Austritt gehört in jede eigene Zeile.
 */
describe('Teams', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    // Wer angemeldet ist, entscheidet über eine Zeile der Mitgliederliste:
    // die eigene trägt „Team verlassen" statt „Mitglied entfernen".
    useAuthStore.setState({
      user: { id: 1, username: 'einmalmaik' } as User, isAuthenticated: true, isLoading: false,
    })
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        // `users.read` gehört dazu, seit die Einladungsauswahl geprüft wird:
        // ohne dieses Recht holt die Seite die Benutzerliste gar nicht erst,
        // und die Auswahl, um die es geht, entsteht nie.
        global_keys: ['teams.create', 'ai.skills.use', 'ai.memory.use', 'users.read'],
        server_keys: {},
      },
      isLoading: false, error: null,
    })
    // Die Benutzerliste kommt aus `/admin/users` und muss echte Namen liefern:
    // ohne sie gäbe es keine Auswahl, gegen die die Einladungsfilterung
    // überhaupt prüfbar wäre.
    vi.mocked(api).mockImplementation((async (pfad: string) => {
      if (typeof pfad === 'string' && pfad.includes('/ai/memory/')) {
        return { entries: [], total: 0, clearable: 0, limit: 200 }
      }
      if (pfad === '/admin/users') return alleBenutzer
      return []
    }) as never)
    vi.mocked(teamsApi.list).mockReset().mockResolvedValue([real, personal])
    vi.mocked(teamsApi.get).mockReset().mockImplementation(async (id: number) =>
      (id === 1 ? personalDetail : realDetail))
    vi.mocked(teamsApi.assignableServers).mockReset().mockResolvedValue([
      { server_id: 7, server_name: 'Valheim', permission_keys: ['server.view', 'server.start'] },
    ])
    vi.mocked(teamsApi.updateMember).mockReset().mockResolvedValue(realDetail)
    vi.mocked(teamsApi.inviteMember).mockReset().mockResolvedValue(realDetail)
    vi.mocked(teamsApi.invitations).mockReset().mockResolvedValue([])
    vi.mocked(teamsApi.acceptInvitation).mockReset().mockResolvedValue(realDetail)
    vi.mocked(teamsApi.declineInvitation).mockReset().mockResolvedValue(undefined)
    vi.mocked(teamsApi.removeMember).mockReset().mockResolvedValue(realDetail)
    vi.mocked(confirm).mockReset().mockResolvedValue(true)
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
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

  it('meldet eine Anhebung als Anfrage — nicht als Speicherung', async () => {
    // Das Backend nimmt eine Anhebung seit dem 23.08.2026 nicht mehr direkt
    // an: es legt eine Einladung an, die der Betroffene annehmen muss. Der
    // Schalter springt danach sichtbar auf aus zurück — „Gespeichert." wäre
    // an dieser Stelle schlicht gelogen.
    vi.mocked(teamsApi.updateMember).mockResolvedValue(anhebungDetail)
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    fireEvent.click(await screen.findByLabelText('Skills verwalten: kollege'))

    await waitFor(() => expect(teamsApi.updateMember).toHaveBeenCalledWith(2, 2, {
      can_manage_skills: true, can_manage_memory: false,
    }))
    await waitFor(() => expect(toast.success)
      .toHaveBeenCalledWith('Zustimmung angefragt — kollege muss annehmen.'))
    expect(toast.success).not.toHaveBeenCalledWith('Gespeichert.')
  })

  it('meldet ein Zurücknehmen weiter als Speicherung', async () => {
    // Die Gegenprobe: heruntersetzen darf der Gründer allein, das Backend tut
    // es sofort und führt niemanden unter `invitations`. Ohne diesen Fall
    // wäre die neue Meldung einfach die einzige.
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    fireEvent.click(await screen.findByLabelText('Memory verwalten: einmalmaik'))

    await waitFor(() => expect(teamsApi.updateMember).toHaveBeenCalledWith(2, 1, {
      can_manage_skills: true, can_manage_memory: false,
    }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Gespeichert.'))
  })

  it('lädt ein, statt aufzunehmen — und meldet auch nichts anderes', async () => {
    // Für diesen Fall darf noch niemand eingeladen sein, sonst gäbe es keine
    // Auswahl mehr.
    vi.mocked(teamsApi.get).mockImplementation(async (id: number) =>
      (id === 1 ? personalDetail : { ...realDetail, invitations: [] }))
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    await oeffneAuswahl('Mitglied einladen')
    fireEvent.click(await screen.findByRole('option', { name: 'neuer' }))
    const absenden = screen.getAllByRole('button', { name: 'Mitglied einladen' })
      .find((element) => element.getAttribute('aria-haspopup') !== 'listbox')
    fireEvent.click(absenden as HTMLElement)

    await waitFor(() => expect(teamsApi.inviteMember).toHaveBeenCalledWith(2, {
      user_id: 3, can_manage_skills: false, can_manage_memory: false,
    }))
    // Das Backend legt seit dem 23.08.2026 nur eine Einladung an. Die Meldung
    // „Mitglied aufgenommen." war danach schlicht falsch: aufgenommen wurde
    // niemand.
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Benutzer eingeladen.'))
  })

  it('bietet einen bereits Eingeladenen nicht noch einmal zur Wahl an', async () => {
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    // „neuer" hat eine offene Einladung, „fremde" nicht. Bliebe „neuer"
    // wählbar, klickte der Gründer dieselbe Einladung beliebig oft und sähe
    // nie einen Unterschied.
    await oeffneAuswahl('Mitglied einladen')
    const optionen = (await screen.findAllByRole('option')).map((element) => element.textContent)
    expect(optionen).toContain('fremde')
    expect(optionen).not.toContain('neuer')
  })

  it('führt Eingeladene getrennt von den Mitgliedern', async () => {
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    expect(await screen.findByText('Offene Einladungen')).toBeInTheDocument()
    expect(screen.getByText('neuer')).toBeInTheDocument()
    expect(screen.getByText('wartet auf Zusage')).toBeInTheDocument()
    // Ein Eingeladener ist kein Mitglied: er hat keine Verwaltungsschalter,
    // und man kann ihn auch nicht entfernen.
    expect(screen.queryByLabelText('Skills verwalten: neuer')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Mitglied entfernen: neuer')).not.toBeInTheDocument()
  })

  it('trennt offene Einladungen von offenen Rechteanfragen', async () => {
    // `invitations` führt beides. Standen beide unter „Offene Einladungen",
    // las der Gründer über „kollege" den Satz, Eingeladene seien noch keine
    // Mitglieder — über jemanden, der seit Tagen im Team ist.
    vi.mocked(teamsApi.get).mockImplementation(async (id: number) =>
      (id === 1 ? personalDetail : gemischtDetail))
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    const einladungen = await screen.findByRole('list', { name: 'Offene Einladungen' })
    expect(within(einladungen).getByText('neuer')).toBeInTheDocument()
    expect(within(einladungen).queryByText('kollege')).not.toBeInTheDocument()

    const anfragen = await screen.findByRole('list', { name: 'Offene Rechteanfragen' })
    expect(within(anfragen).getByText('kollege')).toBeInTheDocument()
    // Was angefragt ist, steht daneben — der Schalter in der Mitgliederzeile
    // steht bis zur Zusage weiter auf aus und verrät es nicht.
    expect(within(anfragen).getByText('Skills verwalten')).toBeInTheDocument()
    expect(within(anfragen).getByText('wartet auf Zustimmung')).toBeInTheDocument()
    expect(screen.getByText(/Diese Mitglieder sind längst im Team/)).toBeInTheDocument()
  })

  it('trennt die eigenen Beitritte von den eigenen Rechteanfragen', async () => {
    // „Nachtschicht" gehört ihm noch nicht, „Betrieb" längst. Beides kam bis
    // eben im selben Block mit demselben Hinweis — und eine angenommene
    // Anhebung meldete „Team beigetreten.".
    vi.mocked(teamsApi.invitations).mockResolvedValue([eigeneEinladung, eigeneAnhebung])
    renderTeams()

    const beitritte = await screen.findByRole('region', { name: 'Einladungen an dich' })
    expect(within(beitritte).getByText('Nachtschicht')).toBeInTheDocument()
    expect(within(beitritte).queryByText('Betrieb')).not.toBeInTheDocument()

    const anfragen = await screen.findByRole('region', { name: 'Rechteanfragen an dich' })
    expect(within(anfragen).getByText('Betrieb')).toBeInTheDocument()
    expect(within(anfragen).getByText(/Diesen Teams gehörst du bereits an/)).toBeInTheDocument()

    fireEvent.click(within(anfragen).getByLabelText('Annehmen: Betrieb'))

    await waitFor(() => expect(teamsApi.acceptInvitation).toHaveBeenCalledWith(2))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Zustimmung erteilt.'))
    expect(toast.success).not.toHaveBeenCalledWith('Team beigetreten.')
  })

  it('lässt ein Mitglied selbst gehen', async () => {
    // Hinein führt seit dem 23.08.2026 nur die eigene Zusage — ohne den
    // Austritt käme niemand mehr heraus, denn der Knopf hing am Gründer.
    useAuthStore.setState({
      user: { id: 2, username: 'kollege' } as User, isAuthenticated: true, isLoading: false,
    })
    const alsMitglied: TeamDetail = { ...realDetail, is_owner: false, invitations: [] }
    vi.mocked(teamsApi.list).mockResolvedValue([{ ...real, is_owner: false }, personal])
    vi.mocked(teamsApi.get).mockImplementation(async (id: number) =>
      (id === 1 ? personalDetail : alsMitglied))
    vi.mocked(teamsApi.removeMember).mockResolvedValue(alsMitglied)
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    // Fremde entlässt er nicht — nur sich selbst.
    expect(screen.queryByLabelText('Mitglied entfernen: einmalmaik')).not.toBeInTheDocument()
    fireEvent.click(await screen.findByLabelText('Team verlassen'))

    // Dieselbe Rückfrage wie beim Teamlöschen: danach ist das Wissen des
    // Teams weg, und zurück geht es nur über eine neue Einladung.
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    await waitFor(() => expect(teamsApi.removeMember).toHaveBeenCalledWith(2, 2))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Du hast das Team verlassen.'))
  })

  it('bietet dem Gründer kein Verlassen an', async () => {
    renderTeams()
    await zuTeams()
    await screen.findByText('Betrieb')

    // Der Gründer bleibt im Team — sein Konto ist die Obergrenze für alles,
    // was das Team weitergibt. Mitglieder entlässt er weiterhin.
    expect(screen.queryByLabelText('Team verlassen')).not.toBeInTheDocument()
    expect(await screen.findByLabelText('Mitglied entfernen: kollege')).toBeInTheDocument()
  })

  it('zeigt die eigene Einladung und tritt auf Zusage bei', async () => {
    vi.mocked(teamsApi.invitations).mockResolvedValue([eigeneEinladung])
    renderTeams()

    // Ohne Klick auf „Teams": wer eine offene Einladung hat, soll sie sehen.
    // Genau der Benutzer, der noch keinem Team angehört, fände unter
    // „Persönlich" sonst nur den Hinweis, dass er keinem Team angehört.
    expect(await screen.findByText('Nachtschicht')).toBeInTheDocument()
    expect(screen.getByText('eingeladen von chefin')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Annehmen: Nachtschicht'))

    await waitFor(() => expect(teamsApi.acceptInvitation).toHaveBeenCalledWith(5))
    await waitFor(() => expect(screen.queryByText('Nachtschicht')).not.toBeInTheDocument())
  })

  it('lehnt eine Einladung ab, ohne beizutreten', async () => {
    vi.mocked(teamsApi.invitations).mockResolvedValue([eigeneEinladung])
    renderTeams()

    fireEvent.click(await screen.findByLabelText('Ablehnen: Nachtschicht'))

    await waitFor(() => expect(teamsApi.declineInvitation).toHaveBeenCalledWith(5))
    expect(teamsApi.acceptInvitation).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('Nachtschicht')).not.toBeInTheDocument())
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
