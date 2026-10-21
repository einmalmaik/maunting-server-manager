/**
 * Geraetewechsel und Raumschluessel: was zwischen zwei Geraeten desselben
 * Kontos laeuft.
 *
 * Der Store reicht hier nur seine Umgebung hinein. Die Regel, wer einen neuen
 * Raumschluessel schicken darf, steht unveraendert in `acceptRoomKey`: im
 * Direktchat die kleinere Kennung, in der Gruppe der Schluesselhalter.
 */

import i18n from '@/i18n'
import { beendeAktivenAnrufRemote } from '@/api/calls'
import type { ActiveCallInfo } from '@/api/calls'
import { getDeviceId } from '@/lib/deviceIdentity'
import { entpacke, erzeugeRaumSchluessel, istSchluesselhalter, verteileAn } from '@/services/raumSchluessel'
import { toneUebergabe } from '@/components/calling/anrufToene'
import { toast } from '@/stores/toastStore'
import { useAuthStore } from '@/stores/authStore'
import type { Room } from 'livekit-client'
import {
  istPartnerAngenommen,
  setzePartnerAngenommen,
  setzePartnerTransferiert,
  type AnrufIdentitaet,
  type CallKind,
  type CallMode,
  type HinweisArt,
  type UseCallState,
} from './callZustand'
import type { Bekannte } from './callTeilnehmer'

/** Was die Geraetewechsel-Regeln aus dem Store brauchen. */
export interface AnrufUmgebung {
  get: () => UseCallState
  set: (
    teil: Partial<UseCallState> | ((s: UseCallState) => Partial<UseCallState>),
  ) => void
  raeumeAuf: () => void
  meldeHinweis: (art: HinweisArt, text: string) => void
  verbindeMitRaum: (
    art: CallKind,
    raum: string,
    gruppenId: number | undefined,
    mode: CallMode,
  ) => Promise<void>
  tauscheSchluesselNachAustritt: (weg: number) => Promise<void>
  spiegele: () => void
  anwesendeIds: () => number[]
  istEntfernt: (id: number) => boolean
  aktuellerRaumSchluessel: () => Uint8Array | null
  setzeRaumSchluessel: (schluessel: Uint8Array | null) => void
  uebernimmRaumSchluessel: (schluessel: Uint8Array) => Promise<void>
  identitaet: () => AnrufIdentitaet | null
  bekannte: Bekannte
  raum: () => Room | null
}

