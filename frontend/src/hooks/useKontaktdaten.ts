/**
 * Was die Seitenleiste des Messengers kennt: Freunde, Gruppen, Teammitglieder,
 * öffentliche Profile, Direktchats und Stories.
 *
 * Geladen wird beim Öffnen, danach alle 15 Sekunden und sofort nach dem
 * Entsperren. Ein offener Abzug in `localStorage` lässt die Liste beim nächsten
 * Öffnen ohne Wartezeit erscheinen; was darin **nicht** stehen darf, steht bei
 * `laden`. Bis 09/2026 stand das in `Messenger.tsx`.
 */

import { useEffect, useState } from 'react'

import {
  getFriends,
  getGroups,
  getPublicProfiles,
  getStories,
  type ChatGroupItem,
  type ChatStoryItem,
  type DirectChatItem,
  type FriendItem,
  type PublicProfileResponse,
} from '@/api/social'
import { teamsApi, type TeamMember } from '@/api/teams'
import { gespraechsListe } from '@/services/gespraechsListe'
import { benenneGruppen } from '@/services/gruppenName'

const CONTACTS_CACHE_KEY = 'msm:chat_contacts_cache'
const TAKT_MS = 15000

export type TeamKontakt = { member: TeamMember; teamName: string }

function loadInitialContactsCache(): {
  friends: FriendItem[]
  groups: ChatGroupItem[]
  teamMembers: TeamKontakt[]
  publicUsers: PublicProfileResponse[]
  stories: ChatStoryItem[]
  directChats: DirectChatItem[]
} {
  if (typeof window === 'undefined') {
    return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [], directChats: [] }
  }
  try {
    const raw = localStorage.getItem(CONTACTS_CACHE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        friends: Array.isArray(parsed.friends) ? parsed.friends : [],
        groups: Array.isArray(parsed.groups) ? parsed.groups : [],
        teamMembers: Array.isArray(parsed.teamMembers) ? parsed.teamMembers : [],
        publicUsers: Array.isArray(parsed.publicUsers) ? parsed.publicUsers : [],
        stories: Array.isArray(parsed.stories) ? parsed.stories : [],
        directChats: Array.isArray(parsed.directChats) ? parsed.directChats : [],
      }
    }
  } catch {}
  return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [], directChats: [] }
}

/**
 * @param messengerGesperrt Steht in den Abhängigkeiten, damit das Entsperren
 *   sofort neu lädt: die Gruppennamen liegen versiegelt und sind vorher nicht
 *   zu haben. Ohne das stünde bis zum nächsten Takt, bis zu 15 Sekunden,
 *   überall „Verschlüsselte Gruppe", obwohl die PIN längst eingegeben ist.
 */
export function useKontaktdaten(currentUserId: number, messengerGesperrt: boolean) {
  const [initialCache] = useState(loadInitialContactsCache)
  const [friends, setFriends] = useState<FriendItem[]>(initialCache.friends)
  const [groups, setGroups] = useState<ChatGroupItem[]>(initialCache.groups)
  const [teamMembers, setTeamMembers] = useState<TeamKontakt[]>(initialCache.teamMembers)
  const [publicUsers, setPublicUsers] = useState<PublicProfileResponse[]>(initialCache.publicUsers)
  const [directChats, setDirectChats] = useState<DirectChatItem[]>(initialCache.directChats)
  const [stories, setStories] = useState<ChatStoryItem[]>(initialCache.stories)

  const laden = async () => {
    try {
      const [friendsData, groupsData, teamsData, storiesData, publicData, directChatsData] = await Promise.all([
        getFriends().catch(() => []),
        getGroups().catch(() => []),
        teamsApi.list().catch(() => []),
        getStories().catch(() => []),
        getPublicProfiles().catch(() => []),
        // Kein Netzaufruf mehr: seit Stufe 6b weiss der Server nicht, mit wem
        // dieses Konto schreibt. Die Liste liegt versiegelt auf diesem Gerät.
        gespraechsListe().catch(() => []),
      ])
      setFriends(friendsData)
      // Der Server liefert für Gruppen seit Stufe 6 keinen Namen mehr. Diese
      // eine Zeile setzt ihn aus dem versiegelten örtlichen Speicher wieder
      // ein — bewusst hier an der Liste und nicht an jeder Anzeige einzeln.
      setGroups(await benenneGruppen(groupsData).catch(() => groupsData))
      setStories(storiesData)
      setPublicUsers(publicData)
      setDirectChats(directChatsData)

      const teamDetails = await Promise.all(
        teamsData.map(async (t) => {
          try {
            const detail = await teamsApi.get(t.id)
            return { team: t, detail }
          } catch {
            return { team: t, detail: null }
          }
        })
      )

      const membersList: TeamKontakt[] = []
      for (const { team, detail } of teamDetails) {
        if (detail && detail.members) {
          for (const m of detail.members) {
            if (m.user_id !== currentUserId) {
              membersList.push({ member: m, teamName: team.name })
            }
          }
        }
      }
      setTeamMembers(membersList)

      // Lokalen Cache für sofortiges 0ms-Laden beim nächsten Aufruf speichern
      try {
        localStorage.setItem(
          CONTACTS_CACHE_KEY,
          JSON.stringify({
            friends: friendsData,
            // Bewusst `groupsData` und nicht die benannte Liste: dieser Cache
            // liegt offen in `localStorage`. Ein Gruppenname darin wäre genau
            // die Zeile, die Stufe 6 aus der Datenbank entfernt hat, nur auf
            // einer anderen Platte. Die Namen stehen versiegelt in
            // `msm:gruppennamen` und kommen beim nächsten `benenneGruppen`.
            groups: groupsData,
            teamMembers: membersList,
            publicUsers: publicData,
            stories: storiesData,
            // Und aus demselben Grund gar nicht: die Gesprächsliste ist die
            // Auskunft „mit wem schreibt dieser Mensch". Sie liegt versiegelt
            // in `msm:gespraeche` und kommt von dort beim nächsten Laden —
            // ein offener Abzug daneben machte die Versiegelung sinnlos.
            directChats: [],
          })
        )
      } catch {}
    } catch {
      // Offline fallback
    }
  }

  useEffect(() => {
    laden()
    const interval = setInterval(laden, TAKT_MS)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUserId, messengerGesperrt])

  return {
    friends,
    groups,
    /** Für örtliche Änderungen zwischen zwei Takten: Umbenennen, Logo, Rechte. */
    setGroups,
    teamMembers,
    publicUsers,
    directChats,
    stories,
    /** Für eine eben erstellte oder gelöschte Story. */
    setStories,
    laden,
  }
}
