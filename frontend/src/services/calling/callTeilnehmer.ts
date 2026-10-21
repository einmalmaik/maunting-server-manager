/**
 * LiveKit-Teilnehmer in Anzeigezustaende uebersetzen.
 *
 * Die Kennung kommt nur aus der signierten Identitaet. Metadaten, die einen
 * anderen Benutzer behaupten, zaehlen nicht - sonst truege eine gefaelschte
 * Kachel Name und Bild eines Mitglieds.
 */

import { benutzerIdAusIdentity } from '@/services/livekitGrundlagen'
import { useAuthStore } from '@/stores/authStore'
import { lk } from './callZugriff'
import type { CallParticipant, CallScreenShare } from './callZustand'
import type { Participant, Room, Track } from 'livekit-client'


// ── Hilfen zum Übersetzen von LiveKit-Zustand in Store-Zustand ──────────────

function anzeigename(teilnehmer: Participant): string {
  return teilnehmer.name || teilnehmer.identity
}

/** Der Name, den eine Meldung nennen soll — aus den Stammdaten, sonst aus LiveKit. */
export function nameVon(teilnehmer: Participant, bekannte: Bekannte): string {
  const userId = benutzerIdAusIdentity(teilnehmer.identity)
  const stammdaten = userId === null ? undefined : bekannte.get(userId)
  return stammdaten?.username || anzeigename(teilnehmer)
}

function videospur(teilnehmer: Participant): Track | null {
  const veroeffentlichung = teilnehmer.getTrackPublication(lk().Track.Source.Camera)
  return veroeffentlichung?.track ?? null
}

function kameraAus(teilnehmer: Participant): boolean {
  const veroeffentlichung = teilnehmer.getTrackPublication(lk().Track.Source.Camera)
  return !veroeffentlichung || veroeffentlichung.isMuted || !veroeffentlichung.track
}

function mikrofonAus(teilnehmer: Participant): boolean {
  const veroeffentlichung = teilnehmer.getTrackPublication(lk().Track.Source.Microphone)
  return !veroeffentlichung || veroeffentlichung.isMuted
}

/** Wie lange eine Beitritts- oder Abgangsmeldung im Anruffenster stehen bleibt. */
export const HINWEIS_DAUER_MS = 3000

/**
 * Darf dieser Teilnehmer diese Quelle senden?
 *
 * LiveKit führt die erlaubten Quellen je Teilnehmer. Nimmt ein Moderator das
 * Mikrofon aus der Liste, verweigert der Server das Senden — der Betroffene
 * kann sich dann nicht selbst wieder freischalten. Eine leere oder fehlende
 * Liste heißt „alles erlaubt", nicht „nichts erlaubt".
 */
export function darfSenden(teilnehmer: Participant, quelle: Track.Source): boolean {
  const erlaubt = teilnehmer.permissions?.canPublishSources
  if (!erlaubt || erlaubt.length === 0) return true
  return erlaubt.includes(quellenNummer(quelle))
}

/** Die Nummern aus LiveKits `TrackSource`-Aufzählung. */
function quellenNummer(quelle: Track.Source): number {
  switch (quelle) {
    case lk().Track.Source.Camera:
      return 1
    case lk().Track.Source.Microphone:
      return 2
    case lk().Track.Source.ScreenShare:
      return 3
    case lk().Track.Source.ScreenShareAudio:
      return 4
    default:
      return 0
  }
}

/**
 * Profilbilder liegen immer auf dem eigenen Server (`/api/auth/avatar/…`).
 * Eine absolute Adresse kann nur von jemandem stammen, der sie untergeschoben
 * hat. Bis 26.09.2026 ging sie durch, und die Desktop-App holte das Bild samt
 * Zugangstoken ab: so kam jeder im Anruf an das Token des anderen.
 */
export function sanitizeAvatarUrl(url: unknown): string | null {
  if (typeof url !== 'string') return null
  const trimmed = url.trim()
  if (!trimmed) return null
  // Safe relative paths (e.g. /media/avatar.png, /avatar/dana.png), not protocol-relative //
  if (trimmed.startsWith('/') && !trimmed.startsWith('//')) {
    return trimmed
  }
  return null
}

export type Bekannte = Map<number, { username: string; avatarUrl?: string | null }>

export function sammleTeilnehmer(room: Room, bekannte: Bekannte, sprechend: Set<string>): CallParticipant[] {
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
        teilnehmer.getTrackPublication(lk().Track.Source.Microphone) === undefined &&
        teilnehmer.getTrackPublication(lk().Track.Source.Camera) === undefined,
      videoTrack: videospur(teilnehmer),
      volume: 1,
    }
  })
}

export function sammleFreigaben(room: Room, bekannte: Bekannte): CallScreenShare[] {
  const freigaben: CallScreenShare[] = []
  const alle: Participant[] = [room.localParticipant, ...room.remoteParticipants.values()]
  for (const teilnehmer of alle) {
    const veroeffentlichung = teilnehmer.getTrackPublication(lk().Track.Source.ScreenShare)
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
