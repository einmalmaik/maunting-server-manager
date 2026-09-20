/**
 * Die einzige Stelle im Frontend, die `livekit-client` kennt.
 *
 * Alles darüber (Store, Overlay, Modals) spricht mit dieser Datei und nie mit
 * dem SDK. Das hält die Abhängigkeit einschätzbar: wer wissen will, was MSM
 * von LiveKit benutzt, liest diese Datei und ist fertig.
 *
 * **Medien sind Ende-zu-Ende verschlüsselt.** Ein SFU sieht sonst Klartext.
 * Hier läuft jeder Frame durch einen Worker, der ihn mit einem Raumschlüssel
 * ver- und entschlüsselt, den der Server nie bekommt (siehe `raumSchluessel.ts`).
 * Kann der Browser das nicht, scheitert der Anruf — er wird nicht still
 * unverschlüsselt geführt.
 */

import {
  ConnectionState,
  ExternalE2EEKeyProvider,
  Room,
  RoomEvent,
  Track,
  isE2EESupported,
  type AudioCaptureOptions,
  type RemoteParticipant,
  type RoomOptions,
  type ScreenShareCaptureOptions,
  type TrackPublishOptions,
  type VideoCaptureOptions,
} from 'livekit-client'
import E2eeWorker from 'livekit-client/e2ee-worker?worker'
import { ausgabeGeraetId } from '@/components/ai/voice/audioGeraete'
import { getAudioTrackConstraints } from '@/lib/audioSettings'
import { getVideoCaptureAufloesung, getVideoSendeGrenzen } from '@/lib/videoSettings'

export { ConnectionState, RoomEvent, Track }
export type { RemoteParticipant }

/** Auflösungen, die die Bildschirmfreigabe anbietet. */
export type FreigabeAufloesung = '720p' | '1080p' | '1440p' | '2160p' | 'quelle'
export type FreigabeBildrate = 30 | 60

export interface FreigabeOptionen {
  aufloesung: FreigabeAufloesung
  bildrate: FreigabeBildrate
  /** System- bzw. Spielton mitübertragen. Nur Chromium-Browser können das. */
  systemton: boolean
}

export const FREIGABE_STANDARD: FreigabeOptionen = {
  aufloesung: '1080p',
  bildrate: 60,
  systemton: true,
}

/**
 * Bitraten je Auflösung. Bewusst großzügig: eine Bildschirmfreigabe zeigt oft
 * Text oder ein Spiel, und beides verliert bei zu knapper Rate genau das, wofür
 * man sie teilt. Bei 60 FPS wird verdoppelt.
 *
 * 2160p liegt bei 20 Mbit/s und damit bei 60 Bildern auf 40. Das ist viel, und
 * es ist der Punkt: eine 4K-Freigabe mit der Rate einer 1440p-Freigabe zeigt
 * vier Mal so viele Pixel, die alle etwas unschärfer sind — dann kann man auch
 * gleich 1440p teilen. Wer die Stufe wählt, will die Schärfe.
 */
const BITRATE_JE_AUFLOESUNG: Record<Exclude<FreigabeAufloesung, 'quelle'>, number> = {
  '720p': 2_500_000,
  '1080p': 5_000_000,
  '1440p': 9_000_000,
  '2160p': 20_000_000,
}

const MASSE_JE_AUFLOESUNG: Record<Exclude<FreigabeAufloesung, 'quelle'>, { width: number; height: number }> = {
  '720p': { width: 1280, height: 720 },
  '1080p': { width: 1920, height: 1080 },
  '1440p': { width: 2560, height: 1440 },
  '2160p': { width: 3840, height: 2160 },
}

/** Kann dieser Browser verschlüsselte Anrufe? */
export function e2eeMoeglich(): boolean {
  try {
    return isE2EESupported()
  } catch {
    return false
  }
}

/**
 * Kann dieser Browser überhaupt eine Bildschirmfreigabe anbieten?
 *
 * In der Desktop-App (WebView2/WKWebView) fehlt `getDisplayMedia` je nach
 * Plattform. Der Knopf soll dann sagen, warum er nichts tut, statt stumm zu
 * bleiben.
 */
export function bildschirmfreigabeMoeglich(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.mediaDevices?.getDisplayMedia === 'function'
}

/**
 * Kann dieser Browser Systemton mit übertragen?
 *
 * Chromium kann es (Tab- und Systemton), Firefox und Safari nicht. Eine
 * Checkbox, die nichts bewirkt, ist schlimmer als keine.
 */
