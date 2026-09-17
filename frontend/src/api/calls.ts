/**
 * Anruf-Endpunkte. Getrennt von `social.ts`, weil dort schon Freundschaften,
 * Umschläge, Gruppen und Stories liegen.
 */

import { api } from './client'

export interface AnrufEinladung {
  signaling_token: string
  recipient_id: number
  expires_in: number
}

export interface AnrufZugang {
  url: string
  token: string
  raum: string
  identity: string
  ttl: number
}

export interface GruppenRaum {
  room_token: string
  group_id: number
  max_peers: number
}

export interface LivekitStatus {
  modus: 'lokal' | 'extern'
  url: string
  konfiguriert: boolean
  erreichbar: boolean
  fehler?: string | null
  api_key_maskiert: string
  raeume_aktiv: number
}

export interface LivekitTestErgebnis {
  erreichbar: boolean
  meldung: string
  raeume_aktiv: number
}

// ── Zweiergespräche ─────────────────────────────────────────────────────────

export async function ladeZuAnrufEin(
  zielBenutzerId: number,
  modus: 'audio' | 'video',
): Promise<AnrufEinladung> {
  return api<AnrufEinladung>(`/social/calls/invite/${zielBenutzerId}?mode=${modus}`, {
    method: 'POST',
  })
}

/** Holt jemanden in ein bereits laufendes Gespräch. */
export async function holeInAnruf(
  raum: string,
  zielBenutzerId: number,
  modus: 'audio' | 'video',
): Promise<AnrufEinladung> {
  return api<AnrufEinladung>(
    `/social/calls/${encodeURIComponent(raum)}/invite/${zielBenutzerId}?mode=${modus}`,
    { method: 'POST' },
  )
}

export async function lehneAnrufAb(raum: string): Promise<void> {
  await api(`/social/calls/${encodeURIComponent(raum)}/reject`, { method: 'POST' })
}

export async function brichAnrufAb(raum: string): Promise<void> {
  await api(`/social/calls/${encodeURIComponent(raum)}/cancel`, { method: 'POST' })
}

// ── Zugang zum Medienserver ─────────────────────────────────────────────────

export async function holeZugang(
  art: 'direkt' | 'gruppe',
  raum: string,
  gruppenId?: number,
): Promise<AnrufZugang> {
  return api<AnrufZugang>('/social/calls/token', {
    method: 'POST',
    body: JSON.stringify({ art, raum, group_id: gruppenId ?? null }),
  })
}

/** Reicht den verpackten Raumschlüssel an einen Teilnehmer weiter. */
export async function sendeRaumSchluessel(
  raum: string,
  zielBenutzerId: number,
  ciphertext: string,
): Promise<void> {
  await api(`/social/calls/${encodeURIComponent(raum)}/key`, {
    method: 'POST',
    body: JSON.stringify({ target_user_id: zielBenutzerId, ciphertext }),
  })
}

export async function holeTeilnehmerzahl(raum: string): Promise<number> {
  const antwort = await api<{ raum: string; teilnehmer: number }>(
    `/social/calls/${encodeURIComponent(raum)}/teilnehmer`,
  )
  return antwort.teilnehmer
}

// ── Moderation im Anruf ─────────────────────────────────────────────────────

/**
 * Nimmt einem Teilnehmer das Mikrofon oder gibt es zurück.
 *
 * Der Entzug wirkt am Medienserver, nicht in dessen Browser: der Betroffene
 * kann sich nicht selbst wieder freischalten. Kamera und Bildschirmfreigabe
 * bleiben unberührt.
 */
export async function setzeServerStumm(
  raum: string,
  zielBenutzerId: number,
  stumm: boolean,
): Promise<void> {
  await api(
    `/social/calls/${encodeURIComponent(raum)}/teilnehmer/${zielBenutzerId}/stumm`,
    { method: 'POST', body: JSON.stringify({ stumm }) },
  )
}

/** Wirft jemanden aus dem Anruf. Wer beitreten darf, kann danach wiederkommen. */
export async function entferneAusAnruf(raum: string, zielBenutzerId: number): Promise<void> {
  await api(
    `/social/calls/${encodeURIComponent(raum)}/teilnehmer/${zielBenutzerId}/entfernen`,
    { method: 'POST' },
  )
}

// ── Gruppenanrufe ───────────────────────────────────────────────────────────

export async function starteGruppenanruf(gruppenId: number): Promise<GruppenRaum> {
  return api<GruppenRaum>(`/social/calls/groups/${gruppenId}/start`, { method: 'POST' })
}

export async function beendeGruppenanruf(gruppenId: number, raum: string): Promise<void> {
  await api(`/social/calls/groups/${gruppenId}/end`, {
    method: 'POST',
    body: JSON.stringify({ room_token: raum }),
  })
}

// ── Betreiber-Einstellungen ─────────────────────────────────────────────────

export async function holeLivekitStatus(): Promise<LivekitStatus> {
  return api<LivekitStatus>('/admin/messenger/livekit/status')
}

export async function speichereLivekitKonfiguration(payload: {
  modus: 'lokal' | 'extern'
  url?: string
  api_key?: string
  api_secret?: string
}): Promise<LivekitStatus> {
  return api<LivekitStatus>('/admin/messenger/livekit/config', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

/** Leere Felder meinen den gespeicherten Stand; die Oberfläche zeigt ihn nur maskiert. */
export async function testeLivekitVerbindung(payload: {
  url: string
  api_key?: string
  api_secret?: string
}): Promise<LivekitTestErgebnis> {
  return api<LivekitTestErgebnis>('/admin/messenger/livekit/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
