import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import { useKonversation, type GespraechsZiel } from '@/hooks/useKonversation'
import { useNachrichtenLaden } from '@/hooks/useNachrichtenLaden'
import { usePostfachWache } from '@/hooks/usePostfachWache'
import { IDENTITY_LOADING, resolveIdentity, type E2eeIdentity } from '@/services/e2eeIdentity'
import { decryptE2eeHybridWithKeyring, deriveBlindMailboxId, scrubPlaintextStorage } from '@/services/e2eeCrypto'
import { uebernimmAltbestand } from '@/services/altbestandUebernahme'
import {
  eigenesGeraetFreigegeben,
  geraeteVon,
  onEigeneFreigabe,
  onSchluesselWarnung,
} from '@/services/e2eeGeraet'
import { sendeVerlaufAnNeueGeraete } from '@/services/geraet/verlaufsAbgleich'
import { merkeWirkungAusKlartext } from '@/services/eigeneWirkungen'
import {
  holeGeraeteSteuerung,
  verteileOffeneZustellungenAller,
} from '@/services/gruppenSchluessel'
import { ladeEntwurf, loadLocalMessages, sichereDauerhafteAblage, sortMessagesChronologically } from '@/services/messengerLocalStore'
import { holeOffeneMedien } from '@/services/medienNachholen'
import { setzeLoeschMelder, stosseLoeschungenAn } from '@/services/loeschNachholen'
import { meldeChat } from '@/services/benachrichtigung/chatMeldung'
import { anheftung } from '@/services/nachrichtAnheftung'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { partnerstand, quittungsstand } from '@/services/quittungsstand'
import { verfallsfrist, verfallStand } from '@/services/nachrichtVerfall'
import { setzeSteuerOutboxMelder, stosseSteuerOutboxAn } from '@/services/steuerOutboxLauf'
import type { SteuerAuftrag } from '@/services/steuerOutboxTypen'
import { toast } from '@/stores/toastStore'
import { useFunkenStore } from '@/stores/funkenStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { setzeAnrufIdentitaet } from '@/stores/useCallStore'

interface Optionen {
  t: (schluessel: string, optionen?: Record<string, unknown>) => string
  currentUserId: number
  friends: { user_id?: number; friend_user_id?: number; id: number; status?: string; created_at?: string }[]
  activeContact: ChatContact | null
  activeGroup: ChatGroupItem | null
  /** Alle Gruppen des Kontos — der Hintergrundlauf ordnet ihnen Postfach-Eintraege zu. */
  groups: ChatGroupItem[]
  messengerGesperrt: boolean
  gruppenrechteVon: (konto: number) => Set<string>
  setAngeheftet: Dispatch<SetStateAction<ChatMessage | null>>
  setVerfallSekunden: (sekunden: number) => void
  markAsRead: (mailbox: string) => void
  isBlocked: (userId: number) => boolean
  readReceiptsEnabled: boolean
  /** Räumt Zitat, Suche, Auswahl und Eingabe beim Wechsel des Gegenübers. */
  onGespraechWechsel: () => void
  /** Übernimmt einen gespeicherten Entwurf in das Eingabefeld. */
  onEntwurf: (text: string) => void
}

/**
 * Der Nachrichtenkern: Verlauf, Identität, Mailbox und Steuerumschläge.
 *
 * Die reine Anzeige bleibt draußen: `onGespraechWechsel` und `onEntwurf` sind
 * die beiden Stellen, an denen der Kern das Eingabefeld berührt. So bleiben
 * Entwurf, Zitat und Mehrfachauswahl dort, wo sie hingehören, ohne dass der
 * Kern ihre Setter einzeln kennen muss.
 */