export function systemtonMoeglich(): boolean {
  if (!bildschirmfreigabeMoeglich()) return false
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
  return /Chrome|Chromium|Edg/.test(ua) && !/Firefox/.test(ua)
}

export class E2eeNichtUnterstuetzt extends Error {
  constructor() {
    super(
      'Dieser Browser kann verschlüsselte Anrufe nicht. MSM überträgt Gespräche nur ' +
        'verschlüsselt, deshalb ist der Anruf hier nicht möglich. Aktuelles Chrome, ' +
        'Edge, Firefox oder Safari ab 15.4 funktionieren.',
    )
    this.name = 'E2eeNichtUnterstuetzt'
  }
}

export interface RaumVerbindung {
  room: Room
  keyProvider: ExternalE2EEKeyProvider
}

/**
 * Die Kameraeinstellungen des Anrufs — dieselben, die auch die Videonotiz
 * benutzt, soweit sie auf sie passen.
 *
 * Hier stand fest `VideoPresets.h720`, und das ist in livekit-client
 * `1280×720 bei 30 Bildern und 1,7 Mbit/s` — unabhängig davon, was Kamera und
 * Leitung hergeben. Jetzt kommt die Stufe aus dem Profil, und LiveKit regelt
 * von dort aus nach unten: `adaptiveStream` je Empfänger, Simulcast-Ebenen je
 * Bandbreite.
 */
export function kameraAufnahme(): VideoCaptureOptions {
  const { width, height, frameRate } = getVideoCaptureAufloesung()
  return { resolution: { width, height, frameRate } }
}

/**
 * Wie die Kamera gesendet wird: Codec, Simulcast und die Obergrenzen aus dem Profil.
 *
 * Eine Funktion, zwei Aufrufer. `raumOptionen` nimmt sie als Voreinstellung des
 * Raums, `setzeKamera` gibt sie beim Einschalten noch einmal mit — sonst bliebe
 * die Bitrate auf dem Stand, den die Wahl beim Verbinden hatte, und wer im
 * Gespräch von 720p auf 2160p stellt, bekäme ein 4K-Bild mit der Bitrate für
 * 720p. Also viel Auflösung und wenig davon zu sehen.
 */
function kameraSenden(): TrackPublishOptions {
  const grenzen = getVideoSendeGrenzen()
  return {
    // VP8 statt VP9/AV1: die einzigen Codecs, für die LiveKit-E2EE in allen
    // unterstützten Browsern geprüft ist. Ein hübscherer Codec, der bei
    // einem Gegenüber schwarz bleibt, ist kein Gewinn.
    videoCodec: 'vp8',
    simulcast: true,
    videoEncoding: {
      maxBitrate: grenzen.maxBitrate,
      maxFramerate: grenzen.maxFramerate,
    },
    // 'balanced' statt 'maintain-resolution' wie bei der Bildschirmfreigabe:
    // dort ist ein scharfer Text alles, hier ein flüssiges Gesicht. Wird die
    // Leitung eng, darf das Bild kleiner werden.
    degradationPreference: 'balanced',
    red: true,
    dtx: true,
  }
}

function raumOptionen(keyProvider: ExternalE2EEKeyProvider, worker: Worker): RoomOptions {
  return {
    adaptiveStream: true,
    // Dynacast schaltet Ebenen ab, die niemand ansieht. In einem Gruppenanruf,
    // in dem alle auf eine Bildschirmfreigabe schauen, spart das die
    // Kamerabilder, die gerade niemand sieht.
    dynacast: true,
    e2ee: { keyProvider, worker },
    videoCaptureDefaults: kameraAufnahme(),
    publishDefaults: kameraSenden(),
    stopLocalTrackOnUnpublish: true,
  }
}

/**
 * Verbindet mit einem Raum. Der Schlüssel wird gesetzt, **bevor** verbunden
 * wird, damit kein Frame unverschlüsselt hinausgeht.
 */
export async function verbinde(
  url: string,
  token: string,
  raumSchluessel: ArrayBuffer,
): Promise<RaumVerbindung> {
  if (!e2eeMoeglich()) throw new E2eeNichtUnterstuetzt()

  const keyProvider = new ExternalE2EEKeyProvider()
  const worker = new E2eeWorker()
  const room = new Room(raumOptionen(keyProvider, worker))

  await keyProvider.setKey(raumSchluessel)
  await room.setE2EEEnabled(true)
  await room.connect(url, token)
  return { room, keyProvider }
}

