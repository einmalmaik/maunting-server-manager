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
  beendeAktivenAnrufRemote,
  brichAnrufAb,
  holeAktivenAnruf,
  holeAusstehendeAnrufe,
  holeInAnruf,
  holeZugang,
  ladeZuAnrufEin,
  lehneAnrufAb,
  sendeAnrufHeartbeat,
  verlasseAnruf,
  type ActiveCallInfo,
  type PendingGroupCallInfo,
} from '@/api/calls'
import { useAuthStore } from '@/stores/authStore'
import { getDeviceId, getDeviceType } from '@/lib/deviceIdentity'
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
import { toneAbgang, toneAufgelegt, toneBeitritt, toneUebergabe } from '@/components/calling/anrufToene'
import { getAudioSettings, saveAudioSettings } from '@/lib/audioSettings'
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
  /** Darf anderen im Anruf das Mikrofon abschalten. */
  canMute?: boolean
  /** Darf andere aus dem Anruf entfernen. */
  canKick?: boolean
}

export type HinweisArt = 'beitritt' | 'abgang' | 'info'

export interface CallHinweis {
  id: number
  art: HinweisArt
  text: string
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
  /** Der Browser lässt noch keinen Ton zu — das Overlay bietet einen Knopf an. */
  audioBlockiert: boolean
  /** Von der Moderation stummgeschaltet; selbst nicht aufhebbar. */
  serverStumm: boolean
  /** Flüchtige Meldungen im Anruffenster. Nichts davon geht in den Verlauf. */
  hinweise: CallHinweis[]
  /** Ein auf einem anderen Gerät laufender Anruf desselben Kontos (Discord-Style). */
  crossDeviceCall: ActiveCallInfo | null
  /** Aktive Gruppenanrufe, denen beigetreten werden kann. */
  activeGroupCalls: PendingGroupCallInfo[]

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
  /** Nach einem Klick: holt die vom Browser verweigerte Tonwiedergabe nach. */
  erlaubeTon: () => Promise<void>
  verwirfHinweis: (id: number) => void
  /** Ein eingehender `call_key`-Umschlag aus dem Ereignisstrom. */
  acceptRoomKey: (raum: string, ciphertext: string, fromUserId?: number) => Promise<void>
  setMode: (mode: CallMode) => void
  /** Prüft geräteübergreifend, ob dieses Konto auf einer anderen Plattform telefoniert. */
  checkActiveCall: () => Promise<void>
  /** Übergibt den laufenden Anruf nahtlos auf dieses Gerät (Handoff). */
  transferCallToThisDevice: () => Promise<void>
  /** Beendet den auf dem anderen Gerät laufenden Anruf aus der Ferne. */
  terminateCrossDeviceCall: () => Promise<void>
  /** Verarbeitet geräteübergreifende Sync-Ereignisse (Handoff, Übernahme, Beenden). */
  handleCrossDeviceEvent: (detail: unknown) => void
  /** Verarbeitet globale Anruf-Ereignisse (Einladung, Abbruch, Gruppenstart, Schlüssel). */
  handleCallSyncEvent: (detail: unknown) => void
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
let heartbeatTimer: number | null = null

function starteHeartbeat(raum: string) {
  stoppeHeartbeat()
  heartbeatTimer = window.setInterval(() => {
    void sendeAnrufHeartbeat(getDeviceId(), raum).catch(() => {})
  }, 25000)
}

function stoppeHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

let partnerDisconnectTimer: number | null = null
let partnerTransferring = false
let partnerAngenommen = false

function brecheDisconnectTimerAb() {
  if (partnerDisconnectTimer !== null) {
    window.clearTimeout(partnerDisconnectTimer)
    partnerDisconnectTimer = null
  }
}

/** Automatisches Auflegen/Verwerfen nach 1 Minute ohne Annahme. */
const CALL_TIMEOUT_MS = 60_000
let incomingCallTimer: number | null = null
let outgoingCallTimer: number | null = null

function brecheCallTimersAb() {
  if (incomingCallTimer !== null) {
    window.clearTimeout(incomingCallTimer)
    incomingCallTimer = null
  }
  if (outgoingCallTimer !== null) {
    window.clearTimeout(outgoingCallTimer)
    outgoingCallTimer = null
  }
}

export function aktiverRaum(): Room | null {
  return verbindung?.room ?? null
}

// ── Hilfen zum Übersetzen von LiveKit-Zustand in Store-Zustand ──────────────

function anzeigename(teilnehmer: Participant): string {
  return teilnehmer.name || teilnehmer.identity
}

/** Der Name, den eine Meldung nennen soll — aus den Stammdaten, sonst aus LiveKit. */
function nameVon(teilnehmer: Participant, bekannte: Bekannte): string {
  const userId = benutzerIdAusIdentity(teilnehmer.identity)
  const stammdaten = userId === null ? undefined : bekannte.get(userId)
  return stammdaten?.username || anzeigename(teilnehmer)
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

/** Wie lange eine Beitritts- oder Abgangsmeldung im Anruffenster stehen bleibt. */
const HINWEIS_DAUER_MS = 3000

/**
 * Darf dieser Teilnehmer diese Quelle senden?
 *
 * LiveKit führt die erlaubten Quellen je Teilnehmer. Nimmt ein Moderator das
 * Mikrofon aus der Liste, verweigert der Server das Senden — der Betroffene
 * kann sich dann nicht selbst wieder freischalten. Eine leere oder fehlende
 * Liste heißt „alles erlaubt", nicht „nichts erlaubt".
 */
function darfSenden(teilnehmer: Participant, quelle: Track.Source): boolean {
  const erlaubt = teilnehmer.permissions?.canPublishSources
  if (!erlaubt || erlaubt.length === 0) return true
  return erlaubt.includes(quellenNummer(quelle))
}

/** Die Nummern aus LiveKits `TrackSource`-Aufzählung. */
function quellenNummer(quelle: Track.Source): number {
  switch (quelle) {
    case Track.Source.Camera:
      return 1
    case Track.Source.Microphone:
      return 2
    case Track.Source.ScreenShare:
      return 3
    case Track.Source.ScreenShareAudio:
      return 4
    default:
      return 0
  }
}

export function sanitizeAvatarUrl(url: unknown): string | null {
  if (typeof url !== 'string') return null
  const trimmed = url.trim()
  if (!trimmed) return null
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed
  }
  // Safe relative paths (e.g. /media/avatar.png, /avatar/dana.png), not protocol-relative //
  if (trimmed.startsWith('/') && !trimmed.startsWith('//')) {
    return trimmed
  }
  return null
}

type Bekannte = Map<number, { username: string; avatarUrl?: string | null }>

function sammleTeilnehmer(room: Room, bekannte: Bekannte, sprechend: Set<string>): CallParticipant[] {
  const alle: Participant[] = [room.localParticipant, ...room.remoteParticipants.values()]
  const currentUser = useAuthStore.getState().user
  return alle.map((teilnehmer) => {
    let meta: { user_id?: number; username?: string; avatar_url?: string | null } | null = null
    if (teilnehmer.metadata) {
      try {
        meta = JSON.parse(teilnehmer.metadata)
      } catch {}
    }
    // Die Kennung kommt allein aus der vom Server signierten Identität.
    // Metadaten, die einen anderen Benutzer behaupten, zählen nicht: sonst
    // trüge eine gefälschte Kachel Name und Bild eines Mitglieds, und ein
    // Moderator schaltete den Falschen stumm.
    const rawUserId = benutzerIdAusIdentity(teilnehmer.identity)
    if (meta && meta.user_id !== undefined && meta.user_id !== rawUserId) meta = null
    const userId = rawUserId ?? 0
    const stammdaten = rawUserId ? bekannte.get(rawUserId) : undefined
    const istSelbst = teilnehmer.identity === room.localParticipant.identity

    const resolvedUsername = istSelbst
      ? (currentUser?.username || meta?.username || stammdaten?.username || anzeigename(teilnehmer))
      : (meta?.username || stammdaten?.username || anzeigename(teilnehmer))

    const rawAvatar = istSelbst
      ? (currentUser?.avatar_url ?? meta?.avatar_url ?? stammdaten?.avatarUrl ?? null)
      : (meta?.avatar_url ?? stammdaten?.avatarUrl ?? null)
    const resolvedAvatarUrl = sanitizeAvatarUrl(rawAvatar)

    if (rawUserId) {
      bekannte.set(rawUserId, {
        username: resolvedUsername,
        avatarUrl: resolvedAvatarUrl,
      })
    }

    return {
      userId,
      identity: teilnehmer.identity,
      username: resolvedUsername,
      avatarUrl: resolvedAvatarUrl,
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
  let hinweisZaehler = 0

  const spiegele = () => {
    const room = verbindung?.room
    if (!room) return
    const serverStumm = !darfSenden(room.localParticipant, Track.Source.Microphone)
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

    room.on(RoomEvent.Connected, wennAktuell(() => {
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
      partnerTransferring = false
      partnerAngenommen = true
      brecheDisconnectTimerAb()
      brecheCallTimersAb()
      set({ state: 'active' })
      spiegele()
      toneBeitritt()
      meldeHinweis('beitritt', `${nameVon(teilnehmer, bekannte)} ist beigetreten`)
      // Nachzügler brauchen den Raumschlüssel. Genau ein Anwesender schickt ihn.
      void schickeSchluesselNach(teilnehmer)
    })
    room.on(RoomEvent.ParticipantDisconnected, (teilnehmer) => {
      if (eigeneGeneration !== generation) return
      spiegele()
      // Zweiergespräch: geht der andere, ist das Gespräch vorbei.
      // Außer wenn ein Gerätewechsel (Handoff) signalisiert wurde: dann geben wir
      // eine Gnadenfrist (8s), damit das neue Gerät die Verbindung übernehmen kann.
      if (get().kind === 'direkt' && (verbindung?.room.remoteParticipants.size ?? 0) === 0) {
        if (partnerTransferring) {
          meldeHinweis('info', 'Verbindung wird wiederhergestellt...')
          brecheDisconnectTimerAb()
          partnerDisconnectTimer = window.setTimeout(() => {
            partnerDisconnectTimer = null
            partnerTransferring = false
            if (eigeneGeneration !== generation) return
            if (get().kind === 'direkt' && (verbindung?.room.remoteParticipants.size ?? 0) === 0) {
              get().endCall()
            }
          }, 8000)
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
    room.on(RoomEvent.ParticipantPermissionsChanged, wennAktuell(spiegele))
    room.on(RoomEvent.ParticipantMetadataChanged, wennAktuell(spiegele))

    // Der Browser hat die Wiedergabe freigegeben oder verweigert.
    room.on(RoomEvent.AudioPlaybackStatusChanged, wennAktuell(() => {
      set({ audioBlockiert: !room.canPlaybackAudio })
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
    set({ audioBlockiert: !(await erlaubeWiedergabe(verbindung.room)) })

    try {
      await setzeMikrofon(verbindung.room, true)
      if (mode === 'video') await setzeKamera(verbindung.room, true)
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
    partnerTransferring = false
    partnerAngenommen = false
    brecheDisconnectTimerAb()
    generation += 1
    const room = verbindung?.room
    verbindung = null
    raumSchluessel = null
    sprechend = new Set<string>()
    bekannte.clear()
    set({ hinweise: [], audioBlockiert: false, serverStumm: false })
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
    // Dasselbe Mikrofon, das Profil → Audio gewählt hat. Ohne das nähme der
    // Anruf das Standardgerät, während der Mikrofontest ein anderes prüft.
    selectedAudioInput: getAudioSettings().preferredMicId ?? 'default',
    selectedVideoInput: 'default',
    selectedAudioOutput: getAudioSettings().preferredSpeakerId ?? 'default',
    callDurationSeconds: 0,
    reconnecting: false,
    errorMessage: null,
    audioBlockiert: false,
    serverStumm: false,
    hinweise: [],
    crossDeviceCall: null,
    activeGroupCalls: [],

    initiateCall: async (partner, mode) => {
      raeumeAuf()
      bekannte.set(partner.userId, { username: partner.username, avatarUrl: partner.avatarUrl })
      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        bekannte.set(currentUser.id, { username: currentUser.username, avatarUrl: currentUser.avatar_url })
      } else if (identitaet) {
        bekannte.set(identitaet.userId, bekannte.get(identitaet.userId) ?? { username: 'Ich' })
      }
      outgoingCallTimer = window.setTimeout(() => {
        outgoingCallTimer = null
        const hatFremde = (verbindung?.room.remoteParticipants.size ?? 0) > 0 ||
          get().participants.some((p) => !p.isSelf)
        if (get().state === 'outgoing' || !hatFremde) {
          get().endCall()
          toast.info(i18n.t('calls.nobodyAnswered'))
        }
      }, CALL_TIMEOUT_MS)
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
      incomingCallTimer = window.setTimeout(() => {
        incomingCallTimer = null
        if (get().state === 'incoming') {
          get().rejectCall()
          toast.info(i18n.t('calls.callMissed'))
        }
      }, CALL_TIMEOUT_MS)
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
      partnerAngenommen = true
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
        if (state === 'outgoing' || !partnerAngenommen) {
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
        toast.error(i18n.t('calls.screenshareFailed'))
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
      set({ audioBlockiert: !(await erlaubeWiedergabe(room)) })
    },

    verwirfHinweis: (id) => set((s) => ({ hinweise: s.hinweise.filter((h) => h.id !== id) })),

    acceptRoomKey: async (raum, ciphertext, fromUserId) => {
      if (!identitaet || get().raum !== raum) return

      // H-4: Schutz vor unberechtigter Schlüsselübernahme.
      // Wenn bereits ein Raumschlüssel aktiv ist, darf nur der nach der
      // lokalen Regel zuständige Schlüsselhalter einen neuen Schlüssel schicken.
      // Fehlt fromUserId oder ist der Absender nicht zuständig, wird der Wechsel abgewiesen.
      if (raumSchluessel) {
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
            zustaendig = Math.min(identitaet.userId, partnerId) === fromUserId
          }
        } else {
          // Im Gruppenanruf gilt die istSchluesselhalter-Regel über alle anwesenden Teilnehmer.
          const room = verbindung?.room
          const anwesende = room
            ? [room.localParticipant, ...room.remoteParticipants.values()]
                .map((t) => benutzerIdAusIdentity(t.identity))
                .filter((id): id is number => id !== null)
            : []
          const liste = anwesende.length > 0 ? anwesende : [identitaet.userId, fromUserId]
          zustaendig = istSchluesselhalter(fromUserId, liste, identitaet.userId)
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

    transferCallToThisDevice: async () => {
      const { crossDeviceCall } = get()
      if (!crossDeviceCall) return
      const target = crossDeviceCall
      raeumeAuf()
      partnerAngenommen = true
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
        if (identitaet) {
          bekannte.set(identitaet.userId, bekannte.get(identitaet.userId) ?? { username: 'Ich' })
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
        if (!raumSchluessel) raumSchluessel = erzeugeRaumSchluessel()
        if (identitaet) {
          void verteileAn(target.raum, raumSchluessel, target.partner.user_id, identitaet.publicKeyJwk)
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
    },

    terminateCrossDeviceCall: async () => {
      set({ crossDeviceCall: null })
      try {
        await beendeAktivenAnrufRemote()
      } catch {
        // Nicht fatal
      }
    },

    handleCrossDeviceEvent: (detail: unknown) => {
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
          partnerTransferring = true
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
    },

    handleCallSyncEvent: (detail: unknown) => {
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
          !partnerAngenommen &&
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
