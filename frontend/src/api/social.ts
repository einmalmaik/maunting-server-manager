/**
 * Social, Friends, Achievements & E2EE API Client
 */

import { api, apiStream } from './client'
import { nachweisKopf } from '@/services/mailboxNachweis'
import { eigenerPushAbdruck } from '@/services/mailboxPush'
import i18n from '@/i18n'

export interface FriendItem {
  id: number
  user_id: number
  username: string
  avatar_url?: string | null
  status: 'pending' | 'accepted' | 'blocked'
  is_requester: boolean
  presence?: {
    status: 'online' | 'away' | 'invisible'
    device_type: 'web' | 'desktop' | 'mobile'
    custom_status?: string | null
    activity_label?: string | null
    activity_detail?: string | null
    last_seen_at?: string | null
    updated_at?: string | null
  } | null
  created_at: string
}

export interface PresenceItem {
  user_id: number
  username: string
  status: 'online' | 'away' | 'invisible'
  device_type: 'web' | 'desktop' | 'mobile'
  custom_status?: string | null
  activity_label?: string | null
  activity_detail?: string | null
  last_seen_at?: string | null
  updated_at?: string | null
}

export interface PresenceUpdatePayload {
  status?: 'online' | 'away' | 'invisible'
  device_type?: 'web' | 'desktop' | 'mobile'
  custom_status?: string | null
  activity_label?: string | null
  activity_detail?: string | null
}

export interface AchievementItem {
  id: string
  title: string
  description: string
  category: string
  points: number
  icon: string
  unlocked: boolean
  unlocked_at?: string | null
  global_unlocked_percentage?: number
  rarity_percent?: number
  rarity_tier?: string
  rarity_text?: string
  unlocked_count?: number
}

export interface AchievementsOverview {
  achievements: AchievementItem[]
  total_unlocked: number
  total_available: number
  prestige_score: number
}

export interface UserStatsResponse {
  user_id?: number
  username?: string
  total_achievements: number
  unlocked_achievements: number
  total_points: number
  earned_points: number
  active_time_seconds: number
  active_time_by_category: Record<string, number>
  // Aliases for component ergonomics
  achievements_unlocked: number
  total_activity_seconds: number
  categories: Record<string, number>
  rarity_summary?: Record<string, unknown>
}

export interface PublicProfileResponse {
  user_id: number
  username: string
  avatar_url?: string | null
  social_privacy: 'private' | 'friends' | 'public'
  is_friend?: boolean
  presence?: PresenceItem | null
  achievements?: AchievementItem[] | null
  stats?: UserStatsResponse | null
  restricted?: boolean
}

export interface BlindEnvelopeItem {
  id: number
  blind_mailbox_id: string
  ciphertext_envelope: string
  client_uuid?: string | null
  created_at: string
}

export async function getFriends(): Promise<FriendItem[]> {
  return api<FriendItem[]>('/social/friends')
}

export async function sendFriendRequest(targetUsername: string): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>('/social/friends/request', {
    method: 'POST',
    body: JSON.stringify({ username: targetUsername, target_username: targetUsername }),
  })
}

export async function acceptFriendRequest(requestId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/friends/${requestId}/accept`, {
    method: 'POST',
  })
}

export async function declineFriendRequest(requestId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/friends/${requestId}/decline`, {
    method: 'POST',
  })
}

export async function removeFriend(friendUserId: number): Promise<{ success: boolean }> {
  return api<{ success: boolean }>(`/social/friends/${friendUserId}`, {
    method: 'DELETE',
  })
}

export async function getBlockedUsers(): Promise<FriendItem[]> {
  return api<FriendItem[]>('/social/friends/blocked')
}

export async function blockUserApi(targetUserId: number): Promise<{ ok: boolean; message: string }> {
  return api<{ ok: boolean; message: string }>(`/social/friends/${targetUserId}/block`, {
    method: 'POST',
  })
}