/** Tauscht den Raumschlüssel (z. B. wenn ein Nachzügler seinen eigenen mitbringt). */
export async function setzeRaumSchluessel(
  verbindung: RaumVerbindung,
  schluessel: ArrayBuffer,
): Promise<void> {
  await verbindung.keyProvider.setKey(schluessel)
}

export async function trenne(room: Room): Promise<void> {
  try {
    await room.disconnect()
  } catch {
    /* Ein Trennen, das scheitert, ist immer noch ein Trennen. */
  }
}

// ── Mikrofon, Kamera, Wiedergabe ────────────────────────────────────────────

/**
 * Die Aufnahmeeinstellungen des Anrufs — dieselben, die Sprachnachricht und
 * Wake-Word benutzen. `getAudioTrackConstraints` ist die eine Stelle, an der
 * Gerätewahl und Chromiums Filterkette zusammenkommen; ein Anruf, der sie
 * umgeht, nähme ein anderes Mikrofon als der Mikrofontest im Profil.
 *
 * LiveKit wertet die Einstellungen nur beim ersten Veröffentlichen aus.
 * Danach schaltet `setMicrophoneEnabled` dieselbe Spur stumm und wieder frei —
 * gewollt, denn ein Gerätewechsel mitten im Gespräch läuft über
 * `wechsleGeraet`.
 */
function mikrofonAufnahme(): AudioCaptureOptions {
  const c = getAudioTrackConstraints()
  return {
    deviceId: c.deviceId,
    echoCancellation: c.echoCancellation as boolean,
    noiseSuppression: c.noiseSuppression as boolean,
    autoGainControl: c.autoGainControl as boolean,
    channelCount: c.channelCount as number,
  }
}

export async function setzeMikrofon(room: Room, an: boolean): Promise<void> {
  await room.localParticipant.setMicrophoneEnabled(an, an ? mikrofonAufnahme() : undefined)
}

/**
 * Legt die Wiedergabe auf den im Profil gewählten Lautsprecher.
 *
 * Muss laufen, **nachdem** Elemente angehängt sind: `switchActiveDevice` setzt
 * `setSinkId` auf genau diesen Elementen. Ohne Wahl bleibt es beim Standard des
 * Systems, und ein Browser ohne `setSinkId` (Firefox ohne Flag) tut nichts —
 * beides ist kein Fehler, nur kein Wechsel.
 */
export async function setzeLautsprecher(room: Room): Promise<void> {
  try {
    const geraet = await ausgabeGeraetId()
    if (!geraet) return
    await room.switchActiveDevice('audiooutput', geraet)
  } catch {
    /* Kein Lautsprecherwechsel ist besser als ein abgebrochener Anruf. */
  }
}

export async function setzeKamera(room: Room, an: boolean): Promise<void> {
  // Aufnahme- *und* Sendeoptionen beim Einschalten mitgeben. Die Voreinstellung
  // des Raums greift nur beim ersten Veröffentlichen; wer die Kamera im
  // Gespräch aus- und wieder einschaltet, bekäme sonst das, was livekit-client
  // für richtig hält. Beide Male frisch gelesen, damit eine Änderung im Profil
  // ohne neuen Anruf ankommt.
  await room.localParticipant.setCameraEnabled(
    an,
    an ? kameraAufnahme() : undefined,
    an ? kameraSenden() : undefined
  )
}

/**
 * Taub schalten: alles Eingehende stumm **und** das eigene Mikrofon aus.
 *
 * Beides zusammen, weil „ich höre euch nicht" ohne „ihr hört mich nicht" eine
 * Einbahnstraße wäre, die die Gegenseite nicht erkennt. Discord macht es
 * genauso, und die Erwartung ist inzwischen genau die.
 */
export function setzeTaub(room: Room, taub: boolean): void {
  room.remoteParticipants.forEach((teilnehmer) => {
    teilnehmer.setVolume(taub ? 0 : 1, Track.Source.Microphone)
    teilnehmer.setVolume(taub ? 0 : 1, Track.Source.ScreenShareAudio)
  })
}

/** Lautstärke einer einzelnen Gegenstelle (0..1). */
export function setzeLautstaerke(room: Room, identity: string, wert: number): void {
  const teilnehmer = room.remoteParticipants.get(identity)
  if (!teilnehmer) return
  teilnehmer.setVolume(wert, Track.Source.Microphone)
  teilnehmer.setVolume(wert, Track.Source.ScreenShareAudio)
}

