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
import {
  beendeGruppenanruf,
  brichAnrufAb,
  holeInAnruf,
  holeZugang,
  ladeZuAnrufEin,
  lehneAnrufAb,
} from '@/api/calls'
import {
  E2eeNichtUnterstuetzt,
  FREIGABE_STANDARD,
  RoomEvent,
  Track,
  beendeBildschirmfreigabe,
  benutzerIdAusIdentity,
  erlaubeWiedergabe,
  setzeKamera,
  setzeLautstaerke,
  setzeMikrofon,
  setzeRaumSchluessel,
  setzeTaub,
  starteBildschirmfreigabe,
  trenne,
  verbinde,
  wechsleGeraet,
  type FreigabeOptionen,
  type RaumVerbindung,
} from '@/services/livekitRaum'
import {
  alsArrayBuffer,
  entpacke,
  erzeugeRaumSchluessel,
  istSchluesselhalter,
  verteileAn,
  verteileAnAlle,
} from '@/services/raumSchluessel'
import { toast } from '@/stores/toastStore'
import type { Participant, Room } from 'livekit-client'

export type CallState = 'idle' | 'outgoing' | 'incoming' | 'connecting' | 'active' | 'ended'
export type CallMode = 'audio' | 'video'
export type CallKind = 'direkt' | 'gruppe'

export interface CallPartner {
  userId: number
  username: string
  avatarUrl?: string | null
}

export interface CallParticipant {
  userId: number
  identity: string
  username: string
  avatarUrl?: string | null
  isSelf: boolean
  isMuted: boolean
  isCameraOff: boolean
  isSpeaking: boolean
  /** Verbunden, aber noch ohne Ton/Bild — meist fehlt der Raumschlüssel. */
  isPending: boolean
  videoTrack: Track | null
  volume: number
}

export interface CallScreenShare {
  identity: string
  userId: number
  ownerName: string
  track: Track
}

export interface GroupCallContext {
  id: number
  name: string
  avatarUrl?: string | null
  canShare: boolean
  canModerate: boolean
}

export interface UseCallState {
  state: CallState
  kind: CallKind
  mode: CallMode
  /** Gegenüber im Zweiergespräch; bei Gruppen `null`. */
  partner: CallPartner | null
  group: GroupCallContext | null
  /** Raumname und zugleich Einladungstoken. */
  raum: string | null
  participants: CallParticipant[]
  screenShares: CallScreenShare[]
  /** Auf welche Freigabe die Bühne zeigt. `null` = Raster. */
  focusedShareIdentity: string | null
  isMuted: boolean
  isDeafened: boolean
  isCameraOff: boolean
  isScreenSharing: boolean
  screenShareOptions: FreigabeOptionen
  selectedAudioInput: string
  selectedVideoInput: string
  selectedAudioOutput: string
  callDurationSeconds: number
  reconnecting: boolean
  errorMessage: string | null

  initiateCall: (partner: CallPartner, mode: CallMode) => Promise<void>
  receiveCall: (partner: CallPartner, mode: CallMode, raum: string) => void
  acceptCall: () => Promise<void>
  rejectCall: () => void
  joinGroupCall: (group: GroupCallContext, raum: string, mitgliederIds?: number[]) => Promise<void>
  endCall: () => void

  toggleMute: () => void
  toggleDeafen: () => void
  toggleCamera: () => void
  startScreenShare: (optionen: FreigabeOptionen) => Promise<void>
  stopScreenShare: () => Promise<void>
  setFocusedShare: (identity: string | null) => void
  setParticipantVolume: (identity: string, wert: number) => void
  inviteToCall: (userId: number) => Promise<void>
  setDevices: (audioIn?: string, videoIn?: string, audioOut?: string) => void
  incrementDuration: () => void
  /** Ein eingehender `call_key`-Umschlag aus dem Ereignisstrom. */
  acceptRoomKey: (raum: string, ciphertext: string) => Promise<void>
  setMode: (mode: CallMode) => void
}

/**
 * Wer ich bin und womit ich entschlüssle. Wird von der Messenger-Seite gesetzt,
 * sobald der Schlüsselbund aufgeschlossen ist. Der Store bekommt das injiziert,
 * statt selbst am Schlüsselbund zu hängen: er soll nicht wissen, wie eine
 * Identität zustande kommt.
 */
interface Identitaet {
  userId: number
  publicKeyJwk: string
  decryptionKeys: string[]
}