export async function unblockUserApi(targetUserId: number): Promise<{ ok: boolean; message: string }> {
  return api<{ ok: boolean; message: string }>(`/social/friends/${targetUserId}/unblock`, {
    method: 'POST',
  })
}

export async function getPresence(): Promise<PresenceItem[]> {
  return api<PresenceItem[]>('/social/presence')
}

export async function updatePresence(payload: PresenceUpdatePayload): Promise<PresenceItem> {
  return api<PresenceItem>('/social/presence', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getAchievements(): Promise<AchievementsOverview> {
  return api<AchievementsOverview>('/social/achievements')
}

export async function getStats(): Promise<UserStatsResponse> {
  return api<UserStatsResponse>('/social/stats')
}

export interface FriendRequestsResponse {
  incoming: FriendItem[]
  outgoing: FriendItem[]
}

export async function getFriendRequests(): Promise<FriendRequestsResponse> {
  return api<FriendRequestsResponse>('/social/friends/requests')
}

export async function getProfile(userId: number): Promise<PublicProfileResponse> {
  return api<PublicProfileResponse>(`/social/profile/user/${userId}`)
}

export async function getPublicProfiles(search?: string): Promise<PublicProfileResponse[]> {
  const q = search && search.trim() ? `?search=${encodeURIComponent(search.trim())}` : ''
  return api<PublicProfileResponse[]>(`/social/profiles/public${q}`)
}

export async function updatePrivacy(payload: {
  privacy?: 'private' | 'friends' | 'public'
  social_privacy?: 'private' | 'friends' | 'public'
}): Promise<{ success: boolean; social_privacy?: string }> {
  const privacyVal = payload.privacy ?? payload.social_privacy ?? 'friends'
  return api<{ success: boolean; social_privacy?: string }>('/social/privacy', {
    method: 'PATCH',
    body: JSON.stringify({ privacy: privacyVal }),
  })
}

export interface E2eeGeraetItem {
  device_id: string
  public_key: string
  /**
   * ECDSA P-256, beglaubigt den Absender einer Gruppennachricht. Leer heißt:
   * dieses Gerät war seit der Umstellung nicht an. Siehe `absenderSignatur.ts`.
   */
  signing_public_key: string
  label: string
}

/**
 * Veroeffentlicht den Schluessel *dieses* Geraets.
 *
 * Keine Benutzerkennung im Rumpf: sie kommt aus der Sitzung. Ein Geraet kann
 * damit ausschliesslich seinen eigenen Eintrag schreiben.
 */
export async function putEigenesGeraet(payload: {
  deviceId: string
  publicKey: string
  signingPublicKey?: string
  label?: string
}): Promise<E2eeGeraetItem> {
  return api<E2eeGeraetItem>('/social/e2ee/devices/self', {
    method: 'PUT',
    body: JSON.stringify({
      device_id: payload.deviceId,
      public_key: payload.publicKey,
      signing_public_key: payload.signingPublicKey ?? '',
      label: payload.label ?? '',
    }),
  })
}

/** Die Zustelladressen eines Kontos — je Geraet eine. */
export async function getE2eeGeraete(userId: number): Promise<E2eeGeraetItem[]> {
  return api<E2eeGeraetItem[]>(`/social/e2ee/devices/${userId}`)
}

export async function deleteEigenesGeraet(deviceId: string): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(
    `/social/e2ee/devices/self?device_id=${encodeURIComponent(deviceId)}`,
    { method: 'DELETE' }
  )
}

/** Der `applicationServerKey`. Leer heißt: dieses Panel kann nicht zustellen. */
export async function getPushPublicKey(): Promise<string> {
  const antwort = await api<{ key: string }>('/social/push/public-key')
  return antwort.key || ''
}

export async function meldePushAbo(abo: {
  endpoint: string
  p256dh: string
  auth: string
}): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>('/social/push/subscribe', {
    method: 'POST',
    body: JSON.stringify(abo),
  })
}