export async function wechsleGeraet(
  room: Room,
  art: MediaDeviceKind,
  geraeteId: string,
): Promise<void> {
  if (!geraeteId || geraeteId === 'default') return
  await room.switchActiveDevice(art, geraeteId)
}

// ── Bildschirmfreigabe ──────────────────────────────────────────────────────

export function freigabeAufnahmeOptionen(optionen: FreigabeOptionen): ScreenShareCaptureOptions {
  const aufnahme: ScreenShareCaptureOptions = {
    audio: optionen.systemton && systemtonMoeglich(),
    // 'motion' statt 'detail': bei 60 FPS soll der Encoder Bildrate halten und
    // notfalls Schärfe opfern. Wer ein Spiel teilt, will flüssig; wer Text
    // teilt, nimmt 30 FPS und bekommt die Schärfe über die Bitrate.
    contentHint: optionen.bildrate >= 60 ? 'motion' : 'detail',
    systemAudio: optionen.systemton ? 'include' : 'exclude',
    selfBrowserSurface: 'exclude',
    surfaceSwitching: 'include',
  }
  if (optionen.aufloesung !== 'quelle') {
    const masse = MASSE_JE_AUFLOESUNG[optionen.aufloesung]
    aufnahme.resolution = { ...masse, frameRate: optionen.bildrate }
  }
  return aufnahme
}

export function freigabeSendeOptionen(optionen: FreigabeOptionen): TrackPublishOptions {
  // „Quelle" heißt: so groß wie der Bildschirm, und der ist heute oft 4K. Hier
  // stand `1440p` als Basis, und damit ging eine 4K-Freigabe mit der Rate einer
  // 2K-Freigabe hinaus — vier Mal so viele Pixel, alle unschärfer. Die Rate ist
  // eine Obergrenze: ein kleinerer Bildschirm schöpft sie nicht aus.
  const basis = optionen.aufloesung === 'quelle' ? '2160p' : optionen.aufloesung
  const bitrate = BITRATE_JE_AUFLOESUNG[basis] * (optionen.bildrate >= 60 ? 2 : 1)
  return {
    // Eine einzige, volle Ebene. Simulcast würde 1440p60 in kleinere Ebenen
    // zerlegen und damit genau die Schärfe kosten, für die die Auflösung
    // überhaupt eingestellt wurde.
    simulcast: false,
    videoCodec: 'vp8',
    screenShareEncoding: { maxBitrate: bitrate, maxFramerate: optionen.bildrate },
    // Lieber Bildrate verlieren als Auflösung: ein unscharfer Text ist
    // unbrauchbar, ein leicht ruckelnder lesbar.
    degradationPreference: 'maintain-resolution',
  }
}

/**
 * Startet die Freigabe. Öffnet den nativen Auswahldialog des Browsers; der
 * Einstellungsdialog von MSM läuft vorher ab.
 */
export async function starteBildschirmfreigabe(
  room: Room,
  optionen: FreigabeOptionen,
): Promise<void> {
  await room.localParticipant.setScreenShareEnabled(
    true,
    freigabeAufnahmeOptionen(optionen),
    freigabeSendeOptionen(optionen),
  )
}

export async function beendeBildschirmfreigabe(room: Room): Promise<void> {
  await room.localParticipant.setScreenShareEnabled(false)
}

/**
 * Browser lassen Ton erst zu, nachdem jemand geklickt hat. Nach dem ersten
 * Klick im Overlay wird das hier nachgeholt, sonst bliebe der Anruf stumm,
 * obwohl alles verbunden ist.
 *
 * Gibt zurück, ob gespielt werden darf. Scheitert es, zeigt das Overlay einen
 * Knopf — ein Anruf, der ohne Erklärung still bleibt, sieht aus wie ein Defekt.
 */
export async function erlaubeWiedergabe(room: Room): Promise<boolean> {
  try {
    await room.startAudio()
    return room.canPlaybackAudio
  } catch {
    return false
  }
}

/** `u42` → 42. Gibt `null`, wenn die Kennung nicht von MSM stammt. */
export function benutzerIdAusIdentity(identity: string): number | null {
  const treffer = /^u(\d+)$/.exec(identity)
  return treffer ? Number(treffer[1]) : null
}