let identitaet: Identitaet | null = null
export function setzeAnrufIdentitaet(werte: Identitaet | null): void {
  identitaet = werte
}

/** Außerhalb des Stores: React soll das LiveKit-Objekt nie neu rendern. */
let verbindung: RaumVerbindung | null = null
let raumSchluessel: Uint8Array | null = null
/** Zählt Verbindungsversuche, damit späte Rückläufer aus einem alten Anruf verpuffen. */
let generation = 0

export function aktiverRaum(): Room | null {
  return verbindung?.room ?? null
}

// ── Hilfen zum Übersetzen von LiveKit-Zustand in Store-Zustand ──────────────

function anzeigename(teilnehmer: Participant): string {
  return teilnehmer.name || teilnehmer.identity
}

function videospur(teilnehmer: Participant): Track | null {
  const veroeffentlichung = teilnehmer.getTrackPublication(Track.Source.Camera)
  return veroeffentlichung?.track ?? null
}

function kameraAus(teilnehmer: Participant): boolean {
  const veroeffentlichung = teilnehmer.getTrackPublication(Track.Source.Camera)
  return !veroeffentlichung || veroeffentlichung.isMuted || !veroeffentlichung.track
}

function mikrofonAus(teilnehmer: Participant): boolean {
  const veroeffentlichung = teilnehmer.getTrackPublication(Track.Source.Microphone)
  return !veroeffentlichung || veroeffentlichung.isMuted
}

type Bekannte = Map<number, { username: string; avatarUrl?: string | null }>

function sammleTeilnehmer(room: Room, bekannte: Bekannte, sprechend: Set<string>): CallParticipant[] {
  const alle: Participant[] = [room.localParticipant, ...room.remoteParticipants.values()]
  return alle.map((teilnehmer) => {
    const userId = benutzerIdAusIdentity(teilnehmer.identity) ?? 0
    const stammdaten = bekannte.get(userId)
    const istSelbst = teilnehmer.identity === room.localParticipant.identity
    return {
      userId,
      identity: teilnehmer.identity,
      username: stammdaten?.username || anzeigename(teilnehmer),
      avatarUrl: stammdaten?.avatarUrl ?? null,
      isSelf: istSelbst,
      isMuted: mikrofonAus(teilnehmer),
      isCameraOff: kameraAus(teilnehmer),
      isSpeaking: sprechend.has(teilnehmer.identity),
      // Niemand hat eine Tonspur veröffentlicht und auch keine Kamera: das ist
      // der Zustand direkt nach dem Beitreten, bevor der Schlüssel da ist.
      isPending:
        !istSelbst &&
        teilnehmer.getTrackPublication(Track.Source.Microphone) === undefined &&
        teilnehmer.getTrackPublication(Track.Source.Camera) === undefined,
      videoTrack: videospur(teilnehmer),
      volume: 1,
    }
  })
}

function sammleFreigaben(room: Room, bekannte: Bekannte): CallScreenShare[] {
  const freigaben: CallScreenShare[] = []
  const alle: Participant[] = [room.localParticipant, ...room.remoteParticipants.values()]
  for (const teilnehmer of alle) {
    const veroeffentlichung = teilnehmer.getTrackPublication(Track.Source.ScreenShare)
    const spur = veroeffentlichung?.track
    if (!spur) continue
    const userId = benutzerIdAusIdentity(teilnehmer.identity) ?? 0
    freigaben.push({
      identity: teilnehmer.identity,
      userId,
      ownerName: bekannte.get(userId)?.username || anzeigename(teilnehmer),
      track: spur,
    })
  }
  return freigaben
}