export async function loeschePushAbo(endpoint: string): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(
    `/social/push/subscribe?endpoint=${encodeURIComponent(endpoint)}`,
    { method: 'DELETE' }
  )
}

export interface ChatGroupMemberItem {
  user_id: number
  username: string
  avatar_url?: string | null
  role: string
  permissions?: string | null
  /**
   * Ob **dieses Mitglied** alle wecken, anheften beziehungsweise die
   * Verfallsfrist der Gruppe stellen darf.
   *
   * Vom Server ausgerechnet, und zwar je Mitglied, nicht nur für mich: der
   * Server kann den Inhalt einer Nachricht nicht lesen, also entscheidet das
   * empfangende Gerät — und das braucht dafür die Rechtelage des *Absenders*.
   * Fehlt das Feld, gilt nein.
   */
  can_mention_everyone?: boolean
  can_pin_messages?: boolean
  can_set_disappearing_messages?: boolean
  joined_at: string
}

export interface ChatGroupItem {
  id: number
  /**
   * `null`, solange dieses Gerät den Namen nicht kennt.
   *
   * Der Server liefert hier seit Stufe 6 **immer** `null` — er kennt den Namen
   * einer Gruppe nicht mehr. Gefüllt wird das Feld im Client aus dem
   * versiegelten Namensspeicher und dem verschlüsselten Gruppenblock
   * (`gruppenName.ts`). Dass der Typ das zulässt, ist Absicht: ein
   * `name: string` wäre eine Zusage, die niemand mehr einhält, und jede
   * Anzeige führe blind auf einen leeren String.
   */
  name: string | null
  description?: string | null
  avatar_url?: string | null
  /**
   * `null`, wenn dieses Mitglied nicht einladen darf.
   *
   * Das Backend lässt den Code dann ganz weg — und das ist die einzige
   * Durchsetzung, die `invite_members` haben kann: wer den Code hat, kommt
   * rein. Bis 09/2026 ging er bei jedem Abruf an jedes Mitglied.
   */
  invite_code: string | null
  owner_user_id: number
  member_count: number
  role: string
  default_permissions?: string | null
  /** Vom Backend entschieden. Was hier false ist, endet dort in einem 403. */
  can_start_call?: boolean
  can_join_call?: boolean
  can_share_screen?: boolean
  can_mute_others?: boolean
  can_kick_from_call?: boolean
  /** Ob ich die Auswahl angeboten bekomme. Die Schranke sitzt beim Empfänger. */
  can_mention_everyone?: boolean
  can_pin_messages?: boolean
  can_set_disappearing_messages?: boolean
  created_at: string
  members: ChatGroupMemberItem[]
  /** Ephemeral room token supplied by a live-call invitation, when present. */
  room_token?: string | null
}

export interface ChatGroupInvitePublic {
  group_id: number
  /**
   * Klartext — und nur, solange die Gruppe keine verschlüsselte Karte hat.
   *
   * Sobald sie eine hat, liefert der Server hier `null`, und die Vorschau kommt
   * aus `invite_card` plus dem Schlüssel hinter der Raute im Link. Beides
   * nebeneinander wäre Verschlüsselung als Zierde.
   */
  name?: string | null
  description?: string | null
  avatar_url?: string | null
  /** `sv-einladung-v1:…` — Name, Beschreibung und Logo, verschlüsselt. */
  invite_card?: string | null
  member_count: number
  /** Läuft gerade ein Gruppenanruf? Für die Vorschaukarte im Chat. */
  live_call: boolean
  live_participants: number
}

export interface DirectChatItem {
  id: number
  other_user_id: number
  other_username: string
  other_avatar_url?: string | null
  blind_mailbox_id: string
  is_friend: boolean
  is_blocked?: boolean
  other_privacy: 'private' | 'friends' | 'public'
  presence?: PresenceItem | null
  created_at: string
  updated_at: string
}

