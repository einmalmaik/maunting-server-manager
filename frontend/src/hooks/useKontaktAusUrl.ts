/**
 * Ein Gespräch aus der Adresse öffnen — aber nur, wenn es eines gibt.
 *
 * Der Block stand bis 10/2026 mitten in `Messenger.tsx`, zwischen acht anderen
 * Effekten. Herausgelöst ist er unverändert: dieselbe Reihenfolge, dieselben
 * Abbrüche, dieselbe Aufräumung von Adresse und `sessionStorage`.
 *
 * **Warum die Prüfung überhaupt existiert.** Bis 10/2026 wurde jede unbekannte
 * `userId` zu „Benutzer #<id>", versiegelt als Gespräch gespeichert und war
 * danach nicht mehr zu löschen. Ein gelöschtes Konto, ein alter Deep-Link oder
 * eine verwechselte Kennung hinterließ einen Chat, den es nie gab.
 *
 * Jetzt gilt die Reihenfolge aus der Privatsphäre: ein **bestehendes**
 * Gespräch darf bleiben (der Server erlaubt das Antworten unabhängig vom
 * Profil), ein **Freund** oder ein **öffentliches** Profil darf geöffnet
 * werden, alles andere fällt raus. Gefragt wird erst, wenn die Kontaktliste
 * geladen ist — sonst verwürfe die Seite einen Freund, der nur noch nicht
 * angekommen ist.
 *
 * Dieselbe Zurückhaltung gilt für Gruppen: eine `groupId`, die in der Liste
 * fehlt, öffnet nichts. Ein Gruppenname lässt sich nicht ermitteln, und ein
 * leerer Rahmen wäre nur ein weiterer Geist.
 */

import { useCallback, useEffect, useRef } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import type { SetURLSearchParams } from 'react-router-dom'

import type { ChatGroupItem } from '@/api/social'
import { getProfile } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { PresenceStatus } from '@/components/social/StatusIndicator'
import { ladeGespraeche } from '@/services/gespraechsListe'

export interface KontaktAusUrlOptionen {
  /** `userId` oder `contact` aus der Adresse, sonst die gemerkte Kennung. */
  queryUserId: string | null
  /** `groupId` aus der Adresse, sonst die gemerkte Kennung. */
  queryGroupId: string | null
  kontakte: readonly ChatContact[]
  gruppen: readonly ChatGroupItem[]
  /** Erst danach wird eine **unbekannte** Kennung geprüft. */
  kontakteGeladen: boolean
  activeContact: ChatContact | null
  activeGroup: ChatGroupItem | null
  setActiveContact: Dispatch<SetStateAction<ChatContact | null>>
  setActiveGroup: Dispatch<SetStateAction<ChatGroupItem | null>>
  searchParams: URLSearchParams
  setSearchParams: SetURLSearchParams
  t: (schluessel: string) => string
}

export interface KontaktAusUrl {
  /**
   * Nimmt eine unbrauchbare `userId` aus Adresse und Sitzung.
   *
   * Ohne diesen Schritt blieb die Kennung in der URL stehen, der Effekt lief
   * bei jeder Kontaktaktualisierung erneut, und die Seite baute denselben
   * Geist wieder auf. `sessionStorage` gehört dazu: von dort kam die Kennung
   * beim nächsten Öffnen sonst zurück.
   */
  verwerfeUnbekanntenKontakt: (unbrauchbar?: number) => void
}