export const useCallStore = create<UseCallState>((set, get) => {
  /** Stammdaten (Name, Avatar) je Benutzer, aus Einladung bzw. Gruppenliste. */
  const bekannte: Bekannte = new Map()
  let sprechend = new Set<string>()

  const spiegele = () => {
    const room = verbindung?.room
    if (!room) return
    set({
      participants: sammleTeilnehmer(room, bekannte, sprechend),
      screenShares: sammleFreigaben(room, bekannte),
    })
  }

  const haengeEreignisseAn = (room: Room, eigeneGeneration: number) => {
    // Rest-Parameter, damit derselbe Handler an Ereignisse unterschiedlicher
    // Signatur passt: keines davon interessiert uns im Einzelnen, wir lesen
    // hinterher ohnehin den ganzen Raumzustand neu.
    const wennAktuell = (fn: () => void) => (..._args: unknown[]) => {
      if (eigeneGeneration === generation) fn()
    }

    room.on(RoomEvent.Connected, wennAktuell(() => {
      // Sofort auf „aktiv": der Zähler soll laufen, sobald die Verbindung
      // steht, nicht erst wenn der erste fremde Ton eintrifft. Alles andere
      // sah aus wie ein Anruf, der nie zustande kommt.
      set({ state: 'active', reconnecting: false, errorMessage: null })
      spiegele()
    }))
    room.on(RoomEvent.Reconnecting, wennAktuell(() => set({ reconnecting: true })))
    room.on(RoomEvent.Reconnected, wennAktuell(() => {
      set({ reconnecting: false })
      spiegele()
    }))
    room.on(RoomEvent.Disconnected, wennAktuell(() => {
      if (get().state !== 'idle') get().endCall()
    }))

    room.on(RoomEvent.ParticipantConnected, (teilnehmer) => {
      if (eigeneGeneration !== generation) return
      spiegele()
      // Nachzügler brauchen den Raumschlüssel. Genau ein Anwesender schickt ihn.
      void schickeSchluesselNach(teilnehmer)
    })
    room.on(RoomEvent.ParticipantDisconnected, wennAktuell(() => {
      spiegele()
      // Zweiergespräch: geht der andere, ist das Gespräch vorbei.
      if (get().kind === 'direkt' && (verbindung?.room.remoteParticipants.size ?? 0) === 0) {
        get().endCall()
      }
    }))

    const beiSpur = wennAktuell(spiegele)
    room.on(RoomEvent.TrackSubscribed, beiSpur)
    room.on(RoomEvent.TrackUnsubscribed, beiSpur)
    room.on(RoomEvent.TrackPublished, beiSpur)
    room.on(RoomEvent.TrackUnpublished, beiSpur)
    room.on(RoomEvent.TrackMuted, beiSpur)
    room.on(RoomEvent.TrackUnmuted, beiSpur)
    room.on(RoomEvent.LocalTrackPublished, beiSpur)
    room.on(RoomEvent.LocalTrackUnpublished, beiSpur)

    room.on(RoomEvent.ActiveSpeakersChanged, (redner) => {
      if (eigeneGeneration !== generation) return
      sprechend = new Set(redner.map((r) => r.identity))
      spiegele()
    })
  }

  const schickeSchluesselNach = async (neuer: Participant) => {
    const zustand = get()
    const neueId = benutzerIdAusIdentity(neuer.identity)
    const room = verbindung?.room
    if (!room || !raumSchluessel || !identitaet || !zustand.raum || neueId === null) return
    const anwesende = [room.localParticipant, ...room.remoteParticipants.values()]
      .map((t) => benutzerIdAusIdentity(t.identity))
      .filter((id): id is number => id !== null)
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
      zugang = await holeZugang(art, raum, gruppenId)
    } catch (fehler) {
      meldeFehler(fehler, 'zugang')
      throw fehler
    }
    if (eigeneGeneration !== generation) return

    try {
      verbindung = await verbinde(zugang.url, zugang.token, alsArrayBuffer(raumSchluessel))
    } catch (fehler) {
      meldeFehler(fehler, 'verbindung')
      throw fehler
    }
    if (eigeneGeneration !== generation) {
      await trenne(verbindung.room)
      verbindung = null
      return
    }

    haengeEreignisseAn(verbindung.room, eigeneGeneration)
    await erlaubeWiedergabe(verbindung.room)

    try {
      await setzeMikrofon(verbindung.room, true)
      if (mode === 'video') await setzeKamera(verbindung.room, true)
    } catch (fehler) {
      // Verbunden, aber ohne eigenes Mikrofon: hörbar bleibt der Anruf trotzdem.
      // Der Benutzer soll wissen, warum ihn niemand hört.
      meldeFehler(fehler, 'geraet')
      set({ isMuted: true })
    }
    set({ state: 'active', isCameraOff: mode !== 'video' })
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
    generation += 1
    const room = verbindung?.room
    verbindung = null
    raumSchluessel = null
    sprechend = new Set<string>()
    bekannte.clear()
    if (room) void trenne(room)
  }

  return {
    state: 'idle',
    kind: 'direkt',
    mode: 'audio',
    partner: null,
    group: null,
    raum: null,
    participants: [],
    screenShares: [],
    focusedShareIdentity: null,
    isMuted: false,
    isDeafened: false,
    isCameraOff: true,
    isScreenSharing: false,
    screenShareOptions: FREIGABE_STANDARD,
    selectedAudioInput: 'default',
    selectedVideoInput: 'default',
    selectedAudioOutput: 'default',
    callDurationSeconds: 0,
    reconnecting: false,
    errorMessage: null,

    initiateCall: async (partner, mode) => {
      raeumeAuf()
      bekannte.set(partner.userId, { username: partner.username, avatarUrl: partner.avatarUrl })
      if (identitaet) {
        bekannte.set(identitaet.userId, bekannte.get(identitaet.userId) ?? { username: 'Ich' })
      }
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
      raeumeAuf()
      bekannte.set(partner.userId, { username: partner.username, avatarUrl: partner.avatarUrl })
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
      const raum = get().raum
      if (raum) void lehneAnrufAb(raum).catch(() => {})
      get().endCall()
    },

    joinGroupCall: async (group, raum, mitgliederIds) => {
      raeumeAuf()
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
      if (raum && state === 'outgoing' && kind === 'direkt') {
        // Auflegen, während es beim Gegenüber noch klingelt: die Einladung muss
        // serverseitig sterben, sonst klingelt es dort weiter.
        void brichAnrufAb(raum).catch(() => {})
      }
      if (kind === 'gruppe' && raum && group && group.canModerate) {
        void beendeGruppenanruf(group.id, raum).catch(() => {})
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
      const naechster = !get().isMuted
      const room = verbindung?.room
      if (room) void setzeMikrofon(room, !naechster).catch(() => {})
      // Aus der Taubheit heraus das Mikrofon einzuschalten hebt sie auf: alles
      // andere wäre ein Zustand, in dem man spricht und nichts hört.
      set(naechster ? { isMuted: true } : { isMuted: false, isDeafened: false })
      if (!naechster && room) setzeTaub(room, false)
    },

    toggleDeafen: () => {
      const naechster = !get().isDeafened
      const room = verbindung?.room
      if (room) {
        setzeTaub(room, naechster)
        void setzeMikrofon(room, !naechster).catch(() => {})
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
      void setzeKamera(room, !naechster)
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
        await starteBildschirmfreigabe(room, optionen)
        set({ isScreenSharing: true })
        spiegele()
      } catch (fehler) {
        const name = (fehler as { name?: string } | null)?.name ?? ''
        // Abbrechen im Auswahldialog des Browsers ist kein Fehler.
        if (name === 'NotAllowedError' || name === 'AbortError') return
        toast.error('Die Bildschirmfreigabe konnte nicht gestartet werden.')
      }
    },

    stopScreenShare: async () => {
      const room = verbindung?.room
      if (!room) return
      await beendeBildschirmfreigabe(room).catch(() => {})
      set({ isScreenSharing: false })
      spiegele()
    },

    setFocusedShare: (identity) => set({ focusedShareIdentity: identity }),

    setParticipantVolume: (identity, wert) => {
      const room = verbindung?.room
      if (room) setzeLautstaerke(room, identity, wert)
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
        if (audioIn) void wechsleGeraet(room, 'audioinput', audioIn).catch(() => {})
        if (videoIn) void wechsleGeraet(room, 'videoinput', videoIn).catch(() => {})
        if (audioOut) void wechsleGeraet(room, 'audiooutput', audioOut).catch(() => {})
      }
      set((s) => ({
        selectedAudioInput: audioIn ?? s.selectedAudioInput,
        selectedVideoInput: videoIn ?? s.selectedVideoInput,
        selectedAudioOutput: audioOut ?? s.selectedAudioOutput,
      }))
    },

    incrementDuration: () => set((s) => ({ callDurationSeconds: s.callDurationSeconds + 1 })),

    acceptRoomKey: async (raum, ciphertext) => {
      if (!identitaet || get().raum !== raum) return
      try {
        const schluessel = await entpacke(ciphertext, identitaet.decryptionKeys)
        raumSchluessel = schluessel
        if (verbindung) await setzeRaumSchluessel(verbindung, alsArrayBuffer(schluessel))
        spiegele()
      } catch {
        // Ein Umschlag, den wir nicht öffnen können, ist nicht für uns. Der
        // Anruf läuft weiter; ohne passenden Schlüssel bleibt er still, und
        // genau das zeigt die Oberfläche über `isPending` an.
      }
    },

    setMode: (mode) => set({ mode }),
  }
})