/*
 * `getDirectChats()` rief bis Stufe 6b `GET /social/direct-chats`.
 *
 * Diese Route ist entfernt. Sie beantwortete „mit wem schreibt dieses Konto?",
 * und um das zu können, musste der Server es aufschreiben — in
 * `direct_chats.user_a_id`/`user_b_id`. Die Liste führt jetzt der Client:
 * `gespraechsListe()` aus `services/gespraechsListe.ts`, versiegelt im
 * örtlichen Speicher.
 */

// Anrufe liegen in `api/calls.ts`: Einladungen, Zugangstoken für den
// Medienserver, Raumschlüssel und die Betreiber-Einstellungen.

export async function checkCanMessage(targetUserId: number): Promise<{
  can_message: boolean
  reason?: string | null
  blind_mailbox_id?: string | null
}> {
  return api<{ can_message: boolean; reason?: string | null; blind_mailbox_id?: string | null }>(
    `/social/chat/can-message/${targetUserId}`
  )
}

export async function startDirectChat(targetUserId: number): Promise<DirectChatItem> {
  return api<DirectChatItem>(`/social/chat/start/${targetUserId}`, {
    method: 'POST',
  })
}

/**
 * Hinterlegt den blinden Besitznachweis einer Mailbox.
 *
 * Der authentifizierte Übergang: danach verlangt der Server für diese Mailbox
 * bei jedem Zugriff den Nachweis — zusätzlich zur bisherigen Prüfung, nicht an
 * ihrer Stelle.
 */
export async function registriereMailbox(mailboxId: string, authToken: string): Promise<void> {
  await api<{ ok: boolean }>('/social/e2ee/mailbox/register', {
    method: 'POST',
    body: JSON.stringify({ mailbox_id: mailboxId, auth_token: authToken }),
  })
}

/**
 * Ein blinder Umschlag — ohne Empfängerkennung.
 *
 * Bis 09/2026 reiste ein `recipient_id` mit. Für eine Mailbox, die der Server
 * ohnehin ausrechnen kann, verriet es nichts Neues; für eine aus einem
 * Geheimnis verriet es alles. Der Server nimmt es nicht mehr entgegen, und
 * dieses Feld gibt es hier deshalb gar nicht mehr — ein durchgereichtes
 * `recipient_id` wäre stiller Ballast, der irgendwann wieder jemand liest.
 */
export async function relayE2eeEnvelope(payload: {
  blind_mailbox_id: string
  ciphertext_envelope: string
  client_uuid?: string | null
  is_control?: boolean
  control_type?: string | null

}): Promise<BlindEnvelopeItem> {
  // Der eigene Push-Abdruck hängt an jedem Umschlag und hält diesen Browser aus
  // der Zustellung heraus. Auf dem kontogebundenen Weg braucht es ihn nicht —
  // dort erkennt der Server den Absender an seiner Kennung. Auf dem
  // Mailbox-Weg gibt es keine Kennung mehr, und ohne ihn bekäme man die
  // Meldung über die eigene Nachricht.
  const abdruck = eigenerPushAbdruck()
  return api<BlindEnvelopeItem>('/social/e2ee/relay', {
    method: 'POST',
    body: JSON.stringify(abdruck ? { ...payload, push_ausnahme: abdruck } : payload),
    headers: nachweisKopf(payload.blind_mailbox_id),
  })
}

export interface ChatMediaItem {
  id: string
  blind_mailbox_id: string
  file_name: string
  media_type: string
  size_bytes: number
  sha256: string
  created_at: string
}

export async function uploadChatMedia(payload: {
  blind_mailbox_id: string
  ciphertext_blob: string
  file_name: string
  media_type?: string
  group_id?: number | null
}): Promise<ChatMediaItem> {
  return api<ChatMediaItem>('/social/media/upload', {
    method: 'POST',
    body: JSON.stringify(payload),
    headers: nachweisKopf(payload.blind_mailbox_id),
  })
}

