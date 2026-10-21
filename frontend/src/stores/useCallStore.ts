/**
 * Anrufzustand. Direkt- und Gruppenanruf laufen durch denselben Code.
 *
 * Vorher stand hier ein eigener P2P-Aufbau mit `RTCPeerConnection`, einem
 * Signalisierungs-Socket, ICE-Kandidatenpuffer und einer Gnadenfrist gegen
 * Verbindungsabbrüche. Das fiel weg: ein Medienserver (LiveKit) nimmt die
 * Ströme entgegen und verteilt sie, und dessen Client bringt Aushandlung und
 * Wiederverbinden mit. Übrig bleibt die Frage, was die Oberfläche zeigt.
 *
 * Die Medien bleiben dabei Ende-zu-Ende verschlüsselt: der Raumschlüssel
 * entsteht im Browser und wird über den vorhandenen Umschlagsweg verteilt
 * (siehe `services/raumSchluessel.ts`).
 */


import { create } from 'zustand'
import i18n from '@/i18n'
import {
  beendeGruppenanruf,
  brichAnrufAb,
  holeAktivenAnruf,
  holeAusstehendeAnrufe,
  holeInAnruf,
  holeZugang,
  ladeZuAnrufEin,
  lehneAnrufAb,
  verlasseAnruf,
} from '@/api/calls'
import { useAuthStore } from '@/stores/authStore'
import { getDeviceId, getDeviceType } from '@/lib/deviceIdentity'
import { E2eeNichtUnterstuetzt, benutzerIdAusIdentity } from '@/services/livekitGrundlagen'
import type { RaumVerbindung } from '@/services/livekitRaum'
import {
  alsArrayBuffer,
  erzeugeRaumSchluessel,
  istSchluesselhalter,
  verteileAn,
  verteileAnAlle,
} from '@/services/raumSchluessel'
import { toneAbgang, toneAufgelegt, toneBeitritt } from '@/components/calling/anrufToene'
import { saveAudioSettings } from '@/lib/audioSettings'
import { toast } from '@/stores/toastStore'
import type { Participant, Room } from 'livekit-client'

import {
  anfangszustand,
  brecheCallTimersAb,
  brecheDisconnectTimerAb,
  istPartnerAngenommen,
  setzeAusgehendenTimer,
  setzeEingehendenTimer,
  setzePartnerAngenommen,
  istPartnerTransferiert,
  setzePartnerTransferiert,
  starteDisconnectGnadenfrist,
  starteHeartbeat,
  stoppeHeartbeat,
  type AnrufIdentitaet,
  type CallKind,
  type CallMode,
  type HinweisArt,
  type UseCallState,
} from '@/services/calling/callZustand'
import {
  HINWEIS_DAUER_MS,
  darfSenden,
  nameVon,
  sammleFreigaben,
  sammleTeilnehmer,
  sanitizeAvatarUrl,
  type Bekannte,
} from '@/services/calling/callTeilnehmer'
import { ladeLivekit, lk } from '@/services/calling/callZugriff'
import { baueGeraetewechsel } from '@/services/calling/callGeraetewechsel'

export type {
  CallHinweis,
  CallKind,
  CallMode,
  CallParticipant,
  CallPartner,
  CallScreenShare,
  CallState,
  GroupCallContext,
  HinweisArt,
  UseCallState,
} from '@/services/calling/callZustand'
export { sanitizeAvatarUrl } from '@/services/calling/callTeilnehmer'

/**
 * Wer ich bin und womit ich entschluessele. Wird von der Messenger-Seite
 * gesetzt, sobald der Schluesselbund aufgeschlossen ist.
 */
let identitaet: AnrufIdentitaet | null = null
export function setzeAnrufIdentitaet(werte: AnrufIdentitaet | null): void {
  identitaet = werte
}

/** Ausserhalb des Stores: React soll das LiveKit-Objekt nie neu rendern. */
let verbindung: RaumVerbindung | null = null
let raumSchluessel: Uint8Array | null = null
/**
 * Wer im laufenden Gruppenanruf die Gruppe verlassen hat oder hinausgeworfen
 * wurde. Er bekommt keinen Schluessel mehr und zaehlt bei der Frage, wer
 * Schluesselhalter ist, nicht mit.
 */
