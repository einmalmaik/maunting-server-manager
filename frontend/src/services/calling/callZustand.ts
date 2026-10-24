/**
 * Typen, Startwerte und Zeitgeber des Anrufzustands.
 *
 * Alles hier gehoert keinem Gespraech: die Fristen und Merker gelten fuer den
 * einen Anruf, den es gleichzeitig gibt. Der Store ruft sie auf, haelt sie aber
 * nicht selbst.
 */

import { sendeAnrufHeartbeat } from '@/api/calls'
import type { ActiveCallInfo, PendingGroupCallInfo } from '@/api/calls'
import { getDeviceId } from '@/lib/deviceIdentity'
import { FREIGABE_STANDARD, type FreigabeOptionen } from '@/services/livekitGrundlagen'
import { getAudioSettings } from '@/lib/audioSettings'
import type { Track } from 'livekit-client'


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

export interface AnrufIdentitaet {
  userId: number
  publicKeyJwk: string
  decryptionKeys: string[]
}

let heartbeatTimer: number | null = null

export function starteHeartbeat(raum: string) {
  stoppeHeartbeat()
  heartbeatTimer = window.setInterval(() => {
    void sendeAnrufHeartbeat(getDeviceId(), raum).catch(() => {})
  }, 25000)
}

export function stoppeHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

let partnerDisconnectTimer: number | null = null
let partnerTransferring = false
let partnerAngenommen = false

export function brecheDisconnectTimerAb() {
  if (partnerDisconnectTimer !== null) {
    window.clearTimeout(partnerDisconnectTimer)
    partnerDisconnectTimer = null
  }
}

/** Automatisches Auflegen/Verwerfen nach 1 Minute ohne Annahme. */
const CALL_TIMEOUT_MS = 60_000
let incomingCallTimer: number | null = null
let outgoingCallTimer: number | null = null

export function brecheCallTimersAb() {
  if (incomingCallTimer !== null) {
    window.clearTimeout(incomingCallTimer)
    incomingCallTimer = null
  }
  if (outgoingCallTimer !== null) {
    window.clearTimeout(outgoingCallTimer)
    outgoingCallTimer = null
  }
}

/** Startet die Frist, nach der ein unbeantworteter Anruf von selbst endet. */
export function setzeEingehendenTimer(fn: () => void): void {
  brecheCallTimersAb()
  incomingCallTimer = window.setTimeout(() => {
    incomingCallTimer = null
    fn()
  }, CALL_TIMEOUT_MS)
}

/** Dasselbe fuer den ausgehenden Anruf. */
export function setzeAusgehendenTimer(fn: () => void): void {
  brecheCallTimersAb()
  outgoingCallTimer = window.setTimeout(() => {
    outgoingCallTimer = null
    fn()
  }, CALL_TIMEOUT_MS)
}

/** Gnadenfrist nach einem Geraetewechsel: so lange darf das neue Geraet uebernehmen. */
export function starteDisconnectGnadenfrist(fn: () => void, ms = 8000): void {
  brecheDisconnectTimerAb()
  partnerDisconnectTimer = window.setTimeout(() => {
    partnerDisconnectTimer = null
    partnerTransferring = false
    fn()
  }, ms)
}

export function istPartnerAngenommen(): boolean {
  return partnerAngenommen
}

export function setzePartnerAngenommen(wert: boolean): void {
  partnerAngenommen = wert
}

export function setzePartnerTransferiert(wert: boolean): void {
  partnerTransferring = wert
}

export function istPartnerTransferiert(): boolean {
  return partnerTransferring
}

/** Die Aktionen, die der Store selbst beisteuert. */
type AnrufAktionen =
  | 'initiateCall'
  | 'receiveCall'
  | 'acceptCall'
  | 'rejectCall'
  | 'joinGroupCall'
  | 'endCall'
  | 'toggleMute'
  | 'toggleDeafen'
  | 'toggleCamera'
  | 'startScreenShare'
  | 'stopScreenShare'
  | 'setFocusedShare'
  | 'setParticipantVolume'
  | 'inviteToCall'
  | 'setDevices'
  | 'incrementDuration'
  | 'erlaubeTon'
  | 'verwirfHinweis'
  | 'acceptRoomKey'
  | 'setMode'
  | 'checkActiveCall'
  | 'transferCallToThisDevice'
  | 'terminateCrossDeviceCall'
  | 'handleCrossDeviceEvent'
  | 'handleCallSyncEvent'

/** Die Startwerte des Stores. */
export function anfangszustand(): Omit<UseCallState, AnrufAktionen> {
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
  }
}