export function useMessengerKern({
  t,
  currentUserId,
  friends,
  activeContact,
  activeGroup,
  groups,
  messengerGesperrt,
  gruppenrechteVon,
  setAngeheftet,
  setVerfallSekunden,
  markAsRead,
  isBlocked,
  readReceiptsEnabled,
  onGespraechWechsel,
  onEntwurf,
}: Optionen) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [sending, setSending] = useState(false)
  const [identity, setIdentity] = useState<E2eeIdentity>(IDENTITY_LOADING)
  const identityRef = useRef<E2eeIdentity>(IDENTITY_LOADING)
  identityRef.current = identity

  const gespraechsZielRef = useRef('')
  const entwurfGeholtRef = useRef('')
  const eingabeBeruehrtRef = useRef(false)
  const activeMailboxIdRef = useRef('')
  const currentLoadSeqRef = useRef(0)
  const highestIncomingIdAcknowledgedRef = useRef(0)
  const highestIncomingIdDeliveredRef = useRef(0)
  const maxPartnerReadIdRef = useRef(0)
  const maxPartnerDeliveredIdRef = useRef(0)
  const sitzungsMeldungRef = useRef<Map<string, number>>(new Map())
  const aufbauMeldungRef = useRef<Map<string, number>>(new Map())
  /**
   * Systemzeilen je Gespräch. Sie entstehen aus Ereignissen und nicht aus dem
   * Verlauf; deshalb überlebt eine Warnung den Abruf, der den Verlauf ersetzt.
   * Der Schlüssel ist das Gegenüber, nicht die Mailbox: die Kennung trifft
   * asynchron ein und darf eine Warnung aus derselben Sekunde nicht verlieren.
   */
  const systemzeilenRef = useRef<Map<string, ChatMessage[]>>(new Map())
  const systemzeilenZielRef = useRef('')
  systemzeilenZielRef.current = activeGroup
    ? `g:${activeGroup.id}`
    : activeContact
      ? `u:${activeContact.userId}`
      : ''

  const merkeSystemzeile = useCallback((zeile: ChatMessage) => {
    const schluessel = systemzeilenZielRef.current
    const bisher = systemzeilenRef.current.get(schluessel) ?? []
    systemzeilenRef.current.set(
      schluessel,
      [...bisher.filter((m) => m.clientUuid !== zeile.clientUuid), zeile].slice(-20),
    )
  }, [])

  /**
   * Dieselbe Systemzeile darf nur einmal im Verlauf stehen. Der Warnhinweis
   * und das Eintreffen der Mailbox können in beliebiger Reihenfolge laufen;
   * beide Wege prüfen deshalb dieselbe Kennung, statt blind anzuhängen.
   */
  const haengeSystemzeileAn = useCallback((vorher: ChatMessage[], zeile: ChatMessage) =>
    vorher.some((m) => m.clientUuid === zeile.clientUuid)
      ? vorher
      : sortMessagesChronologically([...vorher, zeile]), [])

  const [geraetWartet, setGeraetWartet] = useState(eigenesGeraetFreigegeben() === false)

  const gespraechsZiel = useMemo<GespraechsZiel>(() => {
    if (activeGroup) {
      return {
        art: 'gruppe',
        groupId: activeGroup.id,
        mitglieder: (activeGroup.members ?? []).map((m) => m.user_id),
        istEigentuemer: activeGroup.role === 'owner',
      }
    }
    if (activeContact) return { art: 'direkt', peerId: activeContact.userId }
    return { art: 'keins' }
  }, [activeGroup?.id, activeGroup?.members, activeContact?.userId])

  const sitzungNeuGemeldet = useCallback((geraet: string) => {
    const jetzt = Date.now()
    const zuletzt = sitzungsMeldungRef.current.get(geraet) || 0
    if (jetzt - zuletzt < 60_000) return
    sitzungsMeldungRef.current.set(geraet, jetzt)
    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: `sys-dr-${geraet}-${jetzt}`,
      senderId: 0,
      text: t('messenger.sessionRebuilt'),
      createdAt: new Date().toISOString(),
      isSelf: false,
      isSystem: true,
    }
    merkeSystemzeile(zeile)
    setMessages((prev) => haengeSystemzeileAn(prev, zeile))
  }, [haengeSystemzeileAn, merkeSystemzeile])

  const aufbauAbgelehnt = useCallback((geraet: string) => {
    const jetzt = Date.now()
    const zuletzt = aufbauMeldungRef.current.get(geraet) || 0
    if (jetzt - zuletzt < 60_000) return
    aufbauMeldungRef.current.set(geraet, jetzt)
    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: `sys-dr-abgewiesen-${geraet}-${jetzt}`,
      senderId: 0,
      text: t('messenger.sessionSetupRejected'),
      createdAt: new Date().toISOString(),
      isSelf: false,
      isSystem: true,
    }
    merkeSystemzeile(zeile)
    setMessages((prev) => haengeSystemzeileAn(prev, zeile))
  }, [haengeSystemzeileAn, merkeSystemzeile, t])

  const zeigeSystemzeile = useCallback((text: string, kennung?: string) => {
    const jetzt = Date.now()
    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: kennung ? `sys-${kennung}` : `sys-${jetzt}`,
      senderId: 0,
      text,
      createdAt: new Date().toISOString(),
      isSelf: false,
      isSystem: true,
    }
    merkeSystemzeile(zeile)
    setMessages((prev) => haengeSystemzeileAn(prev, zeile))
  }, [haengeSystemzeileAn, merkeSystemzeile])

  useEffect(() => {
    scrubPlaintextStorage()
    if (!currentUserId || messengerGesperrt) return
    let active = true
    resolveIdentity(currentUserId)
      .then((next) => { if (active) setIdentity(next) })
      .catch(() => {})
    return () => { active = false }
  }, [currentUserId, messengerGesperrt])

  /**
   * Die eigene Geräte-Mailbox einmal beim Öffnen lesen — nicht erst, wenn ein
   * Gespräch offen ist.
   *
   * Dort liegen die Chatgeheimnisse und die Verlaufsübergaben. Ohne diesen
   * Schritt erfuhr ein Gerät von beidem erst beim Öffnen eines Chats; die
   * Kontaktliste blieb stumm, und ein frisch freigegebenes Gerät sah seinen
   * Verlauf erst nach dem ersten Klick. Der laufende Chat liest weiterhin
   * selbst, dieser Durchlauf ist nur der Anfang.
   */
  const nachgeholtRef = useRef<number | null>(null)
  useEffect(() => {
    if (!currentUserId || messengerGesperrt) return
    if (identity.state !== 'ready' || !identity.decryptionKeys) return
    if (nachgeholtRef.current === currentUserId) return
    nachgeholtRef.current = currentUserId
    void holeGeraeteSteuerung(
      currentUserId,
      (umschlag) => decryptE2eeHybridWithKeyring(umschlag, identity.decryptionKeys),
      // **Auch ohne offenes Gespräch ablegen.** Eine Bearbeitung oder Löschung
      // aus einem Zweitgerät kam beim Start bisher nicht an: der Cursor wanderte
      // weiter, und die Wirkung war weg, bevor der Chat je geöffnet wurde.
      // Das Versprechen wird zurückgegeben, nicht verworfen: scheitert das
      // Ablegen, bleibt der Lesestand davor stehen und die Wirkung wird beim
      // nächsten Durchlauf erneut versucht.
      (klartext, env) => merkeWirkungAusKlartext(klartext, env),
    ).catch(() => {})
    void sendeVerlaufAnNeueGeraete().catch(() => {})

    /*
     * Ein früherer Lauf kann einzelne Geräte beim Verteilen verpasst haben.
     * Der Auftrag liegt in der Ablage; hier wird er nachgeholt — beim Start,
     * wenn das Netz zurückkommt und wenn die App wieder nach vorn tritt.
     */
    const holeNach = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      void verteileOffeneZustellungenAller(currentUserId).catch(() => {})
    }
    holeNach()
    window.addEventListener('online', holeNach)
    document.addEventListener('visibilitychange', holeNach)
    return () => {
      window.removeEventListener('online', holeNach)
      document.removeEventListener('visibilitychange', holeNach)
    }
  }, [currentUserId, identity, messengerGesperrt])

  useEffect(() => {
    if (!currentUserId) return
    void sichereDauerhafteAblage()
  }, [currentUserId])

  /**
   * Anhaenge nachholen, die beim Verarbeiten nicht zu holen waren.
   *
   * Drei Anlaesse: der Messenger ist bereit (Identitaet da, nicht gesperrt),
   * das Netz kommt zurueck, und die App tritt wieder nach vorn. Der dritte
   * fehlte sonst: ein Telefon, das im Hintergrund das Netz verliert und
   * wiedergewinnt, sah kein `online`, und der Anhang wartete bis zum naechsten
   * Start. Der Ladepfad selbst stoesst ebenfalls an (`gespraech/uebernahme`).
   *
   * Der Auftrag liegt in der Ablage; hier wird nur gelesen, was faellig ist.
   * Kein Timer, kein Dauerfeuer.
   */
  useEffect(() => {
    if (!currentUserId || messengerGesperrt) return
    if (identity.state !== 'ready') return
    let aktiv = true
    const lauf = () => {
      if (!aktiv) return
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      void holeOffeneMedien().catch(() => {})
    }
    lauf()
    window.addEventListener('online', lauf)
    document.addEventListener('visibilitychange', lauf)
    return () => {
      aktiv = false
      window.removeEventListener('online', lauf)
      document.removeEventListener('visibilitychange', lauf)
    }
  }, [currentUserId, identity, messengerGesperrt])

  /**
   * Das Server-Aufräumen gelöschter Nachrichten abarbeiten.
   *
   * Dieselben drei Anlässe wie beim Medien-Nachholen und bei der Outbox:
   * Start (Identität da, nicht gesperrt), `online` und die Rückkehr der App
   * nach vorn. Das Entsperren löst diesen Effekt über `messengerGesperrt`
   * ebenfalls aus. Der vierte Anlass steht im Löschpfad selbst: dort wird der
   * Auftrag sofort versucht.
   *
   * Der Auftrag liegt in der Ablage; hier wird nur gelesen, was fällig ist.
   * Kein Timer, kein Dauerfeuer.
   */
  useEffect(() => {
    if (!currentUserId || messengerGesperrt) return
    if (identity.state !== 'ready') return
    let aktiv = true
    const lauf = () => {
      if (!aktiv) return
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      void stosseLoeschungenAn().catch(() => {})
    }
    lauf()
    window.addEventListener('online', lauf)
    document.addEventListener('visibilitychange', lauf)
    return () => {
      aktiv = false
      window.removeEventListener('online', lauf)
      document.removeEventListener('visibilitychange', lauf)
    }
  }, [currentUserId, identity.state, messengerGesperrt])

  /**
   * Die Outbox der Steuerwirkungen abarbeiten.
   *
   * Dieselben drei Anlässe wie beim Medien-Nachholen — Start (Identität da,
   * nicht gesperrt), `online` und die Rückkehr der App nach vorn. Das
   * Entsperren löst diesen Effekt über `messengerGesperrt` ebenfalls aus.
   * Ein vierter Anlass steht im Sendepfad: nach jedem erfolgreichen Senden
   * (`sendeWirkung` in `useKonversation`).
   *
   * Der Auftrag liegt in der Ablage; hier wird nur gelesen, was fällig ist.
   * Kein Timer, kein Dauerfeuer.
   */
  useEffect(() => {
    if (!currentUserId || messengerGesperrt) return
    if (identity.state !== 'ready') return
    let aktiv = true
    const lauf = () => {
      if (!aktiv) return
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      void stosseSteuerOutboxAn().catch(() => {})
    }
    lauf()
    window.addEventListener('online', lauf)
    document.addEventListener('visibilitychange', lauf)
    return () => {
      aktiv = false
      window.removeEventListener('online', lauf)
      document.removeEventListener('visibilitychange', lauf)
    }
  }, [currentUserId, identity.state, messengerGesperrt])

  /**
   * Was die Outbox aufgibt, wird gesagt — und im offenen Gespräch nachgezogen.
   *
   * Ein Auftrag fällt nur aus zwei Gründen heraus: der Server hat ihn endgültig
   * abgelehnt, oder die Versuche sind erschöpft. In beiden Fällen wurde die
   * lokale Wirkung schon zurückgenommen (`nimmWirkungZurueck`); hier wird sie
   * sichtbar — als Systemzeile, und die betroffenen Zeilen kommen aus der
   * Ablage neu. Eine stille Lücke gibt es nicht (Punkt 168 des Regelwerks).
   */
  useEffect(() => {
    return setzeSteuerOutboxMelder((auftrag: SteuerAuftrag, grund) => {
      const ohneRuecknahme = auftrag.ruecknahme?.art === 'keine'
      const schluessel = ohneRuecknahme
        ? grund === 'abgelehnt'
          ? 'messenger.deleteRejected'
          : 'messenger.deleteUndelivered'
        : grund === 'abgelehnt'
          ? 'messenger.actionRejected'
          : 'messenger.actionUndelivered'
      // Die Systemzeile gehört in das Gespräch, um das es geht. Ist ein anderes
      // offen, sagt es ein Toast — eine Zeile über einen fremden Chat wäre im
      // falschen Verlauf.
      if (auftrag.mailbox === activeMailboxIdRef.current) {
        zeigeSystemzeile(t(schluessel), `steuer-${auftrag.id}`)
      } else {
        toast.info(t(schluessel))
      }

      if (auftrag.mailbox !== activeMailboxIdRef.current) return
      const art = auftrag.ruecknahme?.art
      if (art === 'anheftung') {
        if (!anheftung(auftrag.mailbox).clientUuid) setAngeheftet(null)
        return
      }
      if (art === 'verfall') {
        setVerfallSekunden(verfallStand(auftrag.mailbox).sekunden)
        return
      }
      if (art !== 'reaktion' && art !== 'bearbeitung') return
      // Den geänderten Stand aus der Ablage holen, statt ihn zu raten.
      void loadLocalMessages(auftrag.mailbox)
        .then((zeilen) => {
          setMessages((vorher) =>
            vorher.map((m) => {
              const frisch = zeilen.find(
                (z) => (m.clientUuid && z.clientUuid === m.clientUuid) || z.id === m.id,
              )
              if (!frisch) return m
              return {
                ...m,
                text: frisch.text,
                isEdited: frisch.isEdited,
                editedAt: frisch.editedAt,
                originalText: frisch.originalText,
                reaktionen: frisch.reaktionen,
              }
            }),
          )
        })
        .catch(() => {})
    })
  }, [t, zeigeSystemzeile, setAngeheftet, setVerfallSekunden])

  /**
   * Was beim Server-Aufräumen endgültig nicht ging, wird gesagt.
   *
   * Drei Fälle: der Server hat es abgelehnt, die Versuche sind erschöpft, oder
   * ein fremder Anhang ließ sich nicht entfernen. Der Text gehört in das
   * Gespräch, um das es geht; ist ein anderes offen, sagt es ein Toast. Eine
   * stille Lücke gibt es nicht (Punkt 168 des Regelwerks).
   */
  useEffect(() => {
    return setzeLoeschMelder((auftrag, grund, medienGeblieben) => {
      const fremdesMedium = grund === 'medien' && auftrag.fremd
      const schluessel =
        grund === 'abgelehnt'
          ? 'messenger.serverCleanupRejected'
          : grund === 'aufgegeben'
            ? 'messenger.serverCleanupAbandoned'
            : auftrag.fremd
              ? 'messenger.messageRemovedMediaStays'
              : 'messenger.serverCleanupMediaRemains'
      const text = t(
        schluessel,
        fremdesMedium ? { count: medienGeblieben } : undefined,
      )
      if (auftrag.mailbox === activeMailboxIdRef.current) {
        zeigeSystemzeile(text, `loesch-${auftrag.id}`)
      } else {
        toast.info(text)
      }
    })
  }, [t, zeigeSystemzeile])

  /**
   * Der Sicherheitsnachschlag fuer die dauerhaften Auftraege.
   *
   * Outbox, Medien-Nachholen und Server-Loeschauftrag haben eigene Anlaesse
   * (Start, online, Sichtbarkeit, Entsperren). Ein Auftrag, der beim ersten
   * Versuch in den Rueckzug ging, wird aber erst spaeter faellig — und wenn
   * `online` vorher feuerte, gab es bis 24.10.2026 keinen weiteren Anlass: er
   * wartete beliebig lange. Dieser eigene Takt ist der periodische Nachschlag;
   * die Dienste lesen nur, was faellig ist, und laufen ohnehin nur einmal
   * zugleich. Er haengt bewusst nicht am Postfachlauf: ein haengender Abruf
   * dort darf die Wirkungen nicht mit anhalten.
   */
  useEffect(() => {
    if (!currentUserId || messengerGesperrt) return
    if (identity.state !== 'ready') return
    const takt = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      void holeOffeneMedien().catch(() => {})
      void stosseSteuerOutboxAn().catch(() => {})
      void stosseLoeschungenAn().catch(() => {})
    }, 10_000)
    return () => clearInterval(takt)
  }, [currentUserId, identity.state, messengerGesperrt])

  /**
   * Das Gerätepostfach im Hintergrund leeren.
   *
   * Der Lauf liegt in `usePostfachWache`, weil ihn auch Pontis bei
   * geschlossenem Messenger braucht. Hier bleibt nur die Meldung der neuen
   * Zeilen: das Gerätepostfach erzeugt kein Sync-Ereignis, aus dem eine andere
   * Stelle sie erfahren könnte.
   */
  usePostfachWache({
    eigeneId: currentUserId,
    freunde: friends,
    gruppen: groups,
    identitaet: identity,
    gesperrt: messengerGesperrt,
    t,
    markAsRead,
    setVerfallSekunden,
    gruppenrechteVon,
    istBlockiert: isBlocked,
    beiNeuerNachricht: (gespraech, nachrichten) => {
      for (const msg of nachrichten) {
        meldeChat({
          mailbox: gespraech.mailbox,
          senderId: msg.senderId,
          clientUuid: msg.clientUuid ?? null,
          istGruppe: Boolean(gespraech.group),
          t,
        })
      }
    },
  })

  /**
   * Beim Verlassen des Messengers gibt es keinen offenen Chat mehr.
   *
   * Ohne diesen Schritt blieb die zuletzt geöffnete Mailbox im Store stehen.
   * `planeMeldung` hielt sie dann für „offen" und unterdrückte Zahl und Ton
   * dauerhaft — auch wenn der Messenger längst geschlossen war.
   */
  useEffect(
    () => () => {
      useMessengerNotificationStore.getState().setActiveMailboxId(null)
    },
    [],
  )

  useEffect(() => {
    if (!currentUserId || friends.length === 0 || messengerGesperrt) return
    let active = true
    Promise.all(friends.map((f) => deriveBlindMailboxId(currentUserId, Number(f.user_id ?? f.friend_user_id ?? f.id))))
      .then((mailboxen) => { if (active) return uebernimmAltbestand(currentUserId, mailboxen) })
      .catch(() => {})
    return () => { active = false }
  }, [currentUserId, friends.length, messengerGesperrt])

  useEffect(() => {
    useFunkenStore.getState().setzeFreunde(
      friends
        .filter((f) => f.status === 'accepted' || Boolean(f.friend_user_id))
        .map((f) => ({ userId: Number(f.user_id ?? f.friend_user_id ?? f.id), seit: f.created_at })),
    )
  }, [friends])

  useEffect(() => {
    if (!currentUserId) { setzeAnrufIdentitaet(null); return }
    if (identity.state !== 'ready' || !identity.sendPair) return
    setzeAnrufIdentitaet({
      userId: currentUserId,
      publicKeyJwk: identity.sendPair.publicKeyJwk,
      decryptionKeys: identity.decryptionKeys,
    })
  }, [currentUserId, identity])

  useEffect(() => {
    if (activeContact?.userId) geraeteVon(activeContact.userId).catch(() => {})
  }, [activeContact?.userId])

  useEffect(() => {
    if (currentUserId) geraeteVon(currentUserId).catch(() => {})
  }, [currentUserId])

  useEffect(() => onEigeneFreigabe((frei) => setGeraetWartet(frei === false)), [])

  useEffect(() => {
    const abbestellen = onSchluesselWarnung((ev) => {
      const kennung = `warn-${ev.typ}-${ev.userId}-${ev.geraete.join('-')}`
      if (currentUserId === ev.userId) {
        const text = {
          neues_geraet: t('messenger.ownNewDevice'),
          unbestaetigt: t('messenger.ownUnverifiedDeviceWarning'),
          freigabe_unbestaetigt: t('messenger.ownBridgedDeviceWarning'),
          konto_neustart: t('messenger.ownAccountResetWarning'),
        }[ev.typ]
        zeigeSystemzeile(text, kennung)
        return
      }
      let name = ''
      if (activeContact?.userId === ev.userId) {
        name = activeContact.username || t('messenger.thisContact')
      } else if (activeGroup) {
        const member = (activeGroup.members ?? []).find((m) => Number(m.user_id) === ev.userId)
        if (member) name = member.username || t('messenger.thisContact')
      }
      if (!name) return
      const text = {
        neues_geraet: t('messenger.newDeviceDetected', { name }),
        unbestaetigt: t('messenger.unverifiedDeviceWarning', { name }),
        freigabe_unbestaetigt: t('messenger.bridgedDeviceWarning', { name }),
        konto_neustart: t('messenger.accountResetWarning', { name }),
      }[ev.typ]
      zeigeSystemzeile(text, kennung)
    })
    return abbestellen
  }, [activeContact, activeGroup, currentUserId, t, zeigeSystemzeile])

  const konversation = useKonversation({
    ziel: gespraechsZiel,
    eigeneId: currentUserId,
    identitaetRef: identityRef,
    meldeSitzungsbruch: sitzungNeuGemeldet,
    meldeAufbauAbgelehnt: aufbauAbgelehnt,
  })
  const blindMailboxId = konversation.blindMailboxId

  // Das offene Gespräch im versiegelten örtlichen Speicher merken.
  useEffect(() => {
    if (!activeContact) return
    void import('@/services/gespraechsListe').then(({ merkeGespraech }) =>
      merkeGespraech(activeContact.userId, {
        username: activeContact.username,
        avatarUrl: activeContact.avatarUrl,
        blindMailboxId,
      }),
    ).catch(() => {})
  }, [activeContact?.userId, activeContact?.username, blindMailboxId])

  const sendE2eeControlMessage = async (payloadObj: Record<string, unknown>) => {
    if (!blindMailboxId || !currentUserId || (!activeContact && !activeGroup)) return
    // Ein Weg für Oberfläche und Hintergrund (`services/gespraech/steuerung`).
    await konversation.sendeSteuerpaket(payloadObj)
  }

  // Der Wechsel in ein anderes Gespräch: Anzeige leeren, Zähler setzen, Verlauf zeigen.
  useEffect(() => {
    let active = true
    const zielSchluessel = activeGroup
      ? `g:${activeGroup.id}`
      : activeContact ? `u:${activeContact.userId}` : ''
    const neuesGespraech = gespraechsZielRef.current !== zielSchluessel
    gespraechsZielRef.current = zielSchluessel

    if (neuesGespraech) {
      onGespraechWechsel()
      eingabeBeruehrtRef.current = false
      if (!zielSchluessel) entwurfGeholtRef.current = ''

      // Alte Warnungen gehören zum alten Gespräch. Der Wechsel kann vor der
      // neuen Mailbox ankommen; deshalb fallen sie hier schon aus dem Verlauf,
      // nicht erst beim späteren Ersetzen durch den Abruf.
      for (const schluessel of [...systemzeilenRef.current.keys()]) {
        if (schluessel !== zielSchluessel) systemzeilenRef.current.delete(schluessel)
      }
      const erlaubt = new Set(
        (systemzeilenRef.current.get(zielSchluessel) ?? []).map((m) => m.clientUuid),
      )
      setMessages((vorher) => vorher.filter((m) => !m.isSystem || (m.clientUuid && erlaubt.has(m.clientUuid))))
    }

    if (activeMailboxIdRef.current === blindMailboxId) return
    activeMailboxIdRef.current = blindMailboxId
    const stand = quittungsstand(blindMailboxId)
    highestIncomingIdAcknowledgedRef.current = stand.gelesen
    highestIncomingIdDeliveredRef.current = stand.zugestellt
    /*
     * Der Stand des **Partners** kommt aus der Ablage, nicht aus der Mailbox.
     *
     * Die Quittungen dafür liegen in der Mailbox, aber der Abruf liest seit
     * dem Cursor (Punkt 180) nur noch Neues — die alten Quittungen kommen nie
     * wieder vorbei. Bis 22.10.2026 standen hier zwei Nullen, und die Häkchen
     * an den eigenen Nachrichten fielen bei jedem Chatwechsel auf „gesendet"
     * zurück, obwohl der Partner gelesen hatte.
     */
    const partner = partnerstand(blindMailboxId)
    maxPartnerReadIdRef.current = partner.gelesen
    maxPartnerDeliveredIdRef.current = partner.zugestellt
    useMessengerNotificationStore.getState().setActiveMailboxId(blindMailboxId || null)

    /**
     * Beim Eintreffen der Mailbox können bereits Systemzeilen dastehen: eine
     * Schlüsselwarnung kommt aus einem Ereignis und nicht aus dem Verlauf.
     * Ein Wechsel des Gesprächs verwirft sie; bleibt das Gespräch dasselbe und
     * trifft nur die Kennung ein, bleiben sie stehen.
     */
    const mitSystemzeilen = (vorher: ChatMessage[], basis: ChatMessage[]) => {
      const gemerkt = systemzeilenRef.current.get(zielSchluessel) ?? []
      const ausVerlauf = neuesGespraech ? [] : vorher.filter((m) => m.isSystem)
      const systemzeilen = [...ausVerlauf, ...gemerkt]
      if (!systemzeilen.length) return basis
      const vorhanden = new Set(basis.map((m) => m.clientUuid ?? `#${m.id}`))
      const einmalig = systemzeilen.filter((m) => {
        const kennung = m.clientUuid ?? `#${m.id}`
        if (vorhanden.has(kennung)) return false
        vorhanden.add(kennung)
        return true
      })
      return sortMessagesChronologically([...basis, ...einmalig])
    }

    if (!blindMailboxId) {
      setMessages((vorher) => mitSystemzeilen(vorher, []))
      setLoadingMessages(Boolean(activeContact || activeGroup))
      return
    }

    setVerfallSekunden(verfallsfrist(blindMailboxId))
    if (entwurfGeholtRef.current !== zielSchluessel) {
      entwurfGeholtRef.current = zielSchluessel
      if (!eingabeBeruehrtRef.current) {
        ladeEntwurf(blindMailboxId)
          .then((text) => {
            if (!active || activeMailboxIdRef.current !== blindMailboxId) return
            if (text && !eingabeBeruehrtRef.current) onEntwurf(text)
          })
          .catch(() => {})
      }
    }

    /**
     * Systemzeilen gehören zum Besuch, nicht zum Verlauf. Der Zwischenspeicher
     * hält den ganzen Verlauf inklusive Warnungen; beim Zurückwechseln würden
     * sie sonst wieder auftauchen, obwohl das Gespräch sie längst verworfen hat.
     */
    const cached = sessionChatCache.get(blindMailboxId)?.filter((m) => !m.isSystem)
    if (cached && cached.length > 0) {
      setMessages((vorher) => mitSystemzeilen(vorher, cached))
      setLoadingMessages(false)
    } else {
      setMessages((vorher) => mitSystemzeilen(vorher, []))
      setLoadingMessages(true)
      loadLocalMessages(blindMailboxId)
        .then((localMsgs) => {
          if (active && activeMailboxIdRef.current === blindMailboxId && localMsgs.length > 0) {
            sessionChatCache.set(blindMailboxId, localMsgs)
            setMessages((vorher) => mitSystemzeilen(vorher, localMsgs))
            setLoadingMessages(false)
          }
        })
        .catch(() => {})
    }

    return () => { active = false }
  }, [blindMailboxId, activeContact?.userId, activeGroup?.id])

  const loadMessages = useNachrichtenLaden({
    blindMailboxId,
    currentUserId,
    activeContact,
    activeGroup,
    gruppenrechteVon,
    t,
    messages,
    setMessages,
    setLoadingMessages,
    setAngeheftet,
    setVerfallSekunden,
    zeigeSystemzeile,
    markAsRead,
    isBlocked,
    readReceiptsEnabled,
    konversation,
    identityRef,
    activeMailboxIdRef,
    currentLoadSeqRef,
    maxPartnerReadIdRef,
    maxPartnerDeliveredIdRef,
    highestIncomingIdDeliveredRef,
    highestIncomingIdAcknowledgedRef,
    sendE2eeControlMessage,
  })

  return {
    messages, setMessages, loadingMessages, setLoadingMessages, sending, setSending,
    identity, setIdentity, identityRef, geraetWartet, gespraechsZiel, blindMailboxId,
    konversation, sendE2eeControlMessage, sendeWirkung: konversation.sendeWirkung,
    loadMessages, zeigeSystemzeile,
    gespraechsZielRef, entwurfGeholtRef, eingabeBeruehrtRef, activeMailboxIdRef,
    currentLoadSeqRef, highestIncomingIdAcknowledgedRef, highestIncomingIdDeliveredRef,
    maxPartnerReadIdRef, maxPartnerDeliveredIdRef,
  }
}

export type MessengerKern = ReturnType<typeof useMessengerKern>
