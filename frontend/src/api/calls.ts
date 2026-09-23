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

export interface ActiveCallPartner {
  user_id: number
  username: string
  avatar_url?: string | null
}

export interface ActiveCallInfo {
  raum: string
  art: 'direkt' | 'gruppe'
  group_id?: number | null
  mode: 'audio' | 'video'
  device_id?: string | null
  device_type?: string | null
  started_at: number
  partner?: ActiveCallPartner | null
}

export interface ActiveCallResponse {
  has_active_call: boolean
  call: ActiveCallInfo | null
}

/**
 * Ein laufender Gruppenanruf — ohne zu sagen, wie die Gruppe heisst.
 *
 * `group_name` und `avatar_url` standen hier bis Stufe 6c und kamen aus
 * `chat_groups.name`/`avatar_url`. Die Spalten sind entfernt; den Namen holt
 * der Anzeigende aus seinem versiegelten Namensspeicher
 * ([[gruppenName.ts]]) ueber die `group_id`.
 */
export interface PendingGroupCallInfo {
  group_id: number
  room_token: string
  participant_count: number
}

export interface PendingCallInfo {
  signaling_token: string
  caller_id: number
  caller_username: string
  caller_avatar_url?: string | null
  mode: 'audio' | 'video'
  expires_in: number
}

export interface PendingCallResponse {
  has_pending_call: boolean
  call: PendingCallInfo | null
  group_calls: PendingGroupCallInfo[]
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
  options?: {
    device_id?: string
    device_type?: string
    mode?: 'audio' | 'video'
  },
): Promise<AnrufZugang> {
  return api<AnrufZugang>('/social/calls/token', {
    method: 'POST',
    body: JSON.stringify({
      art,
      raum,
      group_id: gruppenId ?? null,
      device_id: options?.device_id ?? null,
      device_type: options?.device_type ?? null,
      mode: options?.mode ?? 'audio',
    }),
  })
}

// ── Aktiver Anruf & Cross-Device Handoff ────────────────────────────────────

export async function holeAktivenAnruf(): Promise<ActiveCallResponse> {
  return api<ActiveCallResponse>('/social/calls/active')
}

export async function holeAusstehendeAnrufe(): Promise<PendingCallResponse> {
  return api<PendingCallResponse>('/social/calls/pending')
}

export async function verlasseAnruf(raum?: string, deviceId?: string): Promise<void> {
  await api('/social/calls/leave', {
    method: 'POST',
    body: JSON.stringify({ raum: raum ?? null, device_id: deviceId ?? null }),
  })
}

export async function beendeAktivenAnrufRemote(): Promise<void> {
  await api('/social/calls/active/terminate', { method: 'POST' })
}

export async function sendeAnrufHeartbeat(deviceId?: string, raum?: string): Promise<void> {
  await api('/social/calls/heartbeat', {
    method: 'POST',
    body: JSON.stringify({ device_id: deviceId ?? null, raum: raum ?? null }),
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