export function useKontaktAusUrl(optionen: KontaktAusUrlOptionen): KontaktAusUrl {
  const {
    queryUserId,
    queryGroupId,
    kontakte,
    gruppen,
    kontakteGeladen,
    activeContact,
    activeGroup,
    setActiveContact,
    setActiveGroup,
    searchParams,
    setSearchParams,
    t,
  } = optionen

  /** Welche Kennung bereits geprüft wurde — sonst liefe die Anfrage je Render. */
  const gepruefterKontaktRef = useRef<number | null>(null)

  const verwerfeUnbekanntenKontakt = useCallback(
    (unbrauchbar?: number) => {
      // Zwischen Prüfung und Antwort kann der Mensch einen anderen Chat
      // geöffnet haben. Dann gehört dessen Auswahl nicht weggeräumt.
      if (unbrauchbar !== undefined) {
        setActiveContact((prev) => (prev?.userId === unbrauchbar ? null : prev))
        try {
          if (sessionStorage.getItem('msm:active_messenger_user_id') === String(unbrauchbar)) {
            sessionStorage.removeItem('msm:active_messenger_user_id')
          }
        } catch {}
      }

      const next = new URLSearchParams(searchParams)
      let geaendert = false
      for (const key of ['userId', 'contact']) {
        const wert = next.get(key)
        if (wert === null) continue
        if (unbrauchbar === undefined || Number(wert) === unbrauchbar) {
          next.delete(key)
          geaendert = true
        }
      }
      if (geaendert) setSearchParams(next, { replace: true })
    },
    [searchParams, setSearchParams, setActiveContact],
  )

  useEffect(() => {
    if (!queryUserId || activeGroup) return
    const targetId = Number(queryUserId)
    if (!Number.isInteger(targetId) || targetId <= 0) {
      verwerfeUnbekanntenKontakt()
      return
    }

    // Ein bekannter Kontakt öffnet sofort — auch aus dem örtlichen Abzug, also
    // bevor die frische Liste da ist. Gewartet wird nur für die Prüfung einer
    // **unbekannten** Kennung; sonst wäre der Chat nach jedem Öffnen kurz tot.
    const match = kontakte.find((c) => c.userId === targetId)
    if (match) {
      setActiveContact((prev) => {
        if (
          prev?.userId === match.userId &&
          prev.username === match.username &&
          prev.avatarUrl === match.avatarUrl
        ) {
          return prev
        }
        return match
      })
      setActiveGroup(null)
      return
    }

    if (!kontakteGeladen) return
    if (gepruefterKontaktRef.current === targetId) return
    gepruefterKontaktRef.current = targetId

    let aktiv = true
    void (async () => {
      // Ein bestehender Chat überlebt eine Profiländerung: der Server prüft
      // beim Senden `can_message_user` und lässt Antworten weiter zu. Ohne
      // diesen Zweig verschwände ein alter Chat, sobald jemand sein Profil auf
      // „privat" stellt.
      const bekannt = (await ladeGespraeche().catch(() => new Map())).get(targetId) ?? null
      let profil: Awaited<ReturnType<typeof getProfile>> | null = null
      try {
        profil = await getProfile(targetId)
      } catch {
        // 404 heißt: gibt es nicht, inaktiv oder für dieses Konto unsichtbar.
      }
      if (!aktiv) return

      const erlaubt = Boolean(bekannt) || Boolean(profil && (profil.privacy === 'public' || profil.is_friend))
      if (!erlaubt) {
        verwerfeUnbekanntenKontakt(targetId)
        return
      }

      setActiveContact({
        listKey: `q-${targetId}`,
        id: targetId,
        userId: targetId,
        username: profil?.username || bekannt?.username || t('messenger.thisContact'),
        avatarUrl: profil?.avatar_url ?? bekannt?.avatarUrl ?? null,
        status: (profil?.presence?.status as PresenceStatus) || 'invisible',
        deviceType: profil?.presence?.device_type ?? null,
        activityLabel: profil?.presence?.activity_label ?? null,
        isFriend: Boolean(profil?.is_friend),
        teamName: null,
        isPublicUser: profil?.privacy === 'public',
      })
      setActiveGroup(null)
    })()

    return () => {
      aktiv = false
    }
  }, [
    queryUserId,
    kontakte,
    activeGroup,
    kontakteGeladen,
    verwerfeUnbekanntenKontakt,
    setActiveContact,
    setActiveGroup,
    t,
  ])

  // Ist die Adresse leer, ist die Prüfung von vorhin erledigt: derselbe
  // Aufruf darf später erneut geprüft werden, statt am Merker zu scheitern.
  useEffect(() => {
    if (!queryUserId) gepruefterKontaktRef.current = null
  }, [queryUserId])

  // Eine Gruppe aus der Adresse öffnen. Ein Name kommt aus der Liste; steht sie
  // dort nicht, bleibt der Chat zu, statt als leerer Rahmen aufzugehen.
  useEffect(() => {
    if (queryGroupId && !activeContact && gruppen.length > 0) {
      const targetGroupId = Number(queryGroupId)
      if (targetGroupId) {
        const match = gruppen.find((g) => g.id === targetGroupId)
        if (match) {
          setActiveGroup((prev) => (prev?.id === match.id ? prev : match))
          setActiveContact(null)
        }
      }
    }
  }, [queryGroupId, gruppen, activeContact, setActiveGroup, setActiveContact])

  return { verwerfeUnbekanntenKontakt }
}