export async function getChatMediaSignedUrl(
  mediaId: string,
  ttl: number = 900
): Promise<{ media_id: string; signed_url: string; expires_at: string }> {
  return api<{ media_id: string; signed_url: string; expires_at: string }>(
    `/social/media/${mediaId}/signed-url?ttl=${ttl}`
  )
}

/**
 * Löscht einen hochgeladenen Anhang endgültig. Nur der Absender darf das.
 *
 * Ein Anhang liegt nicht im Umschlag, sondern als eigener Blob daneben. Ohne
 * diesen Aufruf wäre die Nachricht gelöscht und das Bild weiter abrufbar.
 */
export async function loescheChatMedium(
  mediaId: string
): Promise<{ ok: boolean; deleted: boolean }> {
  return api<{ ok: boolean; deleted: boolean }>(
    `/social/media/${encodeURIComponent(mediaId)}`,
    { method: 'DELETE' }
  )
}

/**
 * Nimmt die Umschläge einer gelöschten Nachricht aus der blinden Mailbox.
 *
 * Adressiert wird über die logische Nachrichtenkennung: dieselbe Nachricht
 * liegt dort als eine Kopie je Zielgerät (`<kennung>#<geraet>`).
 */
export async function loescheBlindeUmschlaege(
  blindMailboxId: string,
  clientUuid: string
): Promise<{ ok: boolean; deleted: number }> {
  return api<{ ok: boolean; deleted: number }>(
    `/social/e2ee/envelopes/${encodeURIComponent(blindMailboxId)}?client_uuid=${encodeURIComponent(clientUuid)}`,
    { method: 'DELETE', headers: nachweisKopf(blindMailboxId) }
  )
}

export async function downloadChatMedia(signedUrl: string): Promise<string> {
  const res = await apiStream(signedUrl, {
    method: 'GET',
    headers: { Accept: '*/*' },
  })
  if (!res.ok) {
    throw new Error(i18n.t('chat.errors.mediaDownloadFailed', { status: res.status }))
  }
  return await res.text()
}

/**
 * Verschlüsselt einen Anhang auf diesem Gerät und lädt ihn hoch.
 *
 * Zurück kommt, was der Empfänger braucht, um wieder an ihn heranzukommen:
 * Medienkennung, Paketschlüssel und Anhangkennung. Die drei reisen im
 * Nachrichten-Payload, also innerhalb des Double Ratchets beziehungsweise des
 * Gruppenschlüssels — nie am Server vorbei und nie an ihm vorbeischaubar.
 *
 * Dateiname und Dateityp gehen bewusst **nicht** mit. Der Server speichert
 * beides im Klartext neben dem Blob, und ein Umschlag, den niemand öffnen kann,
 * nützt wenig, wenn daneben „Gehaltsabrechnung.pdf" steht. Gebraucht wird es
 * dort auch nicht: ausgeliefert wird ohnehin als `application/octet-stream`.
 * Was die Anzeige braucht, steht im versiegelten Manifest und im
 * verschlüsselten Nachrichten-Payload.
 */
export async function ladeAnhangHoch(eingabe: {
  klartext: string
  dateiname: string
  mimeType: string
  blindMailboxId: string
  absenderId: number
  groupId?: number | null
}): Promise<import('@/services/medienKrypto').MedienZeiger> {
  const { neueFileId, verschluesselePaket } = await import('@/services/medienKrypto')
  const fileId = neueFileId()
  const { blob, paketSchluessel } = await verschluesselePaket(
    eingabe.klartext,
    {
      absenderId: eingabe.absenderId,
      blindMailboxId: eingabe.blindMailboxId,
      fileId,
    },
    { name: eingabe.dateiname, mimeType: eingabe.mimeType || null },
  )
  const hochgeladen = await uploadChatMedia({
    blind_mailbox_id: eingabe.blindMailboxId,
    ciphertext_blob: blob,
    file_name: 'anhang.bin',
    media_type: 'application/octet-stream',
    group_id: eingabe.groupId,
  })
  return { mediaId: hochgeladen.id, paketSchluessel, fileId }
}

