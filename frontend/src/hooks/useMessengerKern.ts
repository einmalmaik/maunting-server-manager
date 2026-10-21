import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import { relayE2eeEnvelope } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import { useKonversation, type GespraechsZiel } from '@/hooks/useKonversation'
import { useNachrichtenLaden } from '@/hooks/useNachrichtenLaden'
import { IDENTITY_LOADING, resolveIdentity, type E2eeIdentity } from '@/services/e2eeIdentity'
import { deriveBlindMailboxId, scrubPlaintextStorage } from '@/services/e2eeCrypto'
import { uebernimmAltbestand } from '@/services/altbestandUebernahme'
import {
  eigenesGeraetFreigegeben,
  geraeteVon,
  onEigeneFreigabe,
  onSchluesselWarnung,
} from '@/services/e2eeGeraet'
import { ladeEntwurf, loadLocalMessages, sichereDauerhafteAblage, sortMessagesChronologically } from '@/services/messengerLocalStore'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { quittungsstand } from '@/services/quittungsstand'
import { verfallsfrist } from '@/services/nachrichtVerfall'
import { signiereNutzlast } from '@/services/nutzlastSignatur'
import { useFunkenStore } from '@/stores/funkenStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { setzeAnrufIdentitaet } from '@/stores/useCallStore'

interface Optionen {
  t: (schluessel: string, optionen?: Record<string, unknown>) => string
  currentUserId: number
  friends: { user_id?: number; friend_user_id?: number; id: number; status?: string; created_at?: string }[]
  activeContact: ChatContact | null
  activeGroup: ChatGroupItem | null
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

  const zeigeSystemzeile = useCallback((text: string) => {
    const jetzt = Date.now()
    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: `sys-${jetzt}`,
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

  useEffect(() => {
    if (!currentUserId) return
    void sichereDauerhafteAblage()
  }, [currentUserId])

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
      if (currentUserId === ev.userId) {
        const text = {
          neues_geraet: t('messenger.ownNewDevice'),
          unbestaetigt: t('messenger.ownUnverifiedDeviceWarning'),
          konto_neustart: t('messenger.ownAccountResetWarning'),
        }[ev.typ]
        zeigeSystemzeile(text)
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
        konto_neustart: t('messenger.accountResetWarning', { name }),
      }[ev.typ]
      zeigeSystemzeile(text)
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
    const clientUuid = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : 'ctrl-' + Date.now() + '-' + Math.random().toString(36).substring(2, 9)
    const payload = JSON.stringify(
      await signiereNutzlast(blindMailboxId, currentUserId, {
        actor_id: currentUserId,
        ...payloadObj,
        mailbox: blindMailboxId,
        client_uuid: clientUuid,
      }),
    )
    const auftraege = await konversation.baueSteuerversand(
      payload, clientUuid, String(payloadObj.type || 'control'),
    )
    await Promise.all(auftraege.map((auftrag) => relayE2eeEnvelope(auftrag)))
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
    maxPartnerReadIdRef.current = 0
    maxPartnerDeliveredIdRef.current = 0
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

    const cached = sessionChatCache.get(blindMailboxId)
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
    konversation, sendE2eeControlMessage, loadMessages, zeigeSystemzeile,
    gespraechsZielRef, entwurfGeholtRef, eingabeBeruehrtRef, activeMailboxIdRef,
    currentLoadSeqRef, highestIncomingIdAcknowledgedRef, highestIncomingIdDeliveredRef,
    maxPartnerReadIdRef, maxPartnerDeliveredIdRef,
  }
}

export type MessengerKern = ReturnType<typeof useMessengerKern>