let entfernte = new Set<number>()
/** Zaehlt Verbindungsversuche, damit spaete Ruecklaeufer aus einem alten Anruf verpuffen. */
let generation = 0

export function aktiverRaum(): Room | null {
  return verbindung?.room ?? null
}
export const useCallStore = create<UseCallState>((set, get) => {
  /** Stammdaten (Name, Avatar) je Benutzer, aus Einladung bzw. Gruppenliste. */
  const bekannte: Bekannte = new Map()
  let sprechend = new Set<string>()
  let hinweisZaehler = 0

  const spiegele = () => {
    const room = verbindung?.room
    if (!room) return
    const serverStumm = !darfSenden(room.localParticipant, lk().Track.Source.Microphone)
    set((s) => ({
      participants: sammleTeilnehmer(room, bekannte, sprechend),
      screenShares: sammleFreigaben(room, bekannte),
      serverStumm,
      // Serverstumm heißt stumm — der Knopf soll das zeigen, auch wenn der
      // Benutzer selbst nichts gedrückt hat. Beim Aufheben bleibt es stumm:
      // wieder zu sprechen ist eine bewusste Entscheidung, kein Automatismus.
      isMuted: serverStumm ? true : s.isMuted,
    }))
  }

  /**
   * Eine Meldung fürs Anruffenster, die nach ein paar Sekunden von selbst geht.
   * Bewusst nicht in den Chatverlauf: wer wann telefoniert hat, soll dort
   * hinterher nicht nachlesbar sein.
   */
  const meldeHinweis = (art: HinweisArt, text: string) => {
    hinweisZaehler += 1
    const id = hinweisZaehler
    set((s) => ({ hinweise: [...s.hinweise, { id, art, text }] }))
    window.setTimeout(() => get().verwirfHinweis(id), HINWEIS_DAUER_MS)
  }

  const haengeEreignisseAn = (room: Room, eigeneGeneration: number) => {
    // Rest-Parameter, damit derselbe Handler an Ereignisse unterschiedlicher
    // Signatur passt: keines davon interessiert uns im Einzelnen, wir lesen
    // hinterher ohnehin den ganzen Raumzustand neu.
    const wennAktuell = (fn: () => void) => (..._args: unknown[]) => {
      if (eigeneGeneration === generation) fn()
    }

    room.on(lk().RoomEvent.Connected, wennAktuell(() => {
      set({ state: 'active', reconnecting: false, errorMessage: null })
      spiegele()
    }))
    room.on(lk().RoomEvent.Reconnecting, wennAktuell(() => set({ reconnecting: true })))
    room.on(lk().RoomEvent.Reconnected, wennAktuell(() => {
      set({ reconnecting: false })
      spiegele()
    }))
    room.on(lk().RoomEvent.Disconnected, wennAktuell(() => {
      if (get().state !== 'idle') get().endCall()
    }))

    room.on(lk().RoomEvent.ParticipantConnected, (teilnehmer) => {
      if (eigeneGeneration !== generation) return
      setzePartnerTransferiert(false)
      setzePartnerAngenommen(true)
      brecheDisconnectTimerAb()
      brecheCallTimersAb()
      set({ state: 'active' })
      spiegele()
      toneBeitritt()
      meldeHinweis('beitritt', `${nameVon(teilnehmer, bekannte)} ist beigetreten`)
      // Nachzügler brauchen den Raumschlüssel. Genau ein Anwesender schickt ihn.
      void schickeSchluesselNach(teilnehmer)
    })
    room.on(lk().RoomEvent.ParticipantDisconnected, (teilnehmer) => {
      if (eigeneGeneration !== generation) return
      spiegele()
      // Zweiergespräch: geht der andere, ist das Gespräch vorbei.
      // Außer wenn ein Gerätewechsel (Handoff) signalisiert wurde: dann geben wir
      // eine Gnadenfrist (8s), damit das neue Gerät die Verbindung übernehmen kann.
      if (get().kind === 'direkt' && (verbindung?.room.remoteParticipants.size ?? 0) === 0) {
        if (istPartnerTransferiert()) {
          meldeHinweis('info', 'Verbindung wird wiederhergestellt...')
          starteDisconnectGnadenfrist(() => {
            if (eigeneGeneration !== generation) return
            if (get().kind === 'direkt' && (verbindung?.room.remoteParticipants.size ?? 0) === 0) {
              get().endCall()
            }
          })
          return
        }
        get().endCall()
        return
      }
      toneAbgang()
      meldeHinweis('abgang', `${nameVon(teilnehmer, bekannte)} hat den Anruf verlassen`)
    })

    // Ein Moderator hat die erlaubten Quellen geändert. Ohne diese Zeile bliebe
    // der Mikrofonknopf bedienbar und täte nichts.
    room.on(lk().RoomEvent.ParticipantPermissionsChanged, wennAktuell(spiegele))
    room.on(lk().RoomEvent.ParticipantMetadataChanged, wennAktuell(spiegele))

    // Der Browser hat die Wiedergabe freigegeben oder verweigert.
    room.on(lk().RoomEvent.AudioPlaybackStatusChanged, wennAktuell(() => {
      set({ audioBlockiert: !room.canPlaybackAudio })
    }))

    const beiSpur = wennAktuell(spiegele)
    room.on(lk().RoomEvent.TrackSubscribed, beiSpur)
    room.on(lk().RoomEvent.TrackUnsubscribed, beiSpur)
    room.on(lk().RoomEvent.TrackPublished, beiSpur)
    room.on(lk().RoomEvent.TrackUnpublished, beiSpur)
    room.on(lk().RoomEvent.TrackMuted, beiSpur)
    room.on(lk().RoomEvent.TrackUnmuted, beiSpur)
    room.on(lk().RoomEvent.LocalTrackPublished, beiSpur)
    room.on(lk().RoomEvent.LocalTrackUnpublished, beiSpur)

    room.on(lk().RoomEvent.ActiveSpeakersChanged, (redner) => {
      if (eigeneGeneration !== generation) return
      sprechend = new Set(redner.map((r) => r.identity))
      spiegele()
    })
  }

  /** Die Benutzerkennungen im Raum, ohne Entfernte. */
  const anwesendeIds = (room: Room): number[] =>
    [room.localParticipant, ...room.remoteParticipants.values()]
      .map((t) => benutzerIdAusIdentity(t.identity))
      .filter((id): id is number => id !== null && !entfernte.has(id))

  /**
   * Jemand ist aus der Gruppe raus, der Anruf läuft weiter: neuer Schlüssel.
   * Den tauscht genau einer, der Schlüsselhalter unter den Verbliebenen; die
   * anderen nehmen ihn nach derselben Regel an (`acceptRoomKey`).
   */
  const tauscheSchluesselNachAustritt = async (weg: number) => {
    const room = verbindung?.room
    const zustand = get()
    if (!room || !identitaet || !zustand.raum || zustand.kind !== 'gruppe') return
    entfernte.add(weg)
    const anwesende = anwesendeIds(room)
    if (!istSchluesselhalter(identitaet.userId, anwesende, weg)) return
    const neuer = erzeugeRaumSchluessel()
    raumSchluessel = neuer
    if (verbindung) await lk().setzeRaumSchluessel(verbindung, alsArrayBuffer(neuer))
    const ziele = anwesende.filter((id) => id !== identitaet!.userId)
    if (ziele.length) void verteileAnAlle(zustand.raum, neuer, ziele, identitaet.publicKeyJwk)
  }

  const schickeSchluesselNach = async (neuer: Participant) => {
    const zustand = get()
    const neueId = benutzerIdAusIdentity(neuer.identity)
    const room = verbindung?.room
    if (!room || !raumSchluessel || !identitaet || !zustand.raum || neueId === null) return
    if (entfernte.has(neueId)) return
    const anwesende = anwesendeIds(room)
    if (!istSchluesselhalter(identitaet.userId, anwesende, neueId)) return
    await verteileAn(zustand.raum, raumSchluessel, neueId, identitaet.publicKeyJwk)
  }

  /**
   * Holt ein Token, verbindet und schaltet Mikrofon (und ggf. Kamera) frei.
   * Jeder Fehler landet als Satz im Overlay, nicht als stilles Auflegen.
   */
  const verbindeMitRaum = async (
    art: CallKind,
    raum: string,
    gruppenId: number | undefined,
    mode: CallMode,
  ) => {
    generation += 1
    const eigeneGeneration = generation
    if (!raumSchluessel) raumSchluessel = erzeugeRaumSchluessel()

    let zugang
    try {
      zugang = await holeZugang(art, raum, gruppenId, {
        device_id: getDeviceId(),
        device_type: getDeviceType(),
        mode,
      })
    } catch (fehler) {
      meldeFehler(fehler, 'zugang')
      throw fehler
    }
    if (eigeneGeneration !== generation) return

    try {
      verbindung = await (await ladeLivekit()).verbinde(zugang.url, zugang.token, alsArrayBuffer(raumSchluessel))
    } catch (fehler) {
      meldeFehler(fehler, 'verbindung')
      throw fehler
    }
    if (eigeneGeneration !== generation) {
      await lk().trenne(verbindung.room)
      verbindung = null
      return
    }

    haengeEreignisseAn(verbindung.room, eigeneGeneration)
    set({ audioBlockiert: !(await lk().erlaubeWiedergabe(verbindung.room)) })

    try {
      await lk().setzeMikrofon(verbindung.room, true)
      if (mode === 'video') await lk().setzeKamera(verbindung.room, true)
    } catch (fehler) {
      // Verbunden, aber ohne eigenes Mikrofon: hörbar bleibt der Anruf trotzdem.
      // Der Benutzer soll wissen, warum ihn niemand hört.
      meldeFehler(fehler, 'geraet')
      set({ isMuted: true })
    }
    set({
      state: 'active',
      isCameraOff: mode !== 'video',
      crossDeviceCall: null,
    })
    starteHeartbeat(raum)
    spiegele()
  }

  const meldeFehler = (fehler: unknown, phase: 'zugang' | 'verbindung' | 'geraet') => {
    if (fehler instanceof E2eeNichtUnterstuetzt) {
      set({ errorMessage: fehler.message })
      toast.error(fehler.message)
      return
    }
    const name = (fehler as { name?: string } | null)?.name ?? ''
    let text: string
    if (phase === 'geraet') {
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        text = 'Mikrofon oder Kamera sind blockiert. Bitte in den System- bzw. Browsereinstellungen freigeben.'
      } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
        text = 'Kein Mikrofon oder keine Kamera gefunden. Bitte Gerät prüfen.'
      } else {
        text = 'Mikrofon oder Kamera konnten nicht geöffnet werden.'
      }
    } else if (phase === 'zugang') {
      text = 'Der Anrufserver ist gerade nicht erreichbar. Bitte später erneut versuchen.'
    } else {
      text = 'Die Verbindung zum Anrufserver kam nicht zustande. Bitte Netzwerk prüfen.'
    }
    set({ errorMessage: text })
    toast.error(text)
  }

  const raeumeAuf = () => {
    stoppeHeartbeat()
    brecheCallTimersAb()
    setzePartnerTransferiert(false)
    setzePartnerAngenommen(false)
    brecheDisconnectTimerAb()
    generation += 1
    const room = verbindung?.room
    verbindung = null
    raumSchluessel = null
    entfernte = new Set<number>()
    sprechend = new Set<string>()
    bekannte.clear()
    set({ hinweise: [], audioBlockiert: false, serverStumm: false })
    if (room) void lk().trenne(room)
  }



  const wechsel = baueGeraetewechsel({
    get,
    set,
    raeumeAuf,
    meldeHinweis,
    verbindeMitRaum,
    tauscheSchluesselNachAustritt,
    spiegele,
    anwesendeIds: () => {
      const room = verbindung?.room
      return room ? anwesendeIds(room) : []
    },
    istEntfernt: (id) => entfernte.has(id),
    aktuellerRaumSchluessel: () => raumSchluessel,
    setzeRaumSchluessel: (schluessel) => {
      raumSchluessel = schluessel
    },
    uebernimmRaumSchluessel: async (schluessel) => {
      raumSchluessel = schluessel
      if (verbindung) await lk().setzeRaumSchluessel(verbindung, alsArrayBuffer(schluessel))
    },
    identitaet: () => identitaet,
    bekannte,
    raum: () => verbindung?.room ?? null,
  })

  const start = anfangszustand()

  return {
    ...start,
    ...wechsel,

    initiateCall: async (partner, mode) => {
      raeumeAuf()
      bekannte.set(partner.userId, { username: partner.username, avatarUrl: partner.avatarUrl })
      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        bekannte.set(currentUser.id, { username: currentUser.username, avatarUrl: currentUser.avatar_url })
      } else if (identitaet) {
        bekannte.set(identitaet.userId, bekannte.get(identitaet.userId) ?? { username: 'Ich' })
      }
      setzeAusgehendenTimer(() => {
        const hatFremde = (verbindung?.room.remoteParticipants.size ?? 0) > 0 ||
          get().participants.some((p) => !p.isSelf)
        if (get().state === 'outgoing' || !hatFremde) {
          get().endCall()
          toast.info(i18n.t('calls.nobodyAnswered'))
        }
      })
      const einladung = await ladeZuAnrufEin(partner.userId, mode)
      set({
        state: 'outgoing',
        kind: 'direkt',
        mode,
        partner,
        group: null,
        raum: einladung.signaling_token,
        participants: [],
        screenShares: [],
        focusedShareIdentity: null,
        isMuted: false,
        isDeafened: false,
        isCameraOff: mode !== 'video',
        isScreenSharing: false,
        callDurationSeconds: 0,
        reconnecting: false,
        errorMessage: null,
      })

      // Der Raumschlüssel geht sofort hinaus, nicht erst beim Beitreten: so
      // liegt er schon bereit, wenn abgenommen wird, und die ersten Sekunden
      // sind nicht stumm.
      raumSchluessel = erzeugeRaumSchluessel()
      if (identitaet) {
        void verteileAn(
          einladung.signaling_token,
          raumSchluessel,
          partner.userId,
          identitaet.publicKeyJwk,
        )
      }

      try {
        await verbindeMitRaum('direkt', einladung.signaling_token, undefined, mode)
      } catch {
        if (get().state !== 'idle') get().endCall()
      }
    },

    receiveCall: (partner, mode, raum) => {
      // Besetzt: ein zweiter Anruf wird abgelehnt und nur gemeldet. Bis
      // 26.09.2026 räumte `raeumeAuf` hier den laufenden Raum ab, und wer
      // gerade telefonierte, flog aus seinem Gespräch.
      const jetzt = get()
      if (jetzt.state !== 'idle') {
        if (jetzt.raum !== raum) {
          void lehneAnrufAb(raum).catch(() => {})
          toast.info(i18n.t('calls.missedWhileBusy', { name: partner.username }))
        }
        return
      }
      raeumeAuf()
      bekannte.set(partner.userId, { username: partner.username, avatarUrl: partner.avatarUrl })
      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        bekannte.set(currentUser.id, { username: currentUser.username, avatarUrl: currentUser.avatar_url })
      }
      setzeEingehendenTimer(() => {
        if (get().state === 'incoming') {
          get().rejectCall()
          toast.info(i18n.t('calls.callMissed'))
        }
      })
      set({
        state: 'incoming',
        kind: 'direkt',
        mode,
        partner,
        group: null,
        raum,
        participants: [],
        screenShares: [],
        focusedShareIdentity: null,
        isMuted: false,
        isDeafened: false,
        isCameraOff: mode !== 'video',
        isScreenSharing: false,
        callDurationSeconds: 0,
        reconnecting: false,
        errorMessage: null,
      })
    },

    acceptCall: async () => {
      brecheCallTimersAb()
      setzePartnerAngenommen(true)
      const { raum, mode } = get()
      if (!raum) return
      set({ state: 'connecting' })
      try {
        await verbindeMitRaum('direkt', raum, undefined, mode)
      } catch {
        get().endCall()
      }
    },

    rejectCall: () => {
      brecheCallTimersAb()
      const raum = get().raum
      if (raum) void lehneAnrufAb(raum).catch(() => {})
      get().endCall()
    },

    joinGroupCall: async (group, raum, mitgliederIds) => {
      raeumeAuf()
      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        bekannte.set(currentUser.id, { username: currentUser.username, avatarUrl: currentUser.avatar_url })
      }
      set({
        state: 'connecting',
        kind: 'gruppe',
        mode: 'audio',
        partner: null,
        group,
        raum,
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

      raumSchluessel = erzeugeRaumSchluessel()
      // Der Startende verteilt den Schlüssel an alle, die beitreten dürfen.
      // Wer später kommt, bekommt ihn über `ParticipantConnected` nachgereicht.
      if (identitaet && mitgliederIds?.length) {
        const ziele = mitgliederIds.filter((id) => id !== identitaet!.userId)
        void verteileAnAlle(raum, raumSchluessel, ziele, identitaet.publicKeyJwk)
      }

      try {
        await verbindeMitRaum('gruppe', raum, group.id, 'audio')
      } catch {
        get().endCall()
      }
    },

    endCall: () => {
      const { raum, state, kind, group } = get()
      // Nur wenn wirklich ein Gespräch lief: beim Wegdrücken eines eingehenden
      // Anrufs klingelt es schon, da wäre ein Auflegeton nur Lärm.
      if (state === 'active' || state === 'connecting' || state === 'outgoing') toneAufgelegt()
      if (raum && kind === 'direkt') {
        // Auflegen, während es beim Gegenüber noch klingelt: die Einladung muss
        // serverseitig sterben, sonst klingelt es dort weiter.
        // Bei einem bereits verbundenen Anruf wird die Einladung nicht mehr abgebrochen,
        // sondern der Anruf regulär über verlasseAnruf verlassen.
        if (state === 'outgoing' || !istPartnerAngenommen()) {
          void brichAnrufAb(raum).catch(() => {})
        }
      }
      if (kind === 'gruppe' && raum && group && group.canModerate) {
        void beendeGruppenanruf(group.id, raum).catch(() => {})
      }
      if (raum) {
        void verlasseAnruf(raum, getDeviceId()).catch(() => {})
      }
      raeumeAuf()
      set({
        state: 'idle',
        partner: null,
        group: null,
        raum: null,
        participants: [],
        screenShares: [],
        focusedShareIdentity: null,
        callDurationSeconds: 0,
        isScreenSharing: false,
        isDeafened: false,
        isMuted: false,
        isCameraOff: true,
        reconnecting: false,
        mode: 'audio',
      })
    },

    toggleMute: () => {
      // Serverstumm hebt nur auf, wer es gesetzt hat. Der Versuch scheiterte
      // ohnehin am Server; hier scheitert er sichtbar und ohne Zustandswechsel.
      if (get().serverStumm) {
        toast.error(i18n.t('calls.moderatorMuted'))
        return
      }
      const naechster = !get().isMuted
      const room = verbindung?.room
      if (room) void lk().setzeMikrofon(room, !naechster).catch(() => {})
      // Aus der Taubheit heraus das Mikrofon einzuschalten hebt sie auf: alles
      // andere wäre ein Zustand, in dem man spricht und nichts hört.
      set(naechster ? { isMuted: true } : { isMuted: false, isDeafened: false })
      if (!naechster && room) lk().setzeTaub(room, false)
    },

    toggleDeafen: () => {
      const naechster = !get().isDeafened
      const room = verbindung?.room
      if (room) {
        lk().setzeTaub(room, naechster)
        void lk().setzeMikrofon(room, !naechster).catch(() => {})
      }
      set({ isDeafened: naechster, isMuted: naechster })
    },

    toggleCamera: () => {
      const naechster = !get().isCameraOff
      const room = verbindung?.room
      if (!room) {
        set({ isCameraOff: naechster })
        return
      }
      void lk().setzeKamera(room, !naechster)
        .then(() => {
          set({ isCameraOff: naechster, mode: naechster ? get().mode : 'video' })
          spiegele()
        })
        .catch((fehler) => meldeFehler(fehler, 'geraet'))
    },

    startScreenShare: async (optionen) => {
      const room = verbindung?.room
      if (!room) return
      set({ screenShareOptions: optionen })
      try {
        await lk().starteBildschirmfreigabe(room, optionen)
        set({ isScreenSharing: true })
        spiegele()
      } catch (fehler) {
        const name = (fehler as { name?: string } | null)?.name ?? ''
        // Abbrechen im Auswahldialog des Browsers ist kein Fehler.
        if (name === 'NotAllowedError' || name === 'AbortError') return
        toast.error(i18n.t('calls.screenshareFailed'))
      }
    },

    stopScreenShare: async () => {
      const room = verbindung?.room
      if (!room) return
      await lk().beendeBildschirmfreigabe(room).catch(() => {})
      set({ isScreenSharing: false })
      spiegele()
    },

    setFocusedShare: (identity) => set({ focusedShareIdentity: identity }),

    setParticipantVolume: (identity, wert) => {
      const room = verbindung?.room
      if (room) lk().setzeLautstaerke(room, identity, wert)
      set((s) => ({
        participants: s.participants.map((t) =>
          t.identity === identity ? { ...t, volume: wert } : t,
        ),
      }))
    },

    inviteToCall: async (userId) => {
      const { raum, mode, kind } = get()
      if (!raum || kind !== 'direkt') return
      await holeInAnruf(raum, userId, mode)
      if (raumSchluessel && identitaet) {
        await verteileAn(raum, raumSchluessel, userId, identitaet.publicKeyJwk)
      }
    },

    setDevices: (audioIn, videoIn, audioOut) => {
      const room = verbindung?.room
      if (room) {
        if (audioIn) void lk().wechsleGeraet(room, 'audioinput', audioIn).catch(() => {})
        if (videoIn) void lk().wechsleGeraet(room, 'videoinput', videoIn).catch(() => {})
        if (audioOut) void lk().wechsleGeraet(room, 'audiooutput', audioOut).catch(() => {})
      }
      // Dieselbe Ablage wie Profil → Audio. Ein Anruf, der sein Gerät nur für
      // sich merkt, wäre die zweite Wahrheit neben dem Mikrofontest.
      if (audioIn || audioOut) {
        saveAudioSettings({
          ...(audioIn ? { preferredMicId: audioIn } : {}),
          ...(audioOut ? { preferredSpeakerId: audioOut } : {}),
        })
      }
      set((s) => ({
        selectedAudioInput: audioIn ?? s.selectedAudioInput,
        selectedVideoInput: videoIn ?? s.selectedVideoInput,
        selectedAudioOutput: audioOut ?? s.selectedAudioOutput,
      }))
    },

    incrementDuration: () => set((s) => ({ callDurationSeconds: s.callDurationSeconds + 1 })),

    erlaubeTon: async () => {
      const room = verbindung?.room
      if (!room) return
      set({ audioBlockiert: !(await lk().erlaubeWiedergabe(room)) })
    },

    verwirfHinweis: (id) => set((s) => ({ hinweise: s.hinweise.filter((h) => h.id !== id) })),


    setMode: (mode) => set({ mode }),

    checkActiveCall: async () => {
      try {
        const res = await holeAktivenAnruf()
        if (res.has_active_call && res.call && get().state === 'idle') {
          set({ crossDeviceCall: res.call })
        } else {
          set({ crossDeviceCall: null })
        }
      } catch {
        // Nicht fatal bei Netzwerkfehlern
      }

      try {
        const pending = await holeAusstehendeAnrufe()
        if (pending.group_calls) {
          set({ activeGroupCalls: pending.group_calls })
        }
        if (get().state === 'incoming') {
          const isStillPending = pending.has_pending_call && pending.call && pending.call.signaling_token === get().raum
          if (!isStillPending) {
            brecheCallTimersAb()
            set({
              state: 'idle',
              partner: null,
              raum: null,
            })
          }
        } else if (pending.has_pending_call && pending.call && get().state === 'idle') {
          const c = pending.call
          get().receiveCall(
            {
              userId: c.caller_id,
              username: c.caller_username,
              avatarUrl: sanitizeAvatarUrl(c.caller_avatar_url) ?? null,
            },
            c.mode === 'video' ? 'video' : 'audio',
            c.signaling_token,
          )
        }
      } catch {
        // Nicht fatal bei Netzwerkfehlern
      }
    },

  }
})

useAuthStore.subscribe((state, prevState) => {
  if (prevState.user && !state.user) {
    setzeAnrufIdentitaet(null)
  }
})

// Im Entwicklungsmodus von der Konsole aus erreichbar. Ein Anruffenster mit
// acht Teilnehmern laesst sich sonst nur mit acht Konten ansehen, und genau
// dort faellt auf, ob eine Kachel aus ihrem Bereich laeuft.
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__msmAnruf = useCallStore
}