/** Holt einen Anhang über seine signierte URL und öffnet ihn auf diesem Gerät. */
export async function ladeAnhangHerunter(
  zeiger: import('@/services/medienKrypto').MedienZeiger,
  bindung: { absenderId: number; blindMailboxId: string }
): Promise<string> {
  const { entschluesselePaket } = await import('@/services/medienKrypto')
  const { signed_url } = await getChatMediaSignedUrl(zeiger.mediaId)
  const blob = await downloadChatMedia(signed_url)
  return entschluesselePaket(blob, zeiger.paketSchluessel, {
    absenderId: bindung.absenderId,
    blindMailboxId: bindung.blindMailboxId,
    fileId: zeiger.fileId,
  })
}

export interface E2eeMailboxSyncItem {
  blind_mailbox_id: string
  max_envelope_id: number
  unread_count: number
}

export interface E2eeMailboxSyncResponse {
  mailboxes: E2eeMailboxSyncItem[]
}

export async function syncE2eeMailboxes(sinceId = 0): Promise<E2eeMailboxSyncResponse> {
  const query = sinceId ? `?since_id=${sinceId}` : ''
  return api<E2eeMailboxSyncResponse>(`/social/e2ee/sync${query}`)
}

export async function fetchE2eeEnvelopes(
  blindMailboxId: string,
  sinceId?: number
): Promise<BlindEnvelopeItem[]> {
  const query = sinceId ? `?since_id=${sinceId}` : ''
  return api<BlindEnvelopeItem[]>(`/social/e2ee/mailbox/${blindMailboxId}${query}`, {
    headers: nachweisKopf(blindMailboxId),
  })
}

export async function sendTypingSignal(payload: {
  blind_mailbox_id: string
  status: 'typing' | 'recording' | 'idle'
}): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>('/social/e2ee/typing', {
    method: 'POST',
    body: JSON.stringify(payload),
    headers: nachweisKopf(payload.blind_mailbox_id),
  })
}

export async function getGroups(): Promise<ChatGroupItem[]> {
  return api<ChatGroupItem[]>('/social/groups')
}

/**
 * Legt eine Gruppe an — ohne ihr einen Namen mitzugeben.
 *
 * Seit Stufe 6 hat der Aufruf keine Nutzlast mehr: Name, Beschreibung und Logo
 * gehen den Server nichts an. Er vergibt eine Kennung und einen Einladungscode,
 * alles Weitere schreibt der Client anschliessend in den verschlüsselten
 * Gruppenblock (`sichereGruppenAnsicht`).
 */
export async function createGroup(): Promise<ChatGroupItem> {
  return api<ChatGroupItem>('/social/groups', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function getGroupInviteInfo(inviteCode: string): Promise<ChatGroupInvitePublic> {
  return api<ChatGroupInvitePublic>(`/social/groups/invite/${inviteCode}`)
}

/**
 * Hinterlegt die verschlüsselte Einladungskarte einer Gruppe.
 *
 * `null` nimmt sie zurück. Gerufen wird das beim Bauen eines Links — die Karte
 * entsteht genau dann, wenn jemand einen teilt, und trägt denselben Stand wie
 * er.
 */
export async function setzeEinladungsKarte(
  groupId: number,
  karte: string | null,
): Promise<void> {
  await api(`/social/groups/${groupId}/invite-card`, {
    method: 'PUT',
    body: JSON.stringify({ invite_card: karte }),
  })
}

export async function joinGroupByInvite(inviteCode: string): Promise<ChatGroupItem> {
  return api<ChatGroupItem>(`/social/groups/join/${inviteCode}`, {
    method: 'POST',
  })
}

export async function leaveGroup(groupId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}/leave`, {
    method: 'POST',
  })
}

export async function deleteGroup(groupId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}`, {
    method: 'DELETE',
  })
}