/** Baut die Wechsel-Aktionen fuer den Store. */
export function baueGeraetewechsel(umgebung: AnrufUmgebung) {
  const {
    get,
    set,
    raeumeAuf,
    meldeHinweis,
    verbindeMitRaum,
    tauscheSchluesselNachAustritt,
    spiegele,
    anwesendeIds,
    istEntfernt,
    aktuellerRaumSchluessel,
    setzeRaumSchluessel,
    uebernimmRaumSchluessel,
    identitaet,
    bekannte,
  } = umgebung


  const acceptRoomKey = async (raum: string, ciphertext: string, fromUserId?: number) => {
      const meineIdentitaet = identitaet()
      if (!meineIdentitaet || get().raum !== raum) return

      // H-4: Schutz vor unberechtigter Schlüsselübernahme.
      // Wenn bereits ein Raumschlüssel aktiv ist, darf nur der nach der
      // lokalen Regel zuständige Schlüsselhalter einen neuen Schlüssel schicken.
      // Fehlt fromUserId oder ist der Absender nicht zuständig, wird der Wechsel abgewiesen.
      if (aktuellerRaumSchluessel()) {
        if (fromUserId === undefined) {
          console.warn('[Call] Room key replacement rejected: missing fromUserId')
          return
        }
        const zustand = get()
        let zustaendig = false
        if (zustand.kind === 'direkt') {
          // Im Direktchat ist nur der Partner berechtigt.
          // Existiert bereits ein Schlüssel, ist die kleinere Benutzerkennung der Schlüsselhalter.
          const partnerId = zustand.partner?.userId
          if (fromUserId === partnerId) {
            zustaendig = Math.min(meineIdentitaet.userId, partnerId) === fromUserId
          }
        } else {
          // Im Gruppenanruf gilt die istSchluesselhalter-Regel über alle anwesenden Teilnehmer.
          if (istEntfernt(fromUserId)) return
          const anwesende = anwesendeIds()
          const liste = anwesende.length > 0 ? anwesende : [meineIdentitaet.userId, fromUserId]
          zustaendig = istSchluesselhalter(fromUserId, liste, meineIdentitaet.userId)
        }

        if (!zustaendig) {
          console.warn('[Call] Room key replacement rejected from user:', fromUserId)
          toast.error(
            i18n.t('calls.unauthorizedKeyExchange', {
              defaultValue: 'Ein unberechtigter Schlüsselwechsel für den Anruf wurde abgewiesen.',
            }),
          )
          return
        }
      }

      try {
        const schluessel = await entpacke(ciphertext, meineIdentitaet.decryptionKeys)
        await uebernimmRaumSchluessel(schluessel)
        spiegele()
      } catch {
        // Ein Umschlag, den wir nicht öffnen können, ist nicht für uns. Der
        // Anruf läuft weiter; ohne passenden Schlüssel bleibt er still, und
        // genau das zeigt die Oberfläche über `isPending` an.
      }
  }

  const transferCallToThisDevice = async () => {
      const { crossDeviceCall } = get()
      if (!crossDeviceCall) return
      const target = crossDeviceCall
      raeumeAuf()
      setzePartnerAngenommen(true)
      set({ crossDeviceCall: null })

      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        bekannte.set(currentUser.id, { username: currentUser.username, avatarUrl: currentUser.avatar_url })
      }

      if (target.art === 'direkt' && target.partner) {
        bekannte.set(target.partner.user_id, {
          username: target.partner.username,
          avatarUrl: target.partner.avatar_url,
        })
        const meins = identitaet()
        if (meins) {
          bekannte.set(meins.userId, bekannte.get(meins.userId) ?? { username: 'Ich' })
        }
        set({
          state: 'connecting',
          kind: 'direkt',
          mode: target.mode,
          partner: {
            userId: target.partner.user_id,
            username: target.partner.username,
            avatarUrl: target.partner.avatar_url,
          },
          group: null,
          raum: target.raum,
          participants: [],
          screenShares: [],
          focusedShareIdentity: null,
          isMuted: false,
          isDeafened: false,
          isCameraOff: target.mode !== 'video',
          isScreenSharing: false,
          callDurationSeconds: 0,
          reconnecting: false,
          errorMessage: null,
        })
        const meineIdentitaet = identitaet()
        const schluessel = aktuellerRaumSchluessel() ?? erzeugeRaumSchluessel()
        if (!aktuellerRaumSchluessel()) setzeRaumSchluessel(schluessel)
        if (meineIdentitaet) {
          void verteileAn(target.raum, schluessel, target.partner.user_id, meineIdentitaet.publicKeyJwk)
        }
        try {
          await verbindeMitRaum('direkt', target.raum, undefined, target.mode)
          toneUebergabe()
        } catch {
          get().endCall()
        }
      } else if (target.art === 'gruppe' && target.group_id) {
        // Der Name kommt aus dem versiegelten oertlichen Namensspeicher: seit
        // Stufe 6c kennt der Server ihn nicht mehr und schickt ihn auch nicht
        // mit. Kennt dieses Geraet die Gruppe nicht, bleibt es beim Platzhalter.
        const gruppenName =
          (
            await import('@/services/gruppenName')
              .then((m) => m.ladeGruppenNamen())
              .catch(() => null)
          )
            ?.get(target.group_id)
            ?.name?.trim() || ''
        set({
          state: 'connecting',
          kind: 'gruppe',
          mode: 'audio',
          partner: null,
          group: {
            id: target.group_id,
            name: gruppenName || i18n.t('messenger.groupSealed'),
            canShare: true,
            canModerate: false,
          },
          raum: target.raum,
          participants: [],
          screenShares: [],
          focusedShareIdentity: null,
          isMuted: false,
          isDeafened: false,
          isCameraOff: true,
          isScreenSharing: false,
          callDurationSeconds: 0,
          reconnecting: false,
          errorMessage: null,
        })
        try {
          await verbindeMitRaum('gruppe', target.raum, target.group_id, 'audio')
          toneUebergabe()
        } catch {
          get().endCall()
        }
      }
  }

  const terminateCrossDeviceCall = async () => {
      set({ crossDeviceCall: null })
      try {
        await beendeAktivenAnrufRemote()
      } catch {
        // Nicht fatal
      }
  }

  const handleCrossDeviceEvent = (detail: unknown) => {
      if (!detail || typeof detail !== 'object') return
      const ev = detail as { type?: string; [key: string]: unknown }
      const myId = getDeviceId()

      if (ev.type === 'user_call_state_changed') {
        const active = ev.active_call as ActiveCallInfo | null | undefined
        if (active && get().state === 'idle') {
          set({ crossDeviceCall: active })
        } else {
          set({ crossDeviceCall: null })
        }
      } else if (ev.type === 'call_transferred') {
        if (ev.old_device_id === myId && get().state !== 'idle') {
          get().endCall()
          toast.info(i18n.t('calls.callTransferred'))
        }
      } else if (ev.type === 'call_partner_transferred') {
        if (get().state === 'active' && (!ev.raum || get().raum === ev.raum)) {
          setzePartnerTransferiert(true)
          meldeHinweis('info', String(ev.message || 'Gesprächspartner wechselt das Gerät...'))
          toneUebergabe()
        }
      } else if (ev.type === 'call_superseded') {
        if (get().state !== 'idle' && (!ev.old_raum || get().raum === ev.old_raum)) {
          get().endCall()
          toast.info(i18n.t('calls.joinedOnOtherDevice'))
        }
      } else if (ev.type === 'call_ended_remotely') {
        if (get().state !== 'idle' && (!ev.raum || get().raum === ev.raum)) {
          get().endCall()
          toast.info(i18n.t('calls.callEnded'))
        }
      }
  }

  const handleCallSyncEvent = (detail: unknown) => {
      if (!detail || typeof detail !== 'object') return
      const ev = detail as { type?: string; [key: string]: unknown }
      const currentUserId = useAuthStore.getState().user?.id

      if (ev.type === 'direct_call_invitation') {
        if (
          ev.recipient_id &&
          currentUserId &&
          Number(ev.recipient_id) !== Number(currentUserId)
        ) {
          return
        }
        if (ev.signaling_token && ev.caller_id && ev.caller_username) {
          if (get().state === 'incoming' && get().raum === ev.signaling_token) {
            return
          }
          get().receiveCall(
            {
              userId: Number(ev.caller_id),
              username: String(ev.caller_username),
              avatarUrl: (ev.caller_avatar_url as string | null | undefined) ?? null,
            },
            ev.mode === 'video' ? 'video' : 'audio',
            String(ev.signaling_token),
          )
        }
      } else if (ev.type === 'direct_call_rejected') {
        /**
         * Abgelehnt ist abgelehnt — auch beim Anrufer.
         *
         * Hier standen zwei Bedingungen, die beide nie zutrafen. `recipient_id`
         * nannte in diesem Ereignis den Ablehnenden, nicht wie bei Einladung und
         * Abbruch den Empfänger des Ereignisses; der Vergleich mit dem eigenen
         * Konto schlug deshalb immer fehl. Und `state` steht längst auf `active`:
         * der Anrufer betritt den Raum, sobald er klingeln lässt, und wartet dort
         * allein. Das Ergebnis war ein Anrufer, der nach der Ablehnung weiter im
         * Raum sass und von Hand auflegen musste.
         *
         * Der Raum ist die eindeutige Kennung, und das Ereignis geht ohnehin nur
         * an den Anrufer. `partnerAngenommen` schützt den Fall, dass jemand
         * nachgeholt wurde und ablehnt, während die beiden anderen sprechen.
         */
        const call = get()
        if (
          call.state !== 'idle' &&
          call.kind === 'direkt' &&
          !istPartnerAngenommen() &&
          (!ev.signaling_token || ev.signaling_token === call.raum)
        ) {
          call.endCall()
          toast.info(i18n.t('calls.callRejected'))
        }
      } else if (ev.type === 'direct_call_cancelled') {
        const call = get()
        if (
          (!ev.recipient_id || !currentUserId || Number(ev.recipient_id) === Number(currentUserId)) &&
          (call.state === 'incoming' || call.state === 'connecting') &&
          (!ev.signaling_token || ev.signaling_token === call.raum)
        ) {
          call.endCall()
          toast.info(i18n.t('calls.callerHungUp'))
        }
      } else if (ev.type === 'group_call_started') {
        if (ev.group_id && ev.room_token) {
          const groupId = Number(ev.group_id)
          const roomToken = String(ev.room_token)
          set((s) => {
            const exists = s.activeGroupCalls.some((g) => g.group_id === groupId)
            if (exists) {
              return {
                activeGroupCalls: s.activeGroupCalls.map((g) =>
                  g.group_id === groupId ? { ...g, room_token: roomToken } : g,
                ),
              }
            }
            return {
              activeGroupCalls: [
                ...s.activeGroupCalls,
                // Ohne Namen: das Ereignis trug nie einen, und seit Stufe 6c
                // hat der Server auch keinen mehr. Wer den Anruf anzeigt,
                // holt ihn aus dem oertlichen Namensspeicher.
                {
                  group_id: groupId,
                  room_token: roomToken,
                  participant_count: 1,
                },
              ],
            }
          })
        }
      } else if (ev.type === 'group_call_member_removed') {
        const call = get()
        const weg = Number(ev.user_id)
        if (!ev.room_token || call.raum !== String(ev.room_token) || call.kind !== 'gruppe') return
        if (weg === Number(currentUserId)) {
          // Ich bin raus: der Anruf endet auch hier.
          call.endCall()
          toast.info(i18n.t('calls.removedFromGroupCall'))
        } else {
          void tauscheSchluesselNachAustritt(weg)
        }
      } else if (ev.type === 'group_call_ended') {
        if (ev.group_id && ev.room_token) {
          const groupId = Number(ev.group_id)
          const roomToken = String(ev.room_token)
          set((s) => ({
            activeGroupCalls: s.activeGroupCalls.filter(
              (g) => !(g.group_id === groupId && g.room_token === roomToken),
            ),
          }))
          const call = get()
          if (call.group?.id === groupId && call.raum === roomToken) {
            call.endCall()
            toast.info(i18n.t('calls.groupCallEnded'))
          }
        }
      } else if (ev.type === 'call_key') {
        if (ev.raum && ev.ciphertext) {
          const fromUser =
            ev.from_user_id !== undefined && ev.from_user_id !== null
              ? Number(ev.from_user_id)
              : undefined
          void get().acceptRoomKey(String(ev.raum), String(ev.ciphertext), fromUser)
        }
      } else {
        get().handleCrossDeviceEvent(detail)
      }
  }

  return {
    acceptRoomKey,
    transferCallToThisDevice,
    terminateCrossDeviceCall,
    handleCrossDeviceEvent,
    handleCallSyncEvent,
  }
}