export async function getGroupMembers(groupId: number): Promise<ChatGroupMemberItem[]> {
  return api<ChatGroupMemberItem[]>(`/social/groups/${groupId}/members`)
}

export async function updateGroupMemberRole(
  groupId: number,
  targetUserId: number,
  role: 'admin' | 'moderator' | 'member',
  permissions?: string
): Promise<ChatGroupMemberItem> {
  return api<ChatGroupMemberItem>(`/social/groups/${groupId}/members/${targetUserId}`, {
    method: 'PATCH',
    body: JSON.stringify({ role, permissions: permissions || null }),
  })
}

export async function kickGroupMember(
  groupId: number,
  targetUserId: number
): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}/members/${targetUserId}`, {
    method: 'DELETE',
  })
}

export async function updateGroupPermissions(
  groupId: number,
  defaultPermissions: string
): Promise<ChatGroupItem> {
  return api<ChatGroupItem>(`/social/groups/${groupId}/permissions`, {
    method: 'PATCH',
    body: JSON.stringify({ default_permissions: defaultPermissions }),
  })
}

/**
 * Der verschlüsselte Gruppenzustand: die eigenen Rollen dieser Gruppe.
 *
 * `blob` ist ein Umschlag unter dem Gruppenschlüssel — das Backend reicht ihn
 * durch und liest ihn nie. Ausgewertet wird er in `services/gruppenKonfig.ts`;
 * hier steht nur der Transport.
 */
export interface GruppenKonfigAntwort {
  group_id: number
  blob: string
  revision: number
  updated_at: string
}

/** `null`, solange die Gruppe noch keinen Zustand hat — dann ist Revision 0. */
export async function getGroupConfig(groupId: number): Promise<GruppenKonfigAntwort | null> {
  return api<GruppenKonfigAntwort | null>(`/social/groups/${groupId}/config`)
}

/**
 * Schreibt den nächsten Stand. `erwarteteRevision` ist der Stand, den dieses
 * Gerät gelesen hat; kam ein anderes dazwischen, antwortet das Backend mit 409
 * und es wird nichts überschrieben.
 */
export async function putGroupConfig(
  groupId: number,
  blob: string,
  erwarteteRevision: number,
): Promise<GruppenKonfigAntwort> {
  return api<GruppenKonfigAntwort>(`/social/groups/${groupId}/config`, {
    method: 'PUT',
    body: JSON.stringify({ blob, erwartete_revision: erwarteteRevision }),
  })
}

/*
 * `uploadGroupAvatar` und `deleteGroupAvatar` gibt es seit Stufe 6 nicht mehr.
 *
 * Ein Gruppenlogo auf der Platte des Servers ist eine Datei, die unter einer
 * rate-URL jedem offensteht, und ein Bild sagt über eine Gruppe oft mehr als
 * ihr Name. Das Logo lebt jetzt als Data-URL im verschlüsselten Gruppenblock;
 * gesetzt wird es über `sichereGruppenAnsicht` in `services/gruppenName.ts`.
 * Die Routen `POST/DELETE /social/groups/{id}/avatar` sind entfernt.
 */

export interface ChatStoryItem {
  id: number
  user_id: number
  username: string
  avatar_url?: string | null
  content: string
  media_url?: string | null
  background: string
  created_at: string
  expires_at: string
  is_self: boolean
}

export async function getStories(): Promise<ChatStoryItem[]> {
  return api<ChatStoryItem[]>('/social/stories')
}

export async function createStory(payload: {
  content: string
  media_url?: string
  background?: string
}): Promise<ChatStoryItem> {
  return api<ChatStoryItem>('/social/stories', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteStory(storyId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/stories/${storyId}`, {
    method: 'DELETE',
  })
}

export async function recordActivityTime(category: string, seconds: number): Promise<{ success: boolean }> {
  return api<{ success: boolean }>('/social/activity/ping', {
    method: 'POST',
    body: JSON.stringify({ category, seconds }),
  })
}
